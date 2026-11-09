"""Tool Broker: allowlist, argument validation, budget, timeout, events (§8.3)."""

from __future__ import annotations

import asyncio
import json
import time
from collections.abc import Iterable, Mapping
from typing import Any

import jsonschema

from reprocase.asyncutil import observe_completion
from reprocase.errors import FailureAttribution, FailureCode, ReprocaseError
from reprocase.recording.events import EventRecorder
from reprocase.tools.protocol import Tool
from reprocase.tools.redaction import redact
from reprocase.tools.registry import ToolRegistry


class ToolCallError(ReprocaseError):
    """Structured tool failure handed back to the agent."""

    def __init__(
        self,
        message: str,
        *,
        code: FailureCode,
        tool: str,
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(
            message,
            code=code,
            attribution=FailureAttribution.AGENT_FAILURE,
            details={"tool": tool, **(details or {})},
        )
        self.tool = tool


class ToolBroker:
    """The only tool interface an agent ever sees."""

    def __init__(
        self,
        registry: ToolRegistry,
        *,
        allow: tuple[str, ...],
        max_calls: int,
        tool_timeout_seconds: float,
        recorder: EventRecorder,
        redact_keys: Iterable[str] = (),
    ) -> None:
        self.registry = registry
        self.allow = frozenset(allow)
        self.remaining = max_calls
        self.tool_timeout_seconds = tool_timeout_seconds
        self.recorder = recorder
        self.redact_keys = tuple(redact_keys)
        self.calls_made = 0
        self.budget_exceeded = False
        self.closed = False
        self._inflight: set[asyncio.Task[Any]] = set()

    def close(self) -> None:
        """Reject all further calls (agent phase is over or being torn down)."""

        self.closed = True

    @property
    def inflight(self) -> int:
        return len(self._inflight)

    async def wait_inflight(self, timeout_seconds: float) -> bool:
        """Bounded wait for in-flight invocations to finish (P1-B).

        Threads cannot be killed: this gives in-progress operations bounded
        time to complete — it never force-stops them. Returns False when
        operations remain unconfirmed after the deadline.
        """

        deadline = time.monotonic() + timeout_seconds
        for task in list(self._inflight):
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            if await observe_completion(task, remaining):
                self._inflight.discard(task)
                if not task.cancelled():
                    task.exception()  # retrieve, avoiding "never retrieved" warnings
        return not self._inflight

    @property
    def tool_names(self) -> tuple[str, ...]:
        return tuple(name for name in self.registry.names if name in self.allow)

    def tool_definitions(self) -> tuple[dict[str, Any], ...]:
        """Allowed tools as ``{name, description, input_schema}`` — the
        projection LLM agents consume to advertise tools to a model."""

        definitions: list[dict[str, Any]] = []
        for name in self.tool_names:
            tool = self.registry.get(name)
            if tool is None:  # pragma: no cover - tool_names comes from the registry
                continue
            definitions.append(
                {
                    "name": tool.name,
                    "description": tool.description,
                    "input_schema": dict(tool.input_schema),
                }
            )
        return tuple(definitions)

    def _redact(self, value: Any) -> Any:
        return redact(value, extra_keys=self.redact_keys)

    def _reject(self, error: ToolCallError) -> ToolCallError:
        self.recorder.emit(
            "tool.call.rejected",
            source="tool_broker",
            payload={
                "tool": error.tool,
                "code": error.code,
                "message": error.message,
                "remaining_calls": self.remaining,
            },
        )
        return error

    def _check_allowed(self, name: str) -> Tool:
        if self.closed:
            raise self._reject(
                ToolCallError(
                    f"Tool broker is closed; late call to {name!r} rejected",
                    code=FailureCode.TOOL_NOT_ALLOWED,
                    tool=name,
                )
            )
        tool = self.registry.get(name)
        if tool is None or name not in self.allow:
            raise self._reject(
                ToolCallError(
                    f"Tool {name!r} is not available or not allowed",
                    code=FailureCode.TOOL_NOT_ALLOWED,
                    tool=name,
                    details={"allowed": sorted(self.allow)},
                )
            )
        return tool

    def _check_arguments(self, tool: Tool, arguments: Mapping[str, Any]) -> None:
        try:
            jsonschema.validate(instance=dict(arguments), schema=dict(tool.input_schema))
        except jsonschema.ValidationError as exc:
            location = ".".join(str(part) for part in exc.absolute_path)
            raise self._reject(
                ToolCallError(
                    f"Invalid arguments for {tool.name!r}: {exc.message}"
                    + (f" (at {location})" if location else ""),
                    code=FailureCode.TOOL_ARGUMENT_INVALID,
                    tool=tool.name,
                    details={"schema_path": location},
                )
            ) from exc
        except jsonschema.SchemaError as exc:
            # unreachable after build-time validation; if a schema still turns
            # out invalid at call time it is a provider defect, never an
            # agent-argument problem
            raise ReprocaseError(
                f"Tool {tool.name!r} has an invalid input schema: {exc.message}",
                code=FailureCode.CASE_SCHEMA_INVALID,
                attribution=FailureAttribution.CASE_ERROR,
                details={"tool": tool.name},
            ) from exc

    def _consume_budget(self, name: str) -> None:
        # Check-and-decrement without an await in between: atomic under
        # cooperative scheduling, so timed-out or failing calls still count.
        if self.remaining <= 0:
            self.budget_exceeded = True
            raise self._reject(
                ToolCallError(
                    f"Tool call budget exhausted ({self.calls_made} calls made)",
                    code=FailureCode.AGENT_BUDGET_EXCEEDED,
                    tool=name,
                )
            )
        self.remaining -= 1
        self.calls_made += 1

    async def call(self, name: str, arguments: Mapping[str, Any]) -> Any:
        tool = self._check_allowed(name)
        self._check_arguments(tool, arguments)
        self._consume_budget(name)

        sequence_payload = {
            "tool": name,
            "arguments": self._redact(dict(arguments)),
            "remaining_calls": self.remaining,
        }
        self.recorder.emit("tool.call.started", source="tool_broker", payload=sequence_payload)
        started = time.monotonic_ns()
        # Each invocation runs as a tracked task (P1-B). The broker only
        # observes completion: a per-tool timeout or a cancellation of this
        # call never cancels the operation itself (a to_thread invocation
        # keeps running in its thread), so the runner can later confirm
        # whether the side effect actually landed.
        invoke_task = asyncio.create_task(tool.invoke(arguments))
        self._inflight.add(invoke_task)
        try:
            finished = await observe_completion(invoke_task, self.tool_timeout_seconds)
            if not finished:
                raise TimeoutError(f"tool invoke exceeded {self.tool_timeout_seconds}s")
            result = invoke_task.result()
        except TimeoutError as exc:
            error = ToolCallError(
                f"Tool {name!r} timed out after {self.tool_timeout_seconds}s",
                code=FailureCode.TOOL_TIMEOUT,
                tool=name,
            )
            self._emit_failed(error, started)
            raise error from exc
        except ReprocaseError as exc:
            # structured infrastructure/case errors keep their original code
            # and attribution — they must never be re-labeled as tool (agent)
            # failures; CancelledError is BaseException and passes through
            self.recorder.emit(
                "tool.call.failed",
                source="tool_broker",
                payload={
                    "tool": name,
                    "code": exc.code,
                    "message": exc.message,
                    "duration_ms": max(0, (time.monotonic_ns() - started) // 1_000_000),
                },
            )
            raise
        except Exception as exc:
            error = ToolCallError(
                f"Tool {name!r} raised {type(exc).__name__}: {exc}",
                code=FailureCode.TOOL_EXECUTION_FAILED,
                tool=name,
            )
            self._emit_failed(error, started)
            raise error from exc
        finally:
            if invoke_task.done():
                self._inflight.discard(invoke_task)

        try:
            json.dumps(result)
        except (TypeError, ValueError) as exc:
            error = ToolCallError(
                f"Tool {name!r} returned a non-JSON-serializable result",
                code=FailureCode.TOOL_EXECUTION_FAILED,
                tool=name,
            )
            self._emit_failed(error, started)
            raise error from exc

        self.recorder.emit(
            "tool.call.completed",
            source="tool_broker",
            payload={
                "tool": name,
                "duration_ms": max(0, (time.monotonic_ns() - started) // 1_000_000),
                "result": self._redact(result),
            },
        )
        return result

    def _emit_failed(self, error: ToolCallError, started: int) -> None:
        self.recorder.emit(
            "tool.call.failed",
            source="tool_broker",
            payload={
                "tool": error.tool,
                "code": error.code,
                "message": error.message,
                "duration_ms": max(0, (time.monotonic_ns() - started) // 1_000_000),
            },
        )
