"""M4 resilience acceptance: timeout evidence, degradation, cleanup, Ctrl-C."""

from __future__ import annotations

import json
import shutil
import signal
import subprocess
import sys
import time
from pathlib import Path

import pytest
import yaml
from conftest import SUPPORT_WORLD_DIR, requires_docker, residual_resources
from typer.testing import CliRunner

from reprocase.cli import app
from reprocase.environments.compose import ComposeEnvironmentDriver
from reprocase.environments.protocol import EnvironmentHandle
from reprocase.errors import EnvironmentDriverError, FailureAttribution, FailureCode
from reprocase.results.models import GraderStatus, RunStatus, Verdict
from reprocase.runner.orchestrator import run_case

AGENT_CASE = SUPPORT_WORLD_DIR / "refund-agent.yaml"

pytestmark = [requires_docker, pytest.mark.docker]


def _variant_case(tmp_path: Path, mutate) -> Path:
    environment = yaml.safe_load(
        (SUPPORT_WORLD_DIR / "environment.yaml").read_text(encoding="utf-8")
    )
    environment["compose"]["file"] = str(SUPPORT_WORLD_DIR / "compose.yaml")
    case = yaml.safe_load(AGENT_CASE.read_text(encoding="utf-8"))
    mutate(environment, case)
    case["environment"]["definition"] = "environment.yaml"
    (tmp_path / "environment.yaml").write_text(yaml.safe_dump(environment), encoding="utf-8")
    case_path = tmp_path / "case.yaml"
    case_path.write_text(yaml.safe_dump(case), encoding="utf-8")
    shutil.copytree(SUPPORT_WORLD_DIR / "agents", tmp_path / "agents")
    shutil.copytree(SUPPORT_WORLD_DIR / "tools", tmp_path / "tools")
    return case_path


def _write_agent(tmp_path: Path, name: str, source: str) -> str:
    (tmp_path / f"{name}.py").write_text(source, encoding="utf-8")
    return f"{name}:run"


def test_timeout_after_side_effect_keeps_evidence(tmp_path: Path) -> None:
    """Agent refunds, then hangs: the refund must stay visible in the evidence."""

    entrypoint = _write_agent(
        tmp_path,
        "agent_refund_then_hang",
        """\
import asyncio


async def run(task, tools, context):
    order = await tools.call("order.get", {"order_id": "ORD-1001"})
    await tools.call(
        "refund.create", {"order_id": order["id"], "amount": order["refundable_amount"]}
    )
    await asyncio.sleep(60)
    return {"message": "unreachable"}
""",
    )

    def mutate(environment: dict, case: dict) -> None:
        environment_state = environment  # noqa: F841
        case["agent"]["entrypoint"] = entrypoint
        case["budgets"]["wall_time_seconds"] = 3

    output = tmp_path / "runs"
    result = run_case(_variant_case(tmp_path, mutate), output)

    assert result.verdict == Verdict.FAIL
    assert result.primary_failure is not None
    assert result.primary_failure.code == FailureCode.AGENT_TIMEOUT
    assert result.primary_failure.attribution == FailureAttribution.AGENT_FAILURE
    # the side effect is captured: final state and diff show the refund
    assert len(result.states) == 2
    diff = json.loads((output / result.run_id / "state.diff.json").read_text(encoding="utf-8"))
    paths = {op["path"] for op in diff["ops"]}
    assert "/refunds/RF-9001" in paths
    # the report renders the side effect too
    report = (output / result.run_id / "report.html").read_text(encoding="utf-8")
    assert "RF-9001" in report
    # `reprocase report` regenerates it on demand
    cli = CliRunner().invoke(app, ["report", str(output / result.run_id)])
    assert cli.exit_code == 0
    assert result.duration_ms < 60_000
    assert residual_resources() == []


def test_agent_ignoring_cancellation_gets_warning(tmp_path: Path) -> None:
    entrypoint = _write_agent(
        tmp_path,
        "agent_swallows_cancel",
        """\
import asyncio


async def run(task, tools, context):
    try:
        await asyncio.sleep(60)
    except asyncio.CancelledError:
        await asyncio.sleep(8)  # swallow cancellation, outlive the grace period
    return {"message": "finished late"}
""",
    )

    def mutate(environment: dict, case: dict) -> None:
        case["agent"]["entrypoint"] = entrypoint
        case["budgets"]["wall_time_seconds"] = 3

    output = tmp_path / "runs"
    started = time.monotonic()
    result = run_case(_variant_case(tmp_path, mutate), output)
    elapsed = time.monotonic() - started

    assert result.verdict == Verdict.FAIL
    assert result.primary_failure is not None
    assert result.primary_failure.code == FailureCode.AGENT_TIMEOUT
    assert result.primary_failure.details["ignored_cancellation"] is True
    assert any("ignored cancellation" in warning for warning in result.warnings)
    # grace period was enforced; the run did not wait for the orphaned sleep
    assert elapsed < 45
    assert residual_resources() == []


def test_missing_final_state_is_invalid_not_fail(tmp_path: Path) -> None:
    """§12.3: without a final state the run is INVALID and graders go SKIPPED."""

    def mutate(environment: dict, case: dict) -> None:
        environment["state"]["capture"]["command"] = [
            "python",
            "-c",
            "import json, pathlib, sys; p = pathlib.Path('/data/cap.flag'); "
            "sys.exit(1) if p.exists() else "
            "(p.write_text('x'), print(json.dumps({'orders': {}, 'refunds': {}, "
            "'customers': {}, 'emails': [], 'audit_log': []})))",
        ]

    output = tmp_path / "runs"
    result = run_case(_variant_case(tmp_path, mutate), output)

    assert result.verdict == Verdict.INVALID
    assert result.status == RunStatus.COMPLETED_WITH_ERROR
    assert result.primary_failure is not None
    assert result.primary_failure.attribution == FailureAttribution.ENVIRONMENT_ERROR
    assert result.primary_failure.code == FailureCode.ENV_STATE_CAPTURE_FAILED
    by_id = {grader.grader_id: grader for grader in result.graders}
    # state-dependent graders degrade to SKIPPED; they must not fabricate a FAIL
    assert by_id["refund-created"].status == GraderStatus.SKIPPED
    assert by_id["non-target-orders-unchanged"].status == GraderStatus.SKIPPED
    # the in-container policy grader still ran and saw the correct refund
    assert by_id["policy-tests"].status == GraderStatus.PASS
    assert not any(grader.status == GraderStatus.FAIL for grader in result.graders)
    assert residual_resources() == []


def test_teardown_failure_does_not_mask_agent_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """§16.2: teardown failure lands in secondary, primary stays the agent's."""

    real_teardown = ComposeEnvironmentDriver.teardown

    async def failing_teardown(self: ComposeEnvironmentDriver, handle: EnvironmentHandle) -> None:
        await real_teardown(self, handle)  # actually clean up; simulate the failure after
        raise EnvironmentDriverError(
            "simulated teardown failure",
            code=FailureCode.ENV_TEARDOWN_FAILED,
            details={"compose_project": handle.project_name},
        )

    monkeypatch.setattr(ComposeEnvironmentDriver, "teardown", failing_teardown)

    output = tmp_path / "runs"
    result = run_case(
        SUPPORT_WORLD_DIR / "refund.yaml", output, agent_override="agents.wrong_order:run"
    )

    assert result.verdict == Verdict.FAIL  # not rewritten to INVALID by the teardown failure
    assert result.primary_failure is not None
    assert result.primary_failure.attribution == FailureAttribution.AGENT_FAILURE
    teardown_failures = [
        failure
        for failure in result.secondary_failures
        if failure.code == FailureCode.ENV_TEARDOWN_FAILED
    ]
    assert len(teardown_failures) == 1
    assert result.cleanup_required is True
    assert result.cleanup_command is not None
    assert "reprocase_support_refund_standard" in result.cleanup_command

    cli = CliRunner().invoke(
        app,
        ["run", str(SUPPORT_WORLD_DIR / "refund.yaml"), "-o", str(tmp_path / "cli-runs")],
    )
    assert cli.exit_code == 3
    assert "INFRASTRUCTURE WARNING" in cli.output
    assert residual_resources() == []


def test_ctrl_c_leaves_diagnosable_run(tmp_path: Path) -> None:
    entrypoint = _write_agent(
        tmp_path,
        "agent_slow_cancel",
        """\
import asyncio


async def run(task, tools, context):
    await asyncio.sleep(300)
    return {"message": "unreachable"}
""",
    )

    def mutate(environment: dict, case: dict) -> None:
        case["agent"]["entrypoint"] = entrypoint
        case["budgets"]["wall_time_seconds"] = 300

    case_path = _variant_case(tmp_path, mutate)
    output = tmp_path / "runs"
    process = subprocess.Popen(
        [sys.executable, "-m", "reprocase.cli", "run", str(case_path), "-o", str(output)],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    try:
        deadline = time.monotonic() + 90
        run_dir: Path | None = None
        while time.monotonic() < deadline:
            if output.exists():
                candidates = [path for path in output.iterdir() if path.is_dir()]
                if candidates and (candidates[0] / "events.jsonl").exists():
                    run_dir = candidates[0]
                    break
            time.sleep(0.5)
        assert run_dir is not None, "run directory never appeared"
        time.sleep(2)  # let the environment come up before interrupting
        process.send_signal(signal.SIGINT)
        try:
            process.wait(timeout=90)
        except subprocess.TimeoutExpired:
            process.kill()
            pytest.fail("CLI did not exit after SIGINT")
    finally:
        if process.poll() is None:
            process.kill()

    assert process.returncode == 4
    result = json.loads((run_dir / "result.json").read_text(encoding="utf-8"))
    assert result["status"] == "CANCELLED"
    assert result["verdict"] == "INVALID"
    assert result["primary_failure"]["code"] == "RUN_CANCELLED"
    if result["cleanup_required"]:
        assert result["cleanup_command"]
        assert "reprocase_" in result["cleanup_command"]
    assert residual_resources() == []


def test_doctor_reports_environment() -> None:
    cli = CliRunner().invoke(app, ["doctor"])
    assert cli.exit_code == 0
    assert "docker daemon" in cli.output
    assert "No stale reprocase compose projects." in cli.output
