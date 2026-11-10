"""M9 audit matrix: every task case passes with its reference agent and fails
with each registered wrong agent (distinct failure causes).

Runs the real Acme World container per case; the wrong agent is injected via
the runner's agent override (the CLI ``--agent`` path).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
import yaml
from conftest import requires_docker, residual_resources

from reprocase.results.models import RunStatus, Verdict
from reprocase.runner.orchestrator import run_case

ROOT = Path(__file__).resolve().parents[1]
ACME = ROOT / "examples" / "acme-world"

pytestmark = [requires_docker, pytest.mark.docker]


def _tasks() -> list[dict[str, Any]]:
    tasks: list[dict[str, Any]] = []
    for case_path in sorted(ACME.glob("task-*.yaml")):
        note = yaml.safe_load((ACME / "audit" / f"{case_path.stem}.notes.yaml").read_text("utf-8"))
        for wrong in note["wrong_agents"]:
            tasks.append(
                {
                    "case": case_path,
                    "case_id": note["case"],
                    "wrong": wrong["entrypoint"],
                    "failure": wrong["expected_failure"],
                    "expected_failed_graders": wrong["expected_failed_graders"],
                }
            )
    return tasks


def _task_id(task: dict[str, Any]) -> str:
    wrong_name = task["wrong"].partition(":")[2]
    return f"{task['case'].stem}::{wrong_name}"


TASKS = _tasks()
assert TASKS, "task registry is empty"


@pytest.mark.parametrize("task", TASKS, ids=_task_id)
def test_reference_passes_and_wrong_fails(task: dict[str, Any], tmp_path: Path) -> None:
    # reference run: the case's own agent must PASS
    reference = run_case(task["case"], tmp_path / "reference")
    assert reference.verdict == Verdict.PASS, (
        f"reference agent failed on {task['case_id']}: "
        f"{reference.primary_failure.model_dump() if reference.primary_failure else 'graders'}"
    )
    assert reference.status == RunStatus.COMPLETED

    # wrong run: the registered wrong agent must FAIL (failure cause is in the note)
    wrong = run_case(task["case"], tmp_path / "wrong", agent_override=task["wrong"])
    assert wrong.verdict == Verdict.FAIL, (
        f"wrong agent {task['wrong']!r} unexpectedly PASSED on {task['case_id']}"
    )
    # failure fidelity: the wrong agent must COMPLETE and land its claim in the
    # evidence — the designed story is "the lie gets caught by graders", not
    # "the agent crashes on the rejected call" (agent.failed would make the
    # fabricated-completion pattern invisible to the failure catalog)
    assert wrong.agent_output is not None, (
        f"wrong agent {task['wrong']!r} crashed instead of completing with its "
        f"claim on {task['case_id']}; primary: "
        f"{wrong.primary_failure.model_dump() if wrong.primary_failure else 'graders'}"
    )
    failed_graders = [
        g.grader_id if hasattr(g, "grader_id") else str(g)
        for g in wrong.graders
        if getattr(g, "status", None) == "FAIL"
    ]
    assert failed_graders, (
        f"wrong agent {task['wrong']!r} failed without any failing grader on {task['case_id']}"
    )
    # the declared failure mechanism, executable: every grader the note
    # promises to trip must actually be in the failed set
    missing = [grader for grader in task["expected_failed_graders"] if grader not in failed_graders]
    assert not missing, (
        f"wrong agent {task['wrong']!r} on {task['case_id']} did not trip the "
        f"declared grader(s) {missing}; failed set was {failed_graders}"
    )

    # digest discipline: same case file -> identical case digest across both runs
    reference_digest = reference.case.digest if reference.case and reference.case.digest else None
    wrong_digest = wrong.case.digest if wrong.case and wrong.case.digest else None
    assert reference_digest and wrong_digest and reference_digest == wrong_digest

    assert residual_resources() == []
