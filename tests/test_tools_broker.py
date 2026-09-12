from __future__ import annotations

import asyncio
import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import pytest

from reprocase.errors import FailureCode
from reprocase.recording.events import EventRecorder
from reprocase.tools.broker import ToolBroker, ToolCallError
from reprocase.tools.redaction import REDACTED
from reprocase.tools.registry import ToolRegistry


class StubTool:
    def __init__(
        self,
        name: str = "order.get",
        *,
        schema: dict[str, Any] | None = None,
        result: Any = None,
        error: Exception | None = None,
        delay: float = 0.0,
    ) -> None:
        self.name = name
        self.description = "stub"
        self.input_schema = schema or {"type": "object"}
        self.result = result if result is not None else {"ok": True}
        self.error = error
        self.delay = delay
        self.calls: list[Mapping[str, Any]] = []

    async def invoke(self, arguments: Mapping[str, Any]) -> Any:
        self.calls.append(arguments)
        if self.delay:
            await asyncio.sleep(self.delay)
        if self.error is not None:
            raise self.error
        return self.result


def _broker(
    tmp_path: Path,
    tool: StubTool,
    *,
    allow: tuple[str, ...] = ("order.get",),
    max_calls: int = 5,
    timeout: float = 1.0,
) -> ToolBroker:
    recorder = EventRecorder(tmp_path / "events.jsonl", "run_test")
    return ToolBroker(
        ToolRegistry([tool]),
        allow=allow,
        max_calls=max_calls,
        tool_timeout_seconds=timeout,
        recorder=recorder,
    )


def _events(tmp_path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in (tmp_path / "events.jsonl").read_text().splitlines()]


def _run(coro: Any) -> Any:
    return asyncio.run(coro)


def test_unallowed_tool_rejected_before_invocation(tmp_path: Path) -> None:
    tool = StubTool()
    broker = _broker(tmp_path, tool, allow=("other.tool",))

    with pytest.raises(ToolCallError) as excinfo:
        _run(broker.call("order.get", {}))

    assert excinfo.value.code == FailureCode.TOOL_NOT_ALLOWED
    assert tool.calls == []
    events = _events(tmp_path)
    assert events[-1]["type"] == "tool.call.rejected"
    assert events[-1]["payload"]["code"] == FailureCode.TOOL_NOT_ALLOWED


def test_unknown_tool_is_also_not_allowed(tmp_path: Path) -> None:
    broker = _broker(tmp_path, StubTool())
    with pytest.raises(ToolCallError) as excinfo:
        _run(broker.call("ghost.tool", {}))
    assert excinfo.value.code == FailureCode.TOOL_NOT_ALLOWED


def test_invalid_arguments_rejected_before_invocation(tmp_path: Path) -> None:
    tool = StubTool(
        schema={
            "type": "object",
            "properties": {"amount": {"type": "number"}},
            "required": ["amount"],
            "additionalProperties": False,
        }
    )
    broker = _broker(tmp_path, tool)

    with pytest.raises(ToolCallError) as excinfo:
        _run(broker.call("order.get", {"amount": "lots"}))
    assert excinfo.value.code == FailureCode.TOOL_ARGUMENT_INVALID
    with pytest.raises(ToolCallError):
        _run(broker.call("order.get", {"amount": 1, "extra": True}))
    with pytest.raises(ToolCallError):
        _run(broker.call("order.get", {}))
    assert tool.calls == []
    assert broker.calls_made == 0
    rejected = [e for e in _events(tmp_path) if e["type"] == "tool.call.rejected"]
    assert len(rejected) == 3


def test_budget_consumed_atomically_even_on_failures(tmp_path: Path) -> None:
    tool = StubTool(delay=0.2)
    broker = _broker(tmp_path, tool, max_calls=1, timeout=0.05)

    with pytest.raises(ToolCallError) as excinfo:
        _run(broker.call("order.get", {}))
    assert excinfo.value.code == FailureCode.TOOL_TIMEOUT
    assert broker.calls_made == 1
    assert broker.remaining == 0

    with pytest.raises(ToolCallError) as excinfo:
        _run(broker.call("order.get", {}))
    assert excinfo.value.code == FailureCode.AGENT_BUDGET_EXCEEDED
    assert broker.budget_exceeded
    assert len(tool.calls) == 1


def test_invoke_exception_is_structured(tmp_path: Path) -> None:
    broker = _broker(tmp_path, StubTool(error=RuntimeError("db down")))
    with pytest.raises(ToolCallError) as excinfo:
        _run(broker.call("order.get", {}))
    assert excinfo.value.code == FailureCode.TOOL_EXECUTION_FAILED
    assert "db down" in excinfo.value.message
    assert _events(tmp_path)[-1]["type"] == "tool.call.failed"


def test_non_serializable_result_is_structured(tmp_path: Path) -> None:
    broker = _broker(tmp_path, StubTool(result=object()))
    with pytest.raises(ToolCallError) as excinfo:
        _run(broker.call("order.get", {}))
    assert excinfo.value.code == FailureCode.TOOL_EXECUTION_FAILED


def test_successful_call_emits_started_and_completed(tmp_path: Path) -> None:
    tool = StubTool(result={"status": "ok", "password": "s3cret"})
    broker = _broker(tmp_path, tool)

    result = _run(broker.call("order.get", {"order_id": "ORD-1", "api_key": "abc"}))

    assert result["status"] == "ok"
    events = _events(tmp_path)
    started = next(e for e in events if e["type"] == "tool.call.started")
    completed = next(e for e in events if e["type"] == "tool.call.completed")
    assert started["payload"]["arguments"]["api_key"] == REDACTED
    assert started["payload"]["arguments"]["order_id"] == "ORD-1"
    assert completed["payload"]["result"]["password"] == REDACTED
    assert completed["payload"]["result"]["status"] == "ok"
    assert broker.remaining == 4


def test_registry_rejects_duplicate_names() -> None:
    from reprocase.errors import ReprocaseError

    with pytest.raises(ReprocaseError):
        ToolRegistry([StubTool(), StubTool()])


def test_reprocase_error_from_invoke_is_not_rewrapped(tmp_path: Path) -> None:
    from reprocase.errors import FailureAttribution, ReprocaseError

    infra_error = ReprocaseError(
        "environment exec failed",
        code=FailureCode.ENV_COMPOSE_INVALID,
        attribution=FailureAttribution.ENVIRONMENT_ERROR,
    )
    broker = _broker(tmp_path, StubTool(error=infra_error))

    with pytest.raises(ReprocaseError) as excinfo:
        _run(broker.call("order.get", {}))

    assert excinfo.value is infra_error  # original error, unwrapped
    assert excinfo.value.code == FailureCode.ENV_COMPOSE_INVALID
    assert excinfo.value.attribution == FailureAttribution.ENVIRONMENT_ERROR
    failed = [e for e in _events(tmp_path) if e["type"] == "tool.call.failed"]
    assert failed[-1]["payload"]["code"] == FailureCode.ENV_COMPOSE_INVALID


def test_cancelled_error_passes_through(tmp_path: Path) -> None:
    broker = _broker(tmp_path, StubTool(error=asyncio.CancelledError()))

    with pytest.raises(asyncio.CancelledError):
        _run(broker.call("order.get", {}))
