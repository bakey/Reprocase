"""Host-level unit tests for the M8 LLM agent (no Docker, no real LLM)."""

from __future__ import annotations

import asyncio
import importlib
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from reprocase.entrypoints import ImportIsolation

ROOT = Path(__file__).resolve().parents[1]
ACME_DIR = ROOT / "examples" / "acme-world"


@pytest.fixture()
def llm(monkeypatch: pytest.MonkeyPatch) -> Any:
    monkeypatch.delenv("REPROCASE_LLM_PROFILE", raising=False)
    with ImportIsolation(ACME_DIR):
        yield importlib.import_module("agents.llm_agent")


def _set_profile(
    monkeypatch: pytest.MonkeyPatch, base_url: str, api_key: str, model: str, second: bool = False
) -> None:
    prefix = "REPROCASE_LLM2" if second else "REPROCASE_LLM"
    monkeypatch.setenv(f"{prefix}_BASE_URL", base_url)
    monkeypatch.setenv(f"{prefix}_API_KEY", api_key)
    monkeypatch.setenv(f"{prefix}_MODEL", model)


class TestResolveConfig:
    def test_primary_and_secondary_profiles(
        self, llm: Any, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _set_profile(monkeypatch, "http://primary.example/v1 ", "pk-1", "model-a")
        _set_profile(monkeypatch, "http://secondary.example/v1", "sk-2", "model-b", second=True)
        assert llm.resolve_config().model == "model-a"
        assert llm.resolve_config("secondary").model == "model-b"
        monkeypatch.setenv("REPROCASE_LLM_PROFILE", "secondary")
        assert llm.resolve_config().base_url == "http://secondary.example/v1"

    def test_missing_config_fails_fast(self, llm: Any, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("REPROCASE_LLM_BASE_URL", raising=False)
        monkeypatch.delenv("REPROCASE_LLM_API_KEY", raising=False)
        monkeypatch.delenv("REPROCASE_LLM_MODEL", raising=False)
        with pytest.raises(RuntimeError, match="not configured"):
            llm.resolve_config()

    def test_unknown_profile_rejected(self, llm: Any, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("REPROCASE_LLM_PROFILE", "third")
        with pytest.raises(RuntimeError, match=r"primary.*secondary"):
            llm.resolve_config()


class TestProjection:
    def test_broker_definitions_project_to_openai_format(self, llm: Any) -> None:
        projected = llm.project_tools(
            [
                {
                    "name": "refund.quote",
                    "description": "Quote refundability.",
                    "input_schema": {
                        "type": "object",
                        "properties": {"order_id": {"type": "string"}},
                    },
                }
            ]
        )
        assert projected == [
            {
                "type": "function",
                "function": {
                    "name": "refund.quote",
                    "description": "Quote refundability.",
                    "parameters": {
                        "type": "object",
                        "properties": {"order_id": {"type": "string"}},
                    },
                },
            }
        ]


class _ScriptedHandler(BaseHTTPRequestHandler):
    """Tiny OpenAI-compatible server: plays a scripted list of responses."""

    server_version = "FakeOpenAI/1.0"

    def do_POST(self) -> None:
        server: _ScriptedServer = self.server  # type: ignore[assignment]
        server.requests.append(json.loads(self.rfile.read(int(self.headers["Content-Length"]))))
        response = (
            server.responses.pop(0)
            if server.responses
            else {"choices": [{"message": {"content": "end"}}]}
        )
        body = json.dumps(response).encode("utf-8")
        self.send_response(server.next_status.pop(0) if server.next_status else 200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args: Any) -> None:
        pass


class _ScriptedServer(ThreadingHTTPServer):
    def __init__(self, responses: list[dict[str, Any]], statuses: list[int] | None = None) -> None:
        super().__init__(("127.0.0.1", 0), _ScriptedHandler)
        self.responses = list(responses)
        self.next_status = list(statuses or [])
        self.requests: list[dict[str, Any]] = []


@pytest.fixture()
def fake_openai() -> Any:
    server = _ScriptedServer([])
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield server
    server.shutdown()
    server.server_close()
    thread.join(timeout=5)


def _tool_call(name: str, arguments: dict[str, Any], call_id: str = "c1") -> dict[str, Any]:
    return {
        "choices": [
            {
                "message": {
                    "content": None,
                    "tool_calls": [
                        {
                            "id": call_id,
                            "type": "function",
                            "function": {"name": name, "arguments": json.dumps(arguments)},
                        }
                    ],
                }
            }
        ],
        "usage": {"prompt_tokens": 11, "completion_tokens": 7},
    }


def _final(content: str, usage: dict[str, Any] | None = None) -> dict[str, Any]:
    return {"choices": [{"message": {"content": content}}], "usage": usage or {}}


class TestChatRequest:
    def test_authorization_header_only_key_channel(
        self, llm: Any, fake_openai: Any, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        fake_openai.responses = [_final("hi", {"prompt_tokens": 3, "completion_tokens": 2})]
        _set_profile(
            monkeypatch,
            f"http://127.0.0.1:{fake_openai.server_address[1]}/v1",
            "sk-secret-123",
            "m",
        )
        config = llm.resolve_config()
        response = llm._chat_request(config, {"model": "m", "messages": []})
        assert response["choices"][0]["message"]["content"] == "hi"
        request = fake_openai.requests[0]
        # the key never appears inside the request body
        assert "sk-secret-123" not in json.dumps(request)

    def test_linear_retry_on_503(
        self, llm: Any, fake_openai: Any, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # request 1 pops a response but is answered with a 503; request 2 succeeds
        fake_openai.responses = [_final("ok"), _final("ok")]
        fake_openai.next_status = [503]
        _set_profile(
            monkeypatch, f"http://127.0.0.1:{fake_openai.server_address[1]}/v1", "sk-x", "m"
        )
        config = llm.resolve_config()
        monkeypatch.setattr(llm.time, "sleep", lambda seconds: None)
        response = llm._chat_request(config, {"model": "m", "messages": []})
        assert response["choices"][0]["message"]["content"] == "ok"
        assert len(fake_openai.requests) == 2

    def test_non_transient_error_fails_fast(
        self, llm: Any, fake_openai: Any, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        fake_openai.responses = [_final("bad")]
        fake_openai.next_status = [400]
        _set_profile(
            monkeypatch, f"http://127.0.0.1:{fake_openai.server_address[1]}/v1", "sk-x", "m"
        )
        config = llm.resolve_config()
        with pytest.raises(RuntimeError, match="HTTP 400"):
            llm._chat_request(config, {"model": "m", "messages": []})
        assert len(fake_openai.requests) == 1


class TestAgentLoop:
    def test_usage_accumulates_and_unknown_stays_none(
        self, llm: Any, fake_openai: Any, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        fake_openai.responses = [
            _tool_call("refund.quote", {"order_id": "ORD-1001"}),
            _final("done", None),  # no usage block at all
        ]
        _set_profile(
            monkeypatch,
            f"http://127.0.0.1:{fake_openai.server_address[1]}/v1",
            "sk-x",
            "test-model",
        )

        emitted: list[dict[str, Any]] = []

        async def emit(payload: dict[str, Any]) -> None:
            emitted.append(payload)

        class FakeBroker:
            def tool_definitions(self) -> list[dict[str, Any]]:
                return [
                    {
                        "name": "refund.quote",
                        "description": "d",
                        "input_schema": {"type": "object"},
                    }
                ]

            async def call(self, name: str, arguments: dict[str, Any]) -> Any:
                return {"refundable_amount": 49.9}

        result = asyncio.run(
            llm.run(
                {"input": {"message": "task"}, "context": {}},
                FakeBroker(),
                SimpleNamespace(
                    emit=emit, run_id="r", seed=1, fixed_clock=None, tool_names=("refund.quote",)
                ),
            )
        )
        assert result.model == "test-model"
        # one response had usage, the other did not: totals are unknown -> None
        assert result.usage is not None
        assert result.usage.model_calls == 2
        assert result.usage.input_tokens is None
        assert result.usage.output_tokens is None
        usage_events = [e for e in emitted if "usage" in e]
        assert len(usage_events) == 2
        assert usage_events[0]["usage"]["model"] == "test-model"

    def test_malformed_tool_arguments_raise(
        self, llm: Any, fake_openai: Any, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        bad = {
            "choices": [
                {
                    "message": {
                        "tool_calls": [
                            {
                                "id": "c1",
                                "type": "function",
                                "function": {"name": "refund.create", "arguments": "not-json"},
                            }
                        ]
                    }
                }
            ],
            "usage": {"prompt_tokens": 1, "completion_tokens": 1},
        }
        fake_openai.responses = [bad]
        _set_profile(
            monkeypatch, f"http://127.0.0.1:{fake_openai.server_address[1]}/v1", "sk-x", "m"
        )

        async def emit(payload: dict[str, Any]) -> None:
            return None

        class FakeBroker:
            def tool_definitions(self) -> list[dict[str, Any]]:
                return []

            async def call(self, name: str, arguments: dict[str, Any]) -> Any:
                raise AssertionError("must not be reached")

        with pytest.raises(RuntimeError, match="invalid tool arguments"):
            asyncio.run(
                llm.run(
                    {"input": {}, "context": {}},
                    FakeBroker(),
                    SimpleNamespace(emit=emit, run_id="r", seed=1, fixed_clock=None, tool_names=()),
                )
            )


class TestScrubSecrets:
    def test_nested_scrubbing(self) -> None:
        from reprocase.tools.redaction import scrub_secrets

        document = {
            "output": "key sk-live-abcdef123 appears",
            "nested": [{"x": "sk-live-abcdef123"}],
        }
        scrubbed = scrub_secrets(document, ["sk-live-abcdef123"])
        assert "sk-live-abcdef123" not in json.dumps(scrubbed)
        assert scrubbed["nested"][0]["x"] == "[REDACTED]"

    def test_short_dev_placeholders_do_not_shred_words(self) -> None:
        from reprocase.tools.redaction import scrub_secrets

        # placeholders shorter than 8 characters must be ignored wholesale, or
        # ordinary words ("latest", "testing", "contest") get shredded
        text = "latest testing results for the contest"
        assert scrub_secrets(text, ["test", "1234", "demo", "a"]) == text
        # an 8+ character value that really occurs is still scrubbed
        assert scrub_secrets(text, ["testing"]) == text  # 7 chars: ignored
        assert "[REDACTED]" in scrub_secrets(text, ["testing "])  # 8 chars: scrubbed


class TestPreflight:
    def test_preflight_passes_when_configured(self, monkeypatch: pytest.MonkeyPatch) -> None:
        with ImportIsolation(ACME_DIR):
            llm = importlib.import_module("agents.llm_agent")
        monkeypatch.setenv("REPROCASE_LLM_BASE_URL", "http://x/v1")
        monkeypatch.setenv("REPROCASE_LLM_API_KEY", "sk-anything")
        monkeypatch.setenv("REPROCASE_LLM_MODEL", "m")
        llm.preflight()  # must not raise

    def test_preflight_lists_missing_vars(self, monkeypatch: pytest.MonkeyPatch) -> None:
        with ImportIsolation(ACME_DIR):
            llm = importlib.import_module("agents.llm_agent")
        for var in (
            "REPROCASE_LLM_BASE_URL",
            "REPROCASE_LLM_API_KEY",
            "REPROCASE_LLM_MODEL",
            "REPROCASE_LLM_PROFILE",
        ):
            monkeypatch.delenv(var, raising=False)
        with pytest.raises(RuntimeError, match=r"not configured.*REPROCASE_LLM_BASE_URL"):
            llm.preflight()

    def test_preflight_honors_secondary_profile(self, monkeypatch: pytest.MonkeyPatch) -> None:
        with ImportIsolation(ACME_DIR):
            llm = importlib.import_module("agents.llm_agent")
        for var in (
            "REPROCASE_LLM_BASE_URL",
            "REPROCASE_LLM_API_KEY",
            "REPROCASE_LLM_MODEL",
            "REPROCASE_LLM2_BASE_URL",
            "REPROCASE_LLM2_API_KEY",
            "REPROCASE_LLM2_MODEL",
            "REPROCASE_LLM_PROFILE",
        ):
            monkeypatch.delenv(var, raising=False)
        monkeypatch.setenv("REPROCASE_LLM_PROFILE", "secondary")
        monkeypatch.setenv("REPROCASE_LLM2_BASE_URL", "http://x/v1")
        monkeypatch.setenv("REPROCASE_LLM2_MODEL", "m")
        # API key still missing: preflight must name the secondary variable
        with pytest.raises(RuntimeError, match="REPROCASE_LLM2_API_KEY"):
            llm.preflight()

    def test_unconfigured_llm_case_is_invalid_not_agent_failure(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from reprocase.errors import FailureAttribution, FailureCode
        from reprocase.results.models import Verdict
        from reprocase.runner.orchestrator import run_case

        for var in (
            "REPROCASE_LLM_BASE_URL",
            "REPROCASE_LLM_API_KEY",
            "REPROCASE_LLM_MODEL",
            "REPROCASE_LLM_PROFILE",
        ):
            monkeypatch.delenv(var, raising=False)
        result = run_case(ACME_DIR / "refund-smoke-llm.yaml", tmp_path / "runs")
        assert result.verdict == Verdict.INVALID
        assert result.primary_failure is not None
        assert result.primary_failure.code == FailureCode.CASE_AGENT_PREFLIGHT_FAILED
        assert result.primary_failure.attribution == FailureAttribution.CASE_ERROR
        # fail-fast: no environment was provisioned, so no docker is required
        # for this run at all
        assert result.states == ()
