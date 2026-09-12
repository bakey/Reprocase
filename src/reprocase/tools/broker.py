"""Tool Broker: allowlist, argument validation, budget, timeout, events (§8.3)."""

from __future__ import annotations

import asyncio
import json
import time
from collections.abc import Mapping
from typing import Any

import jsonschema

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
    ) -> None:
        self.registry = registry
        self.allow = frozenset(allow)
        self.remaining = max_calls
        self.tool_timeout_seconds = tool_timeout_seconds
        self.recorder = recorder
        self.calls_made = 0
        self.budget_exceeded = False

    @property
    def tool_names(self) -> tuple[str, ...]:
        return tuple(name for name in self.registry.names if name in self.allow)

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
            "arguments": redact(dict(arguments)),
            "remaining_calls": self.remaining,
        }
        self.recorder.emit("tool.call.started", source="tool_broker", payload=sequence_payload)
        started = time.monotonic_ns()
        try:
            result = await asyncio.wait_for(
                tool.invoke(arguments), timeout=self.tool_timeout_seconds
            )
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
                "result": redact(result),
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
