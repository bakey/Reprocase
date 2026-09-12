from __future__ import annotations

from reprocase.errors import FailureAttribution, FailureCode
from reprocase.results.models import FailureRecord, GraderResult, GraderStatus, Verdict
from reprocase.runner.orchestrator import _RunDraft, _verdict


def _failure(attribution: FailureAttribution) -> FailureRecord:
    return FailureRecord(
        attribution=attribution, code=FailureCode.RUNNER_INTERNAL_ERROR, message="x"
    )


def _grader(status: GraderStatus) -> GraderResult:
    return GraderResult(grader_id="g", status=status, critical=True, explanation="x")


def test_all_pass() -> None:
    draft = _RunDraft()
    draft.graders.extend([_grader(GraderStatus.PASS), _grader(GraderStatus.PASS)])
    assert _verdict(draft) == Verdict.PASS


def test_grader_fail_is_fail() -> None:
    draft = _RunDraft()
    draft.graders.append(_grader(GraderStatus.FAIL))
    assert _verdict(draft) == Verdict.FAIL


def test_agent_failure_is_fail() -> None:
    draft = _RunDraft()
    draft.failures.append(_failure(FailureAttribution.AGENT_FAILURE))
    draft.graders.append(_grader(GraderStatus.PASS))
    assert _verdict(draft) == Verdict.FAIL


def test_environment_error_is_invalid_not_fail() -> None:
    draft = _RunDraft()
    draft.failures.append(_failure(FailureAttribution.ENVIRONMENT_ERROR))
    draft.graders.append(_grader(GraderStatus.PASS))
    assert _verdict(draft) == Verdict.INVALID


def test_grader_error_is_invalid() -> None:
    draft = _RunDraft()
    draft.graders.append(_grader(GraderStatus.ERROR))
    assert _verdict(draft) == Verdict.INVALID

    draft = _RunDraft()
    draft.failures.append(_failure(FailureAttribution.GRADER_ERROR))
    assert _verdict(draft) == Verdict.INVALID


def test_runner_error_is_invalid() -> None:
    draft = _RunDraft()
    draft.failures.append(_failure(FailureAttribution.RUNNER_ERROR))
    assert _verdict(draft) == Verdict.INVALID


def test_case_error_is_invalid() -> None:
    draft = _RunDraft()
    draft.failures.append(_failure(FailureAttribution.CASE_ERROR))
    assert _verdict(draft) == Verdict.INVALID


def test_invalid_beats_agent_failure() -> None:
    draft = _RunDraft()
    draft.failures.append(_failure(FailureAttribution.AGENT_FAILURE))
    draft.failures.append(_failure(FailureAttribution.ENVIRONMENT_ERROR))
    assert _verdict(draft) == Verdict.INVALID
