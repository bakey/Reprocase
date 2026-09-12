"""State assertion grader (design section 11.3). Pure and side-effect free."""

from __future__ import annotations

import time
from typing import Any

from reprocase.case.digests import sha256_digest
from reprocase.case.models import AssertionSpec, StateAssertionGraderSpec
from reprocase.graders.pointers import MISSING, resolve
from reprocase.results.models import AssertionResult, GraderResult, GraderStatus


def _is_number(value: Any) -> bool:
    return isinstance(value, int | float) and not isinstance(value, bool)


def _compare_eq(actual: Any, expected: Any, tolerance: float | None) -> str | None:
    """Return an error message, or None when the comparison is valid."""

    numeric = _is_number(actual) and _is_number(expected)
    if numeric and (isinstance(actual, float) or isinstance(expected, float)):
        if tolerance is None:
            return "float comparison requires an explicit tolerance"
        if abs(float(actual) - float(expected)) > tolerance:
            return f"{actual} differs from {expected} by more than {tolerance}"
        return None
    if actual != expected:
        return f"{actual!r} != {expected!r}"
    return None


def evaluate_assertion(
    spec: AssertionSpec,
    *,
    final_document: dict[str, Any],
    initial_document: dict[str, Any] | None,
) -> AssertionResult:
    operator = spec.operator
    expected = spec.expected
    final_value = resolve(final_document, spec.path)

    def result(status: GraderStatus, explanation: str, *, actual: Any = None) -> AssertionResult:
        return AssertionResult(
            path=spec.path,
            operator=operator,
            status=status,
            expected=expected,
            actual=actual,
            explanation=explanation,
        )

    if operator in {"unchanged", "changed"}:
        if initial_document is None:
            return result(GraderStatus.ERROR, "initial state is required for this operator")
        initial_value = resolve(initial_document, spec.path)
        both_missing = initial_value is MISSING and final_value is MISSING
        equal = both_missing or initial_value == final_value
        display = {
            "initial": None if initial_value is MISSING else initial_value,
            "final": None if final_value is MISSING else final_value,
        }
        if operator == "unchanged":
            status = GraderStatus.PASS if equal else GraderStatus.FAIL
            return result(status, "value unchanged" if equal else "value changed", actual=display)
        status = GraderStatus.PASS if not equal else GraderStatus.FAIL
        return result(status, "value changed" if not equal else "value unchanged", actual=display)

    if operator == "exists":
        status = GraderStatus.PASS if final_value is not MISSING else GraderStatus.FAIL
        return result(status, "path exists" if final_value is not MISSING else "path not found")

    if operator == "absent":
        status = GraderStatus.PASS if final_value is MISSING else GraderStatus.FAIL
        return result(status, "path absent" if final_value is MISSING else "path exists")

    if final_value is MISSING:
        return result(GraderStatus.FAIL, "path not found in final state")

    if operator == "eq":
        error = _compare_eq(final_value, expected, spec.tolerance)
        status = GraderStatus.PASS if error is None else GraderStatus.FAIL
        if error is not None and "tolerance" in error:
            status = GraderStatus.ERROR
        return result(status, error or "values are equal", actual=final_value)

    if operator == "not_eq":
        error = _compare_eq(final_value, expected, spec.tolerance)
        if error is not None and "tolerance" in error:
            return result(GraderStatus.ERROR, error, actual=final_value)
        status = GraderStatus.PASS if error is not None else GraderStatus.FAIL
        return result(
            status,
            "values differ" if error is not None else f"{final_value!r} == {expected!r}",
            actual=final_value,
        )

    if operator in {"gte", "lte"}:
        if not _is_number(final_value) or not _is_number(expected):
            return result(
                GraderStatus.ERROR,
                f"{operator} requires numeric values, got {final_value!r} and {expected!r}",
                actual=final_value,
            )
        ok = final_value >= expected if operator == "gte" else final_value <= expected
        return result(
            GraderStatus.PASS if ok else GraderStatus.FAIL,
            f"{final_value} {operator} {expected}",
            actual=final_value,
        )

    if operator == "contains":
        if isinstance(final_value, dict):
            ok = expected in final_value
        elif isinstance(final_value, list):
            ok = any(element == expected for element in final_value)
        elif isinstance(final_value, str) and isinstance(expected, str):
            ok = expected in final_value
        else:
            return result(
                GraderStatus.ERROR,
                f"contains is not defined for {type(final_value).__name__}",
                actual=final_value,
            )
        return result(
            GraderStatus.PASS if ok else GraderStatus.FAIL,
            "value found" if ok else f"{expected!r} not contained",
            actual=final_value,
        )

    if operator == "length_eq":
        if not isinstance(expected, int) or isinstance(expected, bool):
            return result(GraderStatus.ERROR, "length_eq expected must be an integer")
        if not isinstance(final_value, list | dict | str):
            return result(
                GraderStatus.ERROR,
                f"length_eq is not defined for {type(final_value).__name__}",
                actual=final_value,
            )
        length = len(final_value)
        return result(
            GraderStatus.PASS if length == expected else GraderStatus.FAIL,
            f"length {length} (expected {expected})",
            actual=length,
        )

    return result(GraderStatus.ERROR, f"unsupported operator: {operator}")


def evaluate_state_assertions(
    spec: StateAssertionGraderSpec,
    *,
    initial_document: dict[str, Any] | None,
    final_document: dict[str, Any] | None,
) -> GraderResult:
    started = time.monotonic_ns()
    digest = sha256_digest(spec.model_dump(mode="json"))
    duration_ms = max(0, (time.monotonic_ns() - started) // 1_000_000)

    if final_document is None:
        return GraderResult(
            grader_id=spec.id,
            status=GraderStatus.SKIPPED,
            critical=spec.critical,
            explanation="final state is unavailable; state assertions cannot run",
            grader_type="state_assertion",
            grader_digest=digest,
            duration_ms=duration_ms,
        )

    assertions = [
        evaluate_assertion(
            assertion,
            final_document=final_document,
            initial_document=initial_document,
        )
        for assertion in spec.assertions
    ]
    failures = [a for a in assertions if a.status == GraderStatus.FAIL]
    errors = [a for a in assertions if a.status == GraderStatus.ERROR]
    if errors:
        status = GraderStatus.ERROR
    elif failures:
        status = GraderStatus.FAIL
    else:
        status = GraderStatus.PASS

    problem = (errors + failures)[:1]
    if status == GraderStatus.PASS:
        explanation = f"{len(assertions)}/{len(assertions)} assertions passed"
        expected: Any = None
        actual: Any = None
    else:
        explanation = (
            f"{len(assertions) - len(failures) - len(errors)}/{len(assertions)} assertions "
            f"passed; first problem at {problem[0].path}: {problem[0].explanation}"
        )
        expected = problem[0].expected
        actual = problem[0].actual

    evidence: list[Any] = []
    for assertion in errors + failures:
        evidence.append(f"state.final.json#{assertion.path}")
        if assertion.operator in {"unchanged", "changed"}:
            evidence.append(f"state.initial.json#{assertion.path}")

    return GraderResult(
        grader_id=spec.id,
        status=status,
        critical=spec.critical,
        explanation=explanation,
        grader_type="state_assertion",
        expected=expected,
        actual=actual,
        evidence=tuple(evidence),
        assertions=tuple(assertions),
        grader_digest=digest,
        duration_ms=max(0, (time.monotonic_ns() - started) // 1_000_000),
    )


class StateAssertionGrader:
    def __init__(self, spec: StateAssertionGraderSpec) -> None:
        self.spec = spec

    async def grade(self, context: Any) -> GraderResult:
        return evaluate_state_assertions(
            self.spec,
            initial_document=context.initial_document,
            final_document=context.final_document,
        )
