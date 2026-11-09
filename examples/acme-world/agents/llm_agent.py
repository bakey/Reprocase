"""LLM agent: OpenAI-compatible chat completions over the Tool Broker (M8).

stdlib-only (urllib) — no new runtime dependencies. Two model configurations
are read from the environment and selected per run::

    REPROCASE_LLM_PROFILE=primary     # or "secondary" (default: primary)

    primary:   REPROCASE_LLM_BASE_URL / REPROCASE_LLM_API_KEY / REPROCASE_LLM_MODEL
    secondary: REPROCASE_LLM2_BASE_URL / REPROCASE_LLM2_API_KEY / REPROCASE_LLM2_MODEL

Discipline: the API key is used only in the Authorization header and never
placed in messages, tool arguments, emitted events, or the returned result —
the runner additionally scrubs its value from all evidence (defense in depth).
Model non-determinism is expected; verdict determinism comes from graders.
"""

from __future__ import annotations

import asyncio
import json
import os
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any

from reprocase.agents.protocol import AgentResult
from reprocase.results.models import Usage
from reprocase.tools.broker import ToolCallError

SYSTEM_PROMPT = (
    "You are a customer-support agent operating inside a company environment. "
    "Use the provided tools to complete the user's task; verify policy with "
    "tools before acting, prefer the least invasive action, and never invent "
    "entity ids — look them up. When policy blocks the task, stop and explain "
    "instead of working around it."
)

_MAX_RETRIES = 2
_RETRY_SLEEP_SECONDS = (1.0, 2.0)
_REQUEST_TIMEOUT_SECONDS = 60.0


@dataclass(frozen=True)
class LLMConfig:
    base_url: str
    api_key: str
    model: str


def resolve_config(profile: str | None = None) -> LLMConfig:
    """Resolve the model configuration from the environment, failing fast."""

    chosen = (profile or os.environ.get("REPROCASE_LLM_PROFILE") or "primary").lower()
    if chosen not in {"primary", "secondary"}:
        raise RuntimeError(
            f"REPROCASE_LLM_PROFILE must be 'primary' or 'secondary', got {chosen!r}"
        )
    prefix = "REPROCASE_LLM" if chosen == "primary" else "REPROCASE_LLM2"
    missing = [
        var
        for var in (f"{prefix}_BASE_URL", f"{prefix}_API_KEY", f"{prefix}_MODEL")
        if not os.environ.get(var)
    ]
    if missing:
        raise RuntimeError(
            f"LLM agent profile {chosen!r} is not configured; set {', '.join(missing)}"
        )
    return LLMConfig(
        base_url=os.environ[f"{prefix}_BASE_URL"].rstrip("/"),
        api_key=os.environ[f"{prefix}_API_KEY"],
        model=os.environ[f"{prefix}_MODEL"],
    )


def project_tools(definitions: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Project broker tool definitions into OpenAI function-tool format."""

    return [
        {
            "type": "function",
            "function": {
                "name": definition["name"],
                "description": definition["description"],
                "parameters": definition["input_schema"],
            },
        }
        for definition in definitions
    ]


def _chat_request(config: LLMConfig, body: dict[str, Any]) -> dict[str, Any]:
    """POST /chat/completions with linear retry on transient failures."""

    request = urllib.request.Request(
        f"{config.base_url}/chat/completions",
        data=json.dumps(body).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {config.api_key}",
        },
        method="POST",
    )
    last_error: Exception | None = None
    for attempt in range(_MAX_RETRIES + 1):
        if attempt:
            time.sleep(_RETRY_SLEEP_SECONDS[min(attempt - 1, len(_RETRY_SLEEP_SECONDS) - 1)])
        try:
            with urllib.request.urlopen(request, timeout=_REQUEST_TIMEOUT_SECONDS) as response:
                return json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            # 429/5xx are transient; other 4xx are request bugs — fail fast
            if exc.code not in {429, 500, 502, 503, 504} or attempt == _MAX_RETRIES:
                detail = exc.read().decode("utf-8", errors="replace")[:500]
                raise RuntimeError(f"LLM API HTTP {exc.code}: {detail}") from exc
            last_error = exc
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
            if attempt == _MAX_RETRIES:
                raise RuntimeError(f"LLM API unreachable after retries: {exc}") from exc
            last_error = exc
    raise RuntimeError(f"LLM API request failed: {last_error}")


async def run(task: dict[str, Any], tools: Any, context: Any) -> AgentResult:
    config = resolve_config()
    await context.emit(
        {"message": f"LLM agent ready (model {config.model})", "model": config.model}
    )

    context_payload = task.get("context") or {}
    messages: list[dict[str, Any]] = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {
            "role": "user",
            "content": json.dumps(
                {"task": task.get("input", {}), "context": context_payload},
                ensure_ascii=False,
            ),
        },
    ]
    tools_payload = project_tools(list(tools.tool_definitions()))

    model_calls = 0
    input_tokens = 0
    output_tokens = 0
    usage_known = True

    while True:
        response = await asyncio.to_thread(
            _chat_request,
            config,
            {
                "model": config.model,
                "messages": messages,
                "tools": tools_payload,
                "tool_choice": "auto",
            },
        )
        model_calls += 1
        usage = response.get("usage") or {}
        if usage.get("prompt_tokens") is None or usage.get("completion_tokens") is None:
            usage_known = False
        input_tokens += int(usage.get("prompt_tokens") or 0)
        output_tokens += int(usage.get("completion_tokens") or 0)
        await context.emit(
            {
                "usage": {
                    "model": config.model,
                    "model_calls": 1,
                    "input_tokens": usage.get("prompt_tokens"),
                    "output_tokens": usage.get("completion_tokens"),
                }
            }
        )

        choice = (response.get("choices") or [{}])[0]
        message = choice.get("message") or {}
        tool_calls = message.get("tool_calls") or []
        if not tool_calls:
            content = message.get("content")
            return AgentResult(
                output={"message": content},
                summary=f"{model_calls} model call(s), {len(messages)} message(s)",
                usage=Usage(
                    model_calls=model_calls,
                    input_tokens=input_tokens if usage_known else None,
                    output_tokens=output_tokens if usage_known else None,
                ),
                model=config.model,
            )

        messages.append(
            {"role": "assistant", "content": message.get("content"), "tool_calls": tool_calls}
        )
        for call in tool_calls:
            function = call.get("function") or {}
            name = function.get("name") or ""
            try:
                arguments = json.loads(function.get("arguments") or "{}")
                if not isinstance(arguments, dict):
                    raise ValueError("arguments must be a JSON object")
            except ValueError as exc:
                # surface malformed model output as an agent failure, not a crash
                raise RuntimeError(
                    f"model returned invalid tool arguments for {name!r}: {exc}"
                ) from exc
            try:
                result = await tools.call(name, arguments)
            except ToolCallError as exc:
                if str(exc.code) == "AGENT_BUDGET_EXCEEDED":
                    # return gracefully: the runner records the budget failure
                    # as the primary agent failure (precedent: scripted agents)
                    return AgentResult(
                        output={"message": "tool budget exhausted before completion"},
                        summary=f"{model_calls} model call(s), budget exhausted",
                        usage=Usage(
                            model_calls=model_calls,
                            input_tokens=input_tokens if usage_known else None,
                            output_tokens=output_tokens if usage_known else None,
                        ),
                        model=config.model,
                    )
                raise
            messages.append(
                {
                    "role": "tool",
                    "tool_call_id": call.get("id"),
                    "content": json.dumps(result, ensure_ascii=False, default=str),
                }
            )
