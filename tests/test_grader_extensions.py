from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from reprocase.case.loader import load_case
from reprocase.case.models import (
    InvariantGraderSpec,
    InvariantRule,
    TemporalConstraintGraderSpec,
    TemporalConstraintRule,
    TemporalEventRef,
)
from reprocase.graders.invariant import evaluate_invariant
from reprocase.graders.temporal import evaluate_temporal_constraints
from reprocase.results.models import GraderStatus

INITIAL = {
    "orders": {"ORD-1": {"status": "paid"}, "ORD-2": {"status": "paid"}},
    "emails": [],
    "refunds": [],
}


def _invariant(*rules: InvariantRule) -> InvariantGraderSpec:
    return InvariantGraderSpec(id="inv", type="invariant", rules=tuple(rules))


def test_invariant_pass_when_nothing_changed() -> None:
    result = evaluate_invariant(
        _invariant(InvariantRule(scope="/orders")),
        initial_document=INITIAL,
        final_document={**INITIAL, "orders": dict(INITIAL["orders"])},
    )
    assert result.status == GraderStatus.PASS
    assert result.grader_type == "invariant"
    assert result.grader_digest.startswith("sha256:")


def test_invariant_allow_path_exempts_target_entity() -> None:
    final = {
        **INITIAL,
        "orders": {"ORD-1": {"status": "refunded"}, "ORD-2": {"status": "paid"}},
    }
    result = evaluate_invariant(
        _invariant(InvariantRule(scope="/orders", allow_paths=("ORD-1",))),
        initial_document=INITIAL,
        final_document=final,
    )
    assert result.status == GraderStatus.PASS


def test_invariant_reports_each_violating_path() -> None:
    final = {
        **INITIAL,
        "orders": {"ORD-1": {"status": "refunded"}, "ORD-2": {"status": "cancelled"}},
    }
    result = evaluate_invariant(
        _invariant(InvariantRule(scope="/orders", allow_paths=("ORD-1",))),
        initial_document=INITIAL,
        final_document=final,
    )
    assert result.status == GraderStatus.FAIL
    assert [a.path for a in result.assertions if a.status == GraderStatus.FAIL] == [
        "/orders/ORD-2/status"
    ]
    assert result.expected == {"unchanged_except": ["ORD-1"]}
    assert "state.initial.json#/orders/ORD-2/status" in result.evidence
    assert "state.final.json#/orders/ORD-2/status" in result.evidence


def test_invariant_covers_array_scope_and_nested_allow() -> None:
    initial = {"log": [{"seq": 1, "event": "a"}]}
    final = {"log": [{"seq": 1, "event": "a"}, {"seq": 2, "event": "b"}]}
    blocked = evaluate_invariant(
        _invariant(InvariantRule(scope="/log")),
        initial_document=initial,
        final_document=final,
    )
    assert blocked.status == GraderStatus.FAIL
    allowed = evaluate_invariant(
        _invariant(InvariantRule(scope="/log", allow_paths=("1",))),
        initial_document=initial,
        final_document=final,
    )
    assert allowed.status == GraderStatus.PASS


def test_invariant_skipped_without_final_state() -> None:
    result = evaluate_invariant(
        _invariant(InvariantRule(scope="/orders")),
        initial_document=INITIAL,
        final_document=None,
    )
    assert result.status == GraderStatus.SKIPPED


LOG = [
    {"sequence": 1, "event": "escalation_created", "entity_id": "ESC-1"},
    {"sequence": 2, "event": "refund_created", "entity_id": "RF-1"},
    {"sequence": 3, "event": "email_queued", "entity_id": "EM-1"},
]


def _temporal(*rules: TemporalConstraintRule) -> TemporalConstraintGraderSpec:
    return TemporalConstraintGraderSpec(
        id="temp", type="temporal_constraint", constraints=tuple(rules)
    )


def test_temporal_before_after_pass() -> None:
    result = evaluate_temporal_constraints(
        _temporal(
            TemporalConstraintRule(
                before=TemporalEventRef(event="refund_created", entity="RF-1"),
                after=TemporalEventRef(event="email_queued", entity="EM-1"),
            )
        ),
        final_document={"audit_log": LOG},
    )
    assert result.status == GraderStatus.PASS
    assert result.assertions[0].actual == {"before_seqs": [2], "after_seqs": [3]}


def test_temporal_before_missing_is_fail_when_obligation_happened() -> None:
    result = evaluate_temporal_constraints(
        _temporal(
            TemporalConstraintRule(
                before=TemporalEventRef(event="approval_approved"),
                after=TemporalEventRef(event="refund_created"),
            )
        ),
        final_document={"audit_log": LOG},
    )
    assert result.status == GraderStatus.FAIL
    assert result.actual == {"before_seq": None, "after_seq": 2}
    assert result.evidence == ("state.final.json#/audit_log sequence=2",)


def test_temporal_vacuous_pass_when_obligation_never_happened() -> None:
    result = evaluate_temporal_constraints(
        _temporal(
            TemporalConstraintRule(
                before=TemporalEventRef(event="approval_approved"),
                after=TemporalEventRef(event="refund_created", entity="RF-999"),
            )
        ),
        final_document={"audit_log": LOG},
    )
    assert result.status == GraderStatus.PASS
    assert "vacuous" in result.explanation


def test_temporal_forbidden_after_anchor() -> None:
    result = evaluate_temporal_constraints(
        _temporal(
            TemporalConstraintRule(
                forbidden=TemporalEventRef(event="email_queued"),
                after=TemporalEventRef(event="refund_created"),
            )
        ),
        final_document={"audit_log": LOG},
    )
    assert result.status == GraderStatus.FAIL
    assert result.actual == {"after_seq": 2, "forbidden_seq": 3}
    assert "sequence=3" in result.evidence[0]

    clean = evaluate_temporal_constraints(
        _temporal(
            TemporalConstraintRule(
                forbidden=TemporalEventRef(event="email_queued"),
                after=TemporalEventRef(event="escalation_created"),
            )
        ),
        final_document={"audit_log": LOG[:2]},
    )
    assert clean.status == GraderStatus.PASS


def test_temporal_error_without_audit_log_and_skipped_without_final() -> None:
    missing = evaluate_temporal_constraints(
        _temporal(
            TemporalConstraintRule(
                before=TemporalEventRef(event="a"), after=TemporalEventRef(event="b")
            )
        ),
        final_document={"no_log": True},
    )
    assert missing.status == GraderStatus.ERROR
    skipped = evaluate_temporal_constraints(
        _temporal(
            TemporalConstraintRule(
                before=TemporalEventRef(event="a"), after=TemporalEventRef(event="b")
            )
        ),
        final_document=None,
    )
    assert skipped.status == GraderStatus.SKIPPED


def test_validate_accepts_new_grader_types(tmp_path: Path) -> None:
    (tmp_path / "environment.yaml").write_text(
        "api_version: reprocase.dev/environment/v0\ndriver: fake\n",
        encoding="utf-8",
    )
    (tmp_path / "case.yaml").write_text(
        """\
api_version: reprocase.dev/v0
kind: EvalCase
metadata:
  id: extended.grader.case
  version: 0.1.0
  title: New grader types
task:
  input: {}
agent:
  adapter: fake
environment:
  driver: fake
  definition: environment.yaml
graders:
  - id: inv
    type: invariant
    rules:
      - scope: /orders
        operator: unchanged_except
        allow_paths: ["ORD-1"]
  - id: temp
    type: temporal_constraint
    constraints:
      - before: {event: refund_created}
        after: {event: email_queued}
  - id: tp
    type: tool_policy
    policy:
      forbidden:
        - tool: refund.create
          args_match: {order_id: "*"}
          max_occurrences: 1
      required:
        - tool: email.send
          args_match: {template: refund_complete}
      patterns:
        - no_progress_repeat:
            max_identical_calls: 3
""",
        encoding="utf-8",
    )
    loaded = load_case(tmp_path / "case.yaml")
    assert [g.type for g in loaded.case.graders] == [
        "invariant",
        "temporal_constraint",
        "tool_policy",
    ]


def test_temporal_constraint_shape_validated(tmp_path: Path) -> None:
    with pytest.raises(ValidationError):
        TemporalConstraintRule(before=TemporalEventRef(event="a"))  # missing after
    with pytest.raises(ValidationError):
        TemporalConstraintRule(
            before=TemporalEventRef(event="a"),
            forbidden=TemporalEventRef(event="b"),
            after=TemporalEventRef(event="c"),
        )


def test_root_scope_rejected_by_model() -> None:
    with pytest.raises(ValidationError, match="root scope"):
        InvariantRule(scope="/")
    # normal scopes still pass
    assert InvariantRule(scope="/orders").scope == "/orders"


def test_root_scope_matcher_fails_closed() -> None:
    """F1 defense in depth: even with validation bypassed, '/' matches all paths."""

    from reprocase.graders.invariant import _is_within

    assert _is_within("/orders/ORD-1", "/")
    assert _is_within("/anything", "/")
    assert not _is_within("/orders/ORD-1", "/orders/ORD-2")

    # constructed bypassing validation: a change anywhere must be caught
    rule = InvariantRule.model_construct(scope="/")
    spec = InvariantGraderSpec.model_construct(
        id="inv", type="invariant", rules=(rule,), critical=True
    )
    result = evaluate_invariant(
        spec,
        initial_document={"a": 1},
        final_document={"a": 2},
    )
    assert result.status == GraderStatus.FAIL


def test_absolute_allow_paths_rejected_by_model() -> None:
    with pytest.raises(ValidationError, match="relative to the scope"):
        InvariantRule(scope="/orders", allow_paths=("/orders/ORD-1",))
    assert InvariantRule(scope="/orders", allow_paths=("ORD-1",)).allow_paths == ("ORD-1",)


def test_absolute_allow_paths_normalized_as_defense() -> None:
    """F2 defense in depth: a bypassed absolute entry still matches after normalization."""

    from reprocase.graders.invariant import _join_scope

    assert _join_scope("/orders", "/ORD-1") == "/orders/ORD-1"
    assert _join_scope("/orders/", "ORD-1") == "/orders/ORD-1"

    rule = InvariantRule.model_construct(scope="/orders", allow_paths=("/ORD-1",))
    spec = InvariantGraderSpec.model_construct(
        id="inv", type="invariant", rules=(rule,), critical=True
    )
    result = evaluate_invariant(
        spec,
        initial_document={"orders": {"ORD-1": {"status": "paid"}}},
        final_document={"orders": {"ORD-1": {"status": "refunded"}}},
    )
    assert result.status == GraderStatus.PASS


def test_validate_warns_when_fake_case_uses_stateful_graders(tmp_path: Path) -> None:
    from typer.testing import CliRunner

    from reprocase.cli import app

    (tmp_path / "environment.yaml").write_text(
        "api_version: reprocase.dev/environment/v0\ndriver: fake\n",
        encoding="utf-8",
    )
    (tmp_path / "case.yaml").write_text(
        """\
api_version: reprocase.dev/v0
kind: EvalCase
metadata:
  id: fake.with.invariant
  version: 0.1.0
  title: Fake case with stateful grader
task:
  input: {}
agent:
  adapter: fake
environment:
  driver: fake
  definition: environment.yaml
graders:
  - id: inv
    type: invariant
    rules:
      - scope: /orders
""",
        encoding="utf-8",
    )
    result = CliRunner().invoke(app, ["validate", str(tmp_path / "case.yaml")])
    assert result.exit_code == 0
    assert "WARNING" in result.stdout
    assert "inv" in result.stdout


def test_temporal_malformed_entry_names_it() -> None:
    spec = TemporalConstraintGraderSpec(
        id="temp",
        type="temporal_constraint",
        constraints=(
            TemporalConstraintRule(
                before=TemporalEventRef(event="a"), after=TemporalEventRef(event="b")
            ),
        ),
    )
    result = evaluate_temporal_constraints(
        spec,
        final_document={"audit_log": [{"event": "a", "entity_id": "x"}]},
    )
    # engine-level: the malformed entry produces an ERROR whose message names it
    assert result.status == GraderStatus.ERROR
    assert "#0" in result.explanation
    assert "sequence" in result.explanation
