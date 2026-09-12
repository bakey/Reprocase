from __future__ import annotations

import json

from reprocase.case.models import TestCommandGraderSpec
from reprocase.environments.protocol import CommandResult
from reprocase.graders.test_command import interpret_command_result
from reprocase.results.models import GraderStatus

SPEC = TestCommandGraderSpec(
    id="policy", type="test_command", service="api", command=("python", "-m", "evals.grade")
)


def _result(exit_code: int, stdout: str = "", stderr: str = "") -> CommandResult:
    return CommandResult(exit_code=exit_code, stdout=stdout, stderr=stderr, duration_ms=5)


def test_exit_zero_passed_true() -> None:
    stdout = json.dumps({"passed": True, "explanation": "ok", "evidence": [{"a": 1}]})
    graded = interpret_command_result(SPEC, _result(0, stdout))
    assert graded.status == GraderStatus.PASS
    assert graded.explanation == "ok"
    assert graded.evidence == ({"a": 1},)


def test_exit_zero_passed_false_is_fail_with_structure() -> None:
    stdout = json.dumps(
        {"passed": False, "explanation": "bad", "expected": {"x": 1}, "actual": {"x": 2}}
    )
    graded = interpret_command_result(SPEC, _result(0, stdout))
    assert graded.status == GraderStatus.FAIL
    assert graded.expected == {"x": 1}
    assert graded.actual == {"x": 2}


def test_exit_zero_invalid_output_is_error() -> None:
    graded = interpret_command_result(SPEC, _result(0, "not json"))
    assert graded.status == GraderStatus.ERROR
    missing_field = interpret_command_result(SPEC, _result(0, '{"explanation": "hi"}'))
    assert missing_field.status == GraderStatus.ERROR


def test_exit_one_is_fail_even_without_json() -> None:
    graded = interpret_command_result(SPEC, _result(1, "plain failure"))
    assert graded.status == GraderStatus.FAIL
    structured = interpret_command_result(
        SPEC, _result(1, json.dumps({"passed": False, "explanation": "structured"}))
    )
    assert structured.status == GraderStatus.FAIL
    assert structured.explanation == "structured"


def test_exit_two_to_125_is_error() -> None:
    for code in (2, 42, 125):
        graded = interpret_command_result(SPEC, _result(code, "", "boom"))
        assert graded.status == GraderStatus.ERROR, code
        assert str(code) in graded.explanation


def test_grader_digest_and_type() -> None:
    graded = interpret_command_result(SPEC, _result(0, '{"passed": true}'))
    assert graded.grader_type == "test_command"
    assert graded.grader_digest.startswith("sha256:")
