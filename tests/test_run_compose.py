"""M2 acceptance matrix: PASS / FAIL / INVALID / evidence over the Compose loop."""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest
import yaml
from conftest import SUPPORT_WORLD_DIR, requires_docker

from reprocase.errors import FailureAttribution, FailureCode
from reprocase.results.models import GraderResult, GraderStatus, RunResult, RunStatus, Verdict
from reprocase.runner.orchestrator import run_case

REFUND_CASE = SUPPORT_WORLD_DIR / "refund.yaml"
GOLDEN_INITIAL_DIGEST = "sha256:5189f4f2dd66c9edf848040762f2492c2569a99f6dcccec3bf9511e2f868c753"

pytestmark = [requires_docker, pytest.mark.docker]


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


def _run(tmp_path: Path, agent: str | None = None) -> RunResult:
    return run_case(REFUND_CASE, tmp_path / "runs", agent_override=agent)


def _grader(result: RunResult, grader_id: str) -> GraderResult:
    return next(grader for grader in result.graders if grader.grader_id == grader_id)


def _diff_ops(result: RunResult, tmp_path: Path) -> list[dict[str, object]]:
    run_dir = tmp_path / "runs" / result.run_id
    return json.loads((run_dir / "state.diff.json").read_text(encoding="utf-8"))["ops"]


def test_correct_actor_passes_with_complete_evidence(tmp_path: Path) -> None:
    result = _run(tmp_path)

    assert result.verdict == Verdict.PASS
    assert result.status == RunStatus.COMPLETED
    assert result.primary_failure is None
    assert result.reproducibility_level == "R1"
    assert {grader.status for grader in result.graders} == {GraderStatus.PASS}
    assert len(result.graders) == 4

    initial, final = result.states
    assert initial.content_digest == GOLDEN_INITIAL_DIGEST
    assert final.content_digest != initial.content_digest
    assert initial.artifact.path == "state.initial.json"
    assert result.state_diff is not None
    assert result.agent_output is not None

    run_dir = tmp_path / "runs" / result.run_id
    assert (run_dir / "state.initial.json").is_file()
    assert (run_dir / "state.final.json").is_file()
    assert (run_dir / "state.diff.json").is_file()
    assert any(artifact.media_type == "text/plain" for artifact in result.artifacts)
    assert _residual_resources() == []


def test_diff_shows_target_and_nontarget_changes(tmp_path: Path) -> None:
    correct = _run(tmp_path / "correct")
    correct_paths = {op["path"] for op in _diff_ops(correct, tmp_path / "correct")}
    assert "/refunds/RF-9001" in correct_paths
    assert "/orders/ORD-1001/refund_status" in correct_paths
    assert not any(path.startswith("/orders/ORD-2002") for path in correct_paths)

    wrong = _run(tmp_path / "wrong", agent="agents.wrong_order:run")
    wrong_paths = {op["path"] for op in _diff_ops(wrong, tmp_path / "wrong")}
    assert "/orders/ORD-2002/refund_status" in wrong_paths


def test_wrong_order_actor_fails_with_structured_evidence(tmp_path: Path) -> None:
    result = _run(tmp_path, agent="agents.wrong_order:run")

    assert result.verdict == Verdict.FAIL
    assert result.status == RunStatus.COMPLETED
    assert _grader(result, "refund-created").status == GraderStatus.FAIL
    assert _grader(result, "non-target-orders-unchanged").status == GraderStatus.FAIL
    assert _grader(result, "policy-tests").status == GraderStatus.FAIL

    for grader_id in ("refund-created", "non-target-orders-unchanged", "policy-tests"):
        graded = _grader(result, grader_id)
        assert graded.explanation, grader_id
        assert graded.expected is not None or grader_id == "non-target-orders-unchanged"
        assert graded.actual is not None, grader_id
        assert graded.evidence, grader_id
    unchanged = _grader(result, "non-target-orders-unchanged")
    assert unchanged.actual["initial"] != unchanged.actual["final"]
    assert _residual_resources() == []


def test_missing_email_actor_fails_notification_grader(tmp_path: Path) -> None:
    result = _run(tmp_path, agent="agents.missing_email:run")

    assert result.verdict == Verdict.FAIL
    assert _grader(result, "refund-created").status == GraderStatus.PASS
    notified = _grader(result, "customer-notified")
    assert notified.status == GraderStatus.FAIL
    assert notified.expected == 1
    assert notified.actual == 0
    assert _grader(result, "non-target-orders-unchanged").status == GraderStatus.PASS
    assert _residual_resources() == []


def test_unapproved_refund_actor_fails_policy_grader(tmp_path: Path) -> None:
    result = _run(tmp_path, agent="agents.unapproved_refund:run")

    assert result.verdict == Verdict.FAIL
    policy = _grader(result, "policy-tests")
    assert policy.status == GraderStatus.FAIL
    assert policy.grader_type == "test_command"
    assert "999" in json.dumps(policy.actual)
    assert policy.expected["amount"] == 49.9
    assert any(str(evidence).startswith("artifact:") for evidence in policy.evidence)

    failure = result.primary_failure
    assert failure is not None
    assert failure.attribution == FailureAttribution.AGENT_FAILURE
    assert _residual_resources() == []


def _write_variant_case(tmp_path: Path, mutate) -> Path:
    environment = yaml.safe_load(
        (SUPPORT_WORLD_DIR / "environment.yaml").read_text(encoding="utf-8")
    )
    environment["compose"]["file"] = str(SUPPORT_WORLD_DIR / "compose.yaml")
    case = yaml.safe_load(REFUND_CASE.read_text(encoding="utf-8"))
    mutate(environment, case)
    case["environment"]["definition"] = "environment.yaml"
    (tmp_path / "environment.yaml").write_text(yaml.safe_dump(environment), encoding="utf-8")
    case_path = tmp_path / "case.yaml"
    case_path.write_text(yaml.safe_dump(case), encoding="utf-8")
    shutil.copytree(SUPPORT_WORLD_DIR / "agents", tmp_path / "agents")
    return case_path


def test_grader_error_makes_run_invalid(tmp_path: Path) -> None:
    def mutate(environment: dict, case: dict) -> None:
        case["graders"] = [
            {
                "id": "broken-policy",
                "type": "test_command",
                "service": "api",
                "command": ["python", "-c", "import sys; sys.exit(2)"],
            }
        ]

    result = run_case(_write_variant_case(tmp_path, mutate), tmp_path / "runs")

    assert result.verdict == Verdict.INVALID
    assert result.status == RunStatus.COMPLETED_WITH_ERROR
    graded = _grader(result, "broken-policy")
    assert graded.status == GraderStatus.ERROR
    assert result.primary_failure is not None
    assert result.primary_failure.attribution == FailureAttribution.GRADER_ERROR
    assert _residual_resources() == []


def test_environment_failure_makes_run_invalid(tmp_path: Path) -> None:
    def mutate(environment: dict, case: dict) -> None:
        environment["restore"]["command"] = ["python", "-c", "import sys; sys.exit(3)"]

    result = run_case(_write_variant_case(tmp_path, mutate), tmp_path / "runs")

    assert result.verdict == Verdict.INVALID
    assert result.status == RunStatus.COMPLETED_WITH_ERROR
    assert result.primary_failure is not None
    assert result.primary_failure.attribution == FailureAttribution.ENVIRONMENT_ERROR
    assert result.primary_failure.code == FailureCode.ENV_RESTORE_FAILED
    assert _residual_resources() == []


def test_agent_exception_is_agent_failure(tmp_path: Path) -> None:
    def mutate(environment: dict, case: dict) -> None:
        # top-level module avoids the `agents` package import cache across tests
        case["agent"]["entrypoint"] = "boom_actor:run"

    case_path = _write_variant_case(tmp_path, mutate)
    (tmp_path / "boom_actor.py").write_text(
        "def run(context):\n    raise RuntimeError('actor exploded')\n",
        encoding="utf-8",
    )
    result = run_case(case_path, tmp_path / "runs")

    assert result.verdict == Verdict.FAIL
    assert result.primary_failure is not None
    assert result.primary_failure.attribution == FailureAttribution.AGENT_FAILURE
    assert result.primary_failure.code == FailureCode.AGENT_EXCEPTION
    # partial evidence is preserved: final state was still captured and graded
    assert len(result.states) == 2
    assert _grader(result, "refund-created").status == GraderStatus.FAIL
    assert _residual_resources() == []
