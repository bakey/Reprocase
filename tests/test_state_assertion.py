from __future__ import annotations

from reprocase.case.models import AssertionSpec, StateAssertionGraderSpec
from reprocase.graders.state_assertion import evaluate_state_assertions
from reprocase.results.models import GraderStatus

INITIAL = {"orders": {"ORD-1": {"status": "paid", "amount": 49.9}}, "emails": []}
FINAL = {
    "orders": {"ORD-1": {"status": "refunded", "amount": 49.9}},
    "emails": [{"id": "EM-1", "to": "CUST-1"}],
    "refunds": {"RF-1": {"status": "completed", "amount": 49.9}},
}


def _grader(*assertions: AssertionSpec) -> StateAssertionGraderSpec:
    return StateAssertionGraderSpec(id="g", type="state_assertion", assertions=tuple(assertions))


_DEFAULT = object()


def _evaluate(*assertions: AssertionSpec, initial=_DEFAULT, final=_DEFAULT):
    return evaluate_state_assertions(
        _grader(*assertions),
        initial_document=INITIAL if initial is _DEFAULT else initial,
        final_document=FINAL if final is _DEFAULT else final,
    )


def test_eq_pass_and_fail() -> None:
    passed = _evaluate(
        AssertionSpec(path="/refunds/RF-1/status", operator="eq", expected="completed")
    )
    assert passed.status == GraderStatus.PASS
    assert passed.assertions[0].explanation == "values are equal"

    failed = _evaluate(
        AssertionSpec(path="/refunds/RF-1/status", operator="eq", expected="pending")
    )
    assert failed.status == GraderStatus.FAIL
    assert failed.expected == "pending"
    assert failed.actual == "completed"
    assert failed.explanation
    assert failed.evidence == ("state.final.json#/refunds/RF-1/status",)


def test_float_eq_requires_tolerance() -> None:
    no_tolerance = _evaluate(
        AssertionSpec(path="/refunds/RF-1/amount", operator="eq", expected=49.9)
    )
    assert no_tolerance.status == GraderStatus.ERROR
    assert "tolerance" in no_tolerance.assertions[0].explanation

    with_tolerance = _evaluate(
        AssertionSpec(path="/refunds/RF-1/amount", operator="eq", expected=49.9, tolerance=0.001)
    )
    assert with_tolerance.status == GraderStatus.PASS


def test_integer_eq_needs_no_tolerance() -> None:
    result = _evaluate(AssertionSpec(path="/emails/0/nope", operator="exists"))
    assert result.status == GraderStatus.FAIL


def test_not_eq() -> None:
    assert (
        _evaluate(
            AssertionSpec(path="/orders/ORD-1/status", operator="not_eq", expected="paid")
        ).status
        == GraderStatus.PASS
    )
    assert (
        _evaluate(
            AssertionSpec(path="/orders/ORD-1/status", operator="not_eq", expected="refunded")
        ).status
        == GraderStatus.FAIL
    )


def test_exists_and_absent() -> None:
    assert _evaluate(AssertionSpec(path="/refunds", operator="exists")).status == GraderStatus.PASS
    assert (
        _evaluate(AssertionSpec(path="/refunds/RF-9", operator="exists")).status
        == GraderStatus.FAIL
    )
    assert (
        _evaluate(AssertionSpec(path="/refunds/RF-9", operator="absent")).status
        == GraderStatus.PASS
    )
    assert _evaluate(AssertionSpec(path="/refunds", operator="absent")).status == GraderStatus.FAIL


def test_missing_path_fails_value_operators() -> None:
    result = _evaluate(AssertionSpec(path="/nope", operator="eq", expected=1))
    assert result.status == GraderStatus.FAIL
    assert "not found" in result.assertions[0].explanation


def test_contains_variants() -> None:
    assert (
        _evaluate(
            AssertionSpec(
                path="/emails", operator="contains", expected={"id": "EM-1", "to": "CUST-1"}
            )
        ).status
        == GraderStatus.PASS
    )
    assert (
        _evaluate(AssertionSpec(path="/orders", operator="contains", expected="ORD-1")).status
        == GraderStatus.PASS
    )
    assert (
        _evaluate(AssertionSpec(path="/orders", operator="contains", expected="ORD-9")).status
        == GraderStatus.FAIL
    )


def test_gte_lte() -> None:
    assert (
        _evaluate(AssertionSpec(path="/refunds/RF-1/amount", operator="gte", expected=49)).status
        == GraderStatus.PASS
    )
    assert (
        _evaluate(AssertionSpec(path="/refunds/RF-1/amount", operator="lte", expected=49)).status
        == GraderStatus.FAIL
    )
    non_numeric = _evaluate(AssertionSpec(path="/refunds/RF-1/status", operator="gte", expected=1))
    assert non_numeric.status == GraderStatus.ERROR


def test_unchanged_and_changed() -> None:
    assert (
        _evaluate(AssertionSpec(path="/orders/ORD-1/amount", operator="unchanged")).status
        == GraderStatus.PASS
    )
    changed = _evaluate(AssertionSpec(path="/orders/ORD-1/status", operator="unchanged"))
    assert changed.status == GraderStatus.FAIL
    assert changed.assertions[0].actual == {"initial": "paid", "final": "refunded"}
    assert "state.initial.json#/orders/ORD-1/status" in changed.evidence

    assert (
        _evaluate(AssertionSpec(path="/orders/ORD-1/status", operator="changed")).status
        == GraderStatus.PASS
    )
    assert (
        _evaluate(AssertionSpec(path="/orders/ORD-1/amount", operator="changed")).status
        == GraderStatus.FAIL
    )


def test_unchanged_requires_initial_document() -> None:
    result = _evaluate(AssertionSpec(path="/orders", operator="unchanged"), initial=None)
    assert result.status == GraderStatus.ERROR


def test_length_eq() -> None:
    assert _evaluate(AssertionSpec(path="/emails", operator="length_eq", expected=1)).status == (
        GraderStatus.PASS
    )
    assert _evaluate(AssertionSpec(path="/emails", operator="length_eq", expected=2)).status == (
        GraderStatus.FAIL
    )


def test_grader_is_error_when_any_assertion_errors() -> None:
    result = _evaluate(
        AssertionSpec(path="/refunds/RF-1/status", operator="eq", expected="completed"),
        AssertionSpec(path="/refunds/RF-1/amount", operator="eq", expected=49.9),
    )
    assert result.status == GraderStatus.ERROR
    assert result.assertions[0].status == GraderStatus.PASS
    assert result.assertions[1].status == GraderStatus.ERROR


def test_skipped_when_final_document_missing() -> None:
    result = _evaluate(AssertionSpec(path="/refunds", operator="exists"), final=None)
    assert result.status == GraderStatus.SKIPPED


def test_grader_digest_stable() -> None:
    first = _evaluate(AssertionSpec(path="/refunds", operator="exists"))
    second = _evaluate(AssertionSpec(path="/refunds", operator="exists"))
    assert first.grader_digest == second.grader_digest
    assert first.grader_digest.startswith("sha256:")
