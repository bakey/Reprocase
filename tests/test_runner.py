from __future__ import annotations

import json
from pathlib import Path

import pytest

from reprocase.errors import FailureAttribution, FailureCode
from reprocase.results.models import RunState, RunStatus, Verdict
from reprocase.runner.fakes import FakeAgent
from reprocase.runner.orchestrator import run_case

ROOT = Path(__file__).resolve().parents[1]


def _event_rows(run_dir: Path) -> list[dict[str, object]]:
    return [json.loads(line) for line in (run_dir / "events.jsonl").read_text().splitlines()]


def test_pass_run_writes_complete_evidence(tmp_path: Path) -> None:
    result = run_case(ROOT / "examples/fake/eval-pass.yaml", tmp_path)
    run_dir = tmp_path / result.run_id

    assert result.verdict == Verdict.PASS
    assert result.status == RunStatus.COMPLETED
    assert result.primary_failure is None
    assert (run_dir / "manifest.json").is_file()
    assert (run_dir / "events.jsonl").is_file()
    assert (run_dir / "result.json").is_file()

    rows = _event_rows(run_dir)
    sequences = [row["sequence"] for row in rows]
    assert sequences == list(range(1, len(rows) + 1))
    assert rows[-1]["type"] == "run.completed"


def test_eval_failure_is_attributed_to_agent(tmp_path: Path) -> None:
    result = run_case(ROOT / "examples/fake/eval-fail.yaml", tmp_path)

    assert result.verdict == Verdict.FAIL
    assert result.status == RunStatus.COMPLETED
    assert result.primary_failure is not None
    assert result.primary_failure.attribution == FailureAttribution.AGENT_FAILURE
    assert result.primary_failure.code == FailureCode.GRADER_ASSERTION_FAILED


def test_grader_error_makes_run_invalid(tmp_path: Path) -> None:
    result = run_case(ROOT / "examples/fake/eval-invalid.yaml", tmp_path)

    assert result.verdict == Verdict.INVALID
    assert result.status == RunStatus.COMPLETED_WITH_ERROR
    assert result.primary_failure is not None
    assert result.primary_failure.attribution == FailureAttribution.GRADER_ERROR


def test_environment_failure_preserves_run_record(tmp_path: Path) -> None:
    case_dir = tmp_path / "case"
    case_dir.mkdir()
    (case_dir / "environment.yaml").write_text(
        """\
api_version: reprocase.dev/environment/v0
driver: fake
config:
  fail_on_start: true
""",
        encoding="utf-8",
    )
    (case_dir / "case.yaml").write_text(
        """\
api_version: reprocase.dev/v0
kind: EvalCase
metadata:
  id: fake.environment.error
  version: 0.1.0
  title: Environment error
task:
  input: {}
agent:
  adapter: fake
environment:
  driver: fake
  definition: environment.yaml
graders:
  - id: never-runs
    type: fake
    outcome: PASS
    explanation: Should not run
""",
        encoding="utf-8",
    )
    output = tmp_path / "runs"

    result = run_case(case_dir / "case.yaml", output)
    run_dir = output / result.run_id

    assert result.verdict == Verdict.INVALID
    assert result.primary_failure is not None
    assert result.primary_failure.code == FailureCode.ENV_FAKE_START_FAILED
    assert (run_dir / "manifest.json").is_file()
    assert (run_dir / "events.jsonl").is_file()
    assert (run_dir / "result.json").is_file()


def test_agent_error_is_fail_but_grading_continues(tmp_path: Path) -> None:
    case_dir = tmp_path / "case"
    case_dir.mkdir()
    (case_dir / "environment.yaml").write_text(
        "api_version: reprocase.dev/environment/v0\ndriver: fake\nconfig: {}\n",
        encoding="utf-8",
    )
    (case_dir / "case.yaml").write_text(
        """\
api_version: reprocase.dev/v0
kind: EvalCase
metadata:
  id: fake.agent.error
  version: 0.1.0
  title: Agent error
task:
  input: {message: fail}
agent:
  adapter: fake
  config:
    outcome: error
environment:
  driver: fake
  definition: environment.yaml
graders:
  - id: still-runs
    type: fake
    outcome: PASS
    explanation: Grading continued
""",
        encoding="utf-8",
    )

    result = run_case(case_dir / "case.yaml", tmp_path / "runs")

    assert result.verdict == Verdict.FAIL
    assert result.primary_failure is not None
    assert result.primary_failure.code == FailureCode.AGENT_FAKE_EXECUTION_FAILED
    assert result.graders[0].status == "PASS"


def test_cancellation_writes_result_and_reaches_cancelled_state(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def interrupted_run(self: object, task: dict[str, object]) -> object:
        raise KeyboardInterrupt

    monkeypatch.setattr(FakeAgent, "run", interrupted_run)

    result = run_case(ROOT / "examples/fake/eval-pass.yaml", tmp_path)
    run_dir = tmp_path / result.run_id

    assert result.status == RunStatus.CANCELLED
    assert result.verdict == Verdict.INVALID
    assert result.final_state == RunState.CANCELLED
    assert result.primary_failure is not None
    assert result.primary_failure.code == FailureCode.RUN_CANCELLED
    assert (run_dir / "result.json").is_file()

    rows = _event_rows(run_dir)
    assert rows[-1]["type"] == "run.completed"
    assert any(row["type"] == "run.cancelled" for row in rows)
