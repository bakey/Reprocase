from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import pytest

from reprocase.agents.protocol import AgentContext, AgentResult
from reprocase.agents.python_adapter import PythonAgentAdapter
from reprocase.errors import CaseValidationError, FailureCode, ReprocaseError


def _context() -> AgentContext:
    async def emit(payload: dict[str, Any]) -> None:
        return None

    return AgentContext(run_id="run_t", seed=0, fixed_clock=None, tool_names=(), emit=emit)


def _write(tmp_path: Path, name: str, source: str) -> None:
    (tmp_path / name).write_text(source, encoding="utf-8")


def test_raw_return_is_normalized(tmp_path: Path) -> None:
    _write(tmp_path, "agent_raw.py", "async def run(task, tools, context):\n    return {'ok': 1}\n")
    adapter = PythonAgentAdapter("agent_raw:run", tmp_path)
    result = asyncio.run(adapter.run({}, None, _context()))
    assert isinstance(result, AgentResult)
    assert result.output == {"ok": 1}
    assert result.usage is None


def test_agent_result_passthrough(tmp_path: Path) -> None:
    _write(
        tmp_path,
        "agent_full.py",
        """\
from reprocase.agents.protocol import AgentResult
from reprocase.results.models import Usage


async def run(task, tools, context):
    return AgentResult(output={"ok": 2}, summary="done", usage=Usage(model_calls=1))
""",
    )
    adapter = PythonAgentAdapter("agent_full:run", tmp_path)
    result = asyncio.run(adapter.run({}, None, _context()))
    assert result.summary == "done"
    assert result.usage is not None
    assert result.usage.model_calls == 1


def test_sync_function_rejected(tmp_path: Path) -> None:
    _write(tmp_path, "agent_sync.py", "def run(task, tools, context):\n    return {}\n")
    with pytest.raises(CaseValidationError) as excinfo:
        PythonAgentAdapter("agent_sync:run", tmp_path)
    assert excinfo.value.code == FailureCode.CASE_ENTRYPOINT_INVALID


def test_non_serializable_output_is_agent_failure(tmp_path: Path) -> None:
    _write(
        tmp_path,
        "agent_bad_output.py",
        "async def run(task, tools, context):\n    return object()\n",
    )
    adapter = PythonAgentAdapter("agent_bad_output:run", tmp_path)
    with pytest.raises(ReprocaseError) as excinfo:
        asyncio.run(adapter.run({}, None, _context()))
    assert excinfo.value.code == FailureCode.AGENT_OUTPUT_INVALID
    assert excinfo.value.attribution == "AGENT_FAILURE"


def test_bad_entrypoint_rejected(tmp_path: Path) -> None:
    with pytest.raises(CaseValidationError):
        PythonAgentAdapter("no-separator", tmp_path)
    with pytest.raises(CaseValidationError):
        PythonAgentAdapter("ghost_module:run", tmp_path)
    _write(tmp_path, "agent_nofunc.py", "X = 1\n")
    with pytest.raises(CaseValidationError):
        PythonAgentAdapter("agent_nofunc:missing", tmp_path)
