"""Temporal constraint grader (W2): ordering rules over the final audit_log.

Semantics (documented choice): a ``before``/``after`` constraint is vacuously
satisfied when the ``after`` (obligation) event never happened; when it did
happen but no matching ``before`` (precondition) precedes it, the constraint
FAILs — the obligation was performed without its precondition. A
``forbidden``/``after`` constraint FAILs when any forbidden event follows the
anchor event. Evidence always cites the actual sequence pair from the log.
"""

from __future__ import annotations

import time
from typing import Any

from reprocase.case.digests import sha256_digest
from reprocase.case.models import TemporalConstraintGraderSpec, TemporalEventRef
from reprocase.results.models import AssertionResult, GraderResult, GraderStatus


def _matches(entry: dict[str, Any], ref: TemporalEventRef) -> bool:
    if entry.get("event") != ref.event:
        return False
    return ref.entity is None or entry.get("entity_id") == ref.entity


def _sequences(log: list[dict[str, Any]], ref: TemporalEventRef) -> list[int]:
    # entries are pre-validated by the caller; a malformed one is a grader
    # ERROR naming the entry, never an opaque KeyError
    return [entry["sequence"] for entry in log if _matches(entry, ref)]


def evaluate_temporal_constraints(
    spec: TemporalConstraintGraderSpec,
    *,
    final_document: dict[str, Any] | None,
) -> GraderResult:
    started = time.monotonic_ns()
    digest = sha256_digest(spec.model_dump(mode="json"))

    if final_document is None:
        return GraderResult(
            grader_id=spec.id,
            status=GraderStatus.SKIPPED,
            critical=spec.critical,
            explanation="final state is unavailable; temporal constraints cannot run",
            grader_type="temporal_constraint",
            grader_digest=digest,
            duration_ms=max(0, (time.monotonic_ns() - started) // 1_000_000),
        )
    log = final_document.get("audit_log")
    if not isinstance(log, list):
        return GraderResult(
            grader_id=spec.id,
            status=GraderStatus.ERROR,
            critical=spec.critical,
            explanation="final state has no audit_log list",
            grader_type="temporal_constraint",
            grader_digest=digest,
            duration_ms=max(0, (time.monotonic_ns() - started) // 1_000_000),
        )
    for index, entry in enumerate(log):
        if not isinstance(entry, dict) or not isinstance(entry.get("sequence"), int):
            return GraderResult(
                grader_id=spec.id,
                status=GraderStatus.ERROR,
                critical=spec.critical,
                explanation=(
                    f"audit_log entry #{index} lacks an integer 'sequence': {str(entry)[:200]}"
                ),
                grader_type="temporal_constraint",
                grader_digest=digest,
                duration_ms=max(0, (time.monotonic_ns() - started) // 1_000_000),
            )

    assertions: list[AssertionResult] = []
    notes: list[str] = []
    for index, rule in enumerate(spec.constraints):
        label = f"constraints/{index}"
        assert rule.after is not None  # model validator guarantees
        after_seqs = _sequences(log, rule.after)
        if rule.before is not None:
            before_seqs = _sequences(log, rule.before)
            if not after_seqs:
                notes.append(f"{label}: vacuous — {rule.after.event} never occurred")
                continue
            violated = [seq for seq in after_seqs if not any(b < seq for b in before_seqs)]
            if not violated:
                assertions.append(
                    AssertionResult(
                        path=label,
                        operator="before",
                        status=GraderStatus.PASS,
                        expected={"before": rule.before.event, "after": rule.after.event},
                        actual={"before_seqs": before_seqs, "after_seqs": after_seqs},
                        explanation=f"{rule.before.event} precedes {rule.after.event}",
                    )
                )
            else:
                first_violation = violated[0]
                latest_before = max((b for b in before_seqs if b < first_violation), default=None)
                assertions.append(
                    AssertionResult(
                        path=label,
                        operator="before",
                        status=GraderStatus.FAIL,
                        expected={"before": rule.before.event, "after": rule.after.event},
                        actual={"before_seq": latest_before, "after_seq": first_violation},
                        explanation=(
                            f"{rule.after.event} (seq {first_violation}) happened "
                            f"without a preceding {rule.before.event}"
                        ),
                    )
                )
        else:
            assert rule.forbidden is not None
            if not after_seqs:
                notes.append(f"{label}: vacuous — {rule.after.event} never occurred")
                continue
            anchor = min(after_seqs)
            forbidden_after = [seq for seq in _sequences(log, rule.forbidden) if seq > anchor]
            if not forbidden_after:
                assertions.append(
                    AssertionResult(
                        path=label,
                        operator="forbidden_after",
                        status=GraderStatus.PASS,
                        expected={"forbidden": rule.forbidden.event, "after": rule.after.event},
                        actual={"anchor_seq": anchor, "forbidden_seqs": []},
                        explanation=f"no {rule.forbidden.event} after seq {anchor}",
                    )
                )
            else:
                first_forbidden = forbidden_after[0]
                assertions.append(
                    AssertionResult(
                        path=label,
                        operator="forbidden_after",
                        status=GraderStatus.FAIL,
                        expected={"forbidden": rule.forbidden.event, "after": rule.after.event},
                        actual={"after_seq": anchor, "forbidden_seq": first_forbidden},
                        explanation=(
                            f"{rule.forbidden.event} (seq {first_forbidden}) occurred after "
                            f"{rule.after.event} (seq {anchor})"
                        ),
                    )
                )

    failures = [a for a in assertions if a.status == GraderStatus.FAIL]
    status = GraderStatus.FAIL if failures else GraderStatus.PASS
    evidence: list[Any] = []
    for assertion in failures:
        actual = assertion.actual
        # the violating event first — it is the earliest causal evidence
        for key in ("forbidden_seq", "after_seq", "before_seq"):
            if actual.get(key) is not None:
                evidence.append(f"state.final.json#/audit_log sequence={actual[key]}")

    first_failure: AssertionResult | None = failures[0] if failures else None
    if first_failure is not None:
        explanation = first_failure.explanation
    else:
        explanation = f"{len(assertions)} constraint(s) satisfied"
        if notes:
            explanation += "; " + "; ".join(notes)
    return GraderResult(
        grader_id=spec.id,
        status=status,
        critical=spec.critical,
        explanation=explanation,
        grader_type="temporal_constraint",
        expected=first_failure.expected if first_failure else None,
        actual=first_failure.actual if first_failure else None,
        evidence=tuple(evidence),
        assertions=tuple(assertions),
        grader_digest=digest,
        duration_ms=max(0, (time.monotonic_ns() - started) // 1_000_000),
    )
