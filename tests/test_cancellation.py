"""Real SIGINT through run_case, without Docker startup or timing assumptions."""

from __future__ import annotations

import asyncio
import json
from dataclasses import replace
from pathlib import Path

import pytest
from conftest import SUPPORT_WORLD_DIR
from signal_helpers import interrupt_next_loop_turn

from reprocase.case.loader import load_case
from reprocase.case.models import FakeGraderSpec
from reprocase.environments.compose import ComposeEnvironmentDriver
from reprocase.environments.protocol import CapturedState, EnvironmentHandle, HealthResult
from reprocase.errors import EnvironmentDriverError, FailureCode
from reprocase.results.models import RunStatus, Verdict
from reprocase.runner import orchestrator


@pytest.mark.parametrize("agent_fails", [False, True], ids=["cancel-first", "agent-failure-first"])
def test_teardown_signal_preserves_cleanup_and_failure_order(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, agent_fails: bool
) -> None:
    trace: list[str] = []

    class StubDriver(ComposeEnvironmentDriver):
        async def prepare(self) -> None:
            self.work_dir.mkdir(parents=True)

        async def start(self) -> EnvironmentHandle:
            return EnvironmentHandle(
                "docker_compose", self.project_name, self.compose_file, self.env_file, self.work_dir
            )

        async def restore(self, handle: EnvironmentHandle) -> None:
            pass

        async def healthcheck(self, handle: EnvironmentHandle) -> HealthResult:
            return HealthResult(True, "fixture", 1)

        async def capture_state(self, handle: EnvironmentHandle) -> CapturedState:
            return CapturedState({}, "sha256:fixture", "sha256:fixture")

        async def collect_logs(self, handle: EnvironmentHandle) -> list[Path]:
            # Yield before teardown, as real grading/log collection does. The
            # interrupt is tied to teardown entry, not any earlier phase.
            await asyncio.sleep(0)
            trace.append("logs")
            return []

        async def teardown(self, handle: EnvironmentHandle) -> None:
            trace.append("teardown.started")
            await interrupt_next_loop_turn()
            await asyncio.sleep(0)
            trace.append("teardown.finished")
            raise EnvironmentDriverError(
                "injected cleanup failure", code=FailureCode.ENV_TEARDOWN_FAILED
            )

    def actor(context: object) -> dict[str, str]:
        if agent_fails:
            raise ValueError("injected agent failure")
        return {"message": "done"}

    case_path = SUPPORT_WORLD_DIR / "refund.yaml"
    loaded = load_case(case_path)
    loaded = replace(
        loaded,
        case=loaded.case.model_copy(
            update={
                "graders": (FakeGraderSpec(id="pass", type="fake", explanation="fixture passes"),)
            }
        ),
    )
    monkeypatch.setattr(orchestrator, "load_case", lambda _: loaded)
    monkeypatch.setattr(orchestrator, "load_actor", lambda *_: actor)
    monkeypatch.setattr(orchestrator, "ComposeEnvironmentDriver", StubDriver)

    result = orchestrator.run_case(case_path, tmp_path / "runs")
    assert trace == ["logs", "teardown.started", "teardown.finished"]
    assert result.status == RunStatus.CANCELLED
    assert result.verdict == Verdict.INVALID
    assert result.cleanup_required and result.cleanup_command
    assert result.primary_failure is not None
    codes = [result.primary_failure.code, *(f.code for f in result.secondary_failures)]
    expected = [FailureCode.RUN_CANCELLED, FailureCode.ENV_TEARDOWN_FAILED]
    if agent_fails:
        expected.insert(0, FailureCode.AGENT_EXCEPTION)
    assert codes == expected
    run_dir = tmp_path / "runs" / result.run_id
    persisted = json.loads((run_dir / "result.json").read_text())
    assert persisted["status"] == "CANCELLED"
    assert persisted["primary_failure"]["code"] == expected[0]
    assert (run_dir / "report.html").is_file()
