"""M5 acceptance matrix: three EvalCases, each with correct, wrong, and timeout agents.

Covers the Phase 1 release gate (design §2.2/§25): three correct agents stably
PASS, three wrong agents stably FAIL with agent attribution (never INVALID),
and wall-time timeouts still leave a complete, diagnosable evidence bundle.
Every run must leave zero residual compose resources behind.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path
from typing import Any

import pytest
import yaml
from conftest import SUPPORT_WORLD_DIR, requires_docker

from reprocase.errors import FailureAttribution, FailureCode
from reprocase.results.models import RunStatus, Verdict
from reprocase.runner.orchestrator import run_case

pytestmark = [requires_docker, pytest.mark.docker]

REFUND_AGENT_CASE = SUPPORT_WORLD_DIR / "refund-agent.yaml"
APPROVAL_CASE = SUPPORT_WORLD_DIR / "refund-approval.yaml"
TIMEOUT_CASE = SUPPORT_WORLD_DIR / "tool-timeout.yaml"

EVIDENCE_FILES = (
    "manifest.json",
    "events.jsonl",
    "result.json",
    "report.html",
    "state.initial.json",
    "state.final.json",
    "state.diff.json",
)


def _residual_resources() -> list[str]:
    leftovers: list[str] = []
    commands = {
        "container": ["docker", "ps", "-a", "--filter", "name=reprocase", "-q"],
        "network": ["docker", "network", "ls", "--filter", "name=reprocase", "-q"],
        "volume": ["docker", "volume", "ls", "--filter", "name=reprocase", "-q"],
    }
    for resource, argv in commands.items():
        output = subprocess.run(argv, capture_output=True, text=True, check=False).stdout.strip()
        if output:
            leftovers.append(f"{resource}: {output}")
    return leftovers


def _short_wall_time_case(tmp_path: Path, base: Path, seconds: int) -> Path:
    """Copy a case next to the world (shared environment) with a short wall time."""
    case = yaml.safe_load(base.read_text(encoding="utf-8"))
    case["budgets"]["wall_time_seconds"] = seconds
    case_path = tmp_path / base.name
    case_path.write_text(yaml.safe_dump(case), encoding="utf-8")
    environment = yaml.safe_load(
        (SUPPORT_WORLD_DIR / "environment.yaml").read_text(encoding="utf-8")
    )
    environment["compose"]["file"] = str(SUPPORT_WORLD_DIR / "compose.yaml")
    (tmp_path / "environment.yaml").write_text(yaml.safe_dump(environment), encoding="utf-8")
    shutil.copytree(SUPPORT_WORLD_DIR / "agents", tmp_path / "agents")
    shutil.copytree(SUPPORT_WORLD_DIR / "tools", tmp_path / "tools")
    return case_path


def _run(case_path: Path, tmp_path: Path, agent: str | None = None) -> tuple[Any, Path]:
    output = tmp_path / "runs"
    result = run_case(case_path, output, agent_override=agent)
    run_dir = output / result.run_id
    return result, run_dir


def _assert_evidence_bundle(run_dir: Path, result: Any) -> None:
    for name in EVIDENCE_FILES:
        assert (run_dir / name).is_file(), f"missing evidence file: {name}"
    persisted = json.loads((run_dir / "result.json").read_text(encoding="utf-8"))
    assert persisted["run_id"] == result.run_id
    assert persisted["verdict"] == result.verdict
    events = (run_dir / "events.jsonl").read_text(encoding="utf-8")
    assert '"agent.started"' in events


def _assert_tool_evidence(run_dir: Path) -> None:
    events = (run_dir / "events.jsonl").read_text(encoding="utf-8")
    assert '"tool.call.started"' in events

# --- correct agents: three stable PASS -------------------------------------


@pytest.mark.parametrize(
    ("case", "agent"),
    [
        (REFUND_AGENT_CASE, None),
        (APPROVAL_CASE, None),
        (TIMEOUT_CASE, None),
    ],
    ids=["standard-refund", "approval-refund", "tool-timeout-fallback"],
)
def test_correct_agent_passes(case: Path, agent: str | None, tmp_path: Path) -> None:
    result, run_dir = _run(case, tmp_path, agent)

    assert result.status == RunStatus.COMPLETED, result.model_dump_json(indent=2)
    assert result.verdict == Verdict.PASS
    assert result.primary_failure is None
    assert not result.cleanup_required
    assert result.usage is not None and result.usage.tool_calls > 0
    _assert_evidence_bundle(run_dir, result)
    _assert_tool_evidence(run_dir)
    assert _residual_resources() == []


# --- wrong agents: stable FAIL with agent attribution -----------------------


@pytest.mark.parametrize(
    ("case", "agent", "code"),
    [
        (REFUND_AGENT_CASE, "agents.wrong_refund_agent:run", None),
        (APPROVAL_CASE, "agents.approval_bypass_agent:run", FailureCode.TOOL_EXECUTION_FAILED),
        (APPROVAL_CASE, "agents.approval_overdraw_agent:run", FailureCode.TOOL_EXECUTION_FAILED),
        (TIMEOUT_CASE, "agents.fragile_agent:run", FailureCode.TOOL_TIMEOUT),
    ],
    ids=["wrong-order", "approval-bypass", "approval-overdraw", "fragile-timeout"],
)
def test_wrong_agent_fails(
    case: Path, agent: str, code: FailureCode | None, tmp_path: Path
) -> None:
    result, run_dir = _run(case, tmp_path, agent)

    assert result.status == RunStatus.COMPLETED, result.model_dump_json(indent=2)
    assert result.verdict == Verdict.FAIL
    assert result.primary_failure is not None
    assert result.primary_failure.attribution == FailureAttribution.AGENT_FAILURE
    if code is not None:
        assert result.primary_failure.code == code
    assert any(grader.status == "FAIL" for grader in result.graders)
    _assert_evidence_bundle(run_dir, result)
    _assert_tool_evidence(run_dir)
    assert _residual_resources() == []


def test_approval_bypass_leaves_no_side_effects(tmp_path: Path) -> None:
    """A rejected high-amount refund must not mutate business state."""
    result, run_dir = _run(APPROVAL_CASE, tmp_path, "agents.approval_bypass_agent:run")

    assert result.verdict == Verdict.FAIL
    final = json.loads((run_dir / "state.final.json").read_text(encoding="utf-8"))
    assert final["refunds"] == {}
    assert final["approvals"] == {}
    assert final["emails"] == []
    assert final["orders"]["ORD-3003"]["refund_status"] == "none"
    # the rejection itself is visible as tool evidence, not just as a verdict
    events = (run_dir / "events.jsonl").read_text(encoding="utf-8")
    assert '"tool.call.failed"' in events
    assert _residual_resources() == []


# --- timeout agents: wall-time expiry with preserved evidence ---------------


@pytest.mark.parametrize(
    "case",
    [REFUND_AGENT_CASE, APPROVAL_CASE, TIMEOUT_CASE],
    ids=["standard-refund", "approval-refund", "tool-timeout-fallback"],
)
@pytest.mark.slow
def test_timeout_agent_fails_with_agent_timeout(case: Path, tmp_path: Path) -> None:
    variant = _short_wall_time_case(tmp_path, case, seconds=5)
    result, run_dir = _run(variant, tmp_path, "agents.hang_agent:run")

    assert result.status == RunStatus.COMPLETED, result.model_dump_json(indent=2)
    assert result.verdict == Verdict.FAIL
    assert result.primary_failure is not None
    assert result.primary_failure.attribution == FailureAttribution.AGENT_FAILURE
    assert result.primary_failure.code == FailureCode.AGENT_TIMEOUT
    events = (run_dir / "events.jsonl").read_text(encoding="utf-8")
    assert '"agent.timed_out"' in events
    _assert_evidence_bundle(run_dir, result)
    assert _residual_resources() == []
