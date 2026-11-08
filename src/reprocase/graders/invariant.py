"""Invariant grader (W2): whole subtrees must stay unchanged except allow-listed paths.

Implemented on top of the canonical state diff: a rule violation is any diff op
whose path falls inside ``scope`` and outside every ``allow_paths`` prefix
(relative to the scope). Works for dict and array subtrees alike.
"""

from __future__ import annotations

import time
from typing import Any

from reprocase.case.digests import sha256_digest
from reprocase.case.models import InvariantGraderSpec
from reprocase.graders.pointers import MISSING, resolve
from reprocase.recording.diff import compute_diff
from reprocase.results.models import AssertionResult, GraderResult, GraderStatus


def _is_within(path: str, prefix: str) -> bool:
    return path == prefix or path.startswith(prefix + "/")


def evaluate_invariant(
    spec: InvariantGraderSpec,
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
            explanation="final state is unavailable; invariant rules cannot run",
            grader_type="invariant",
            grader_digest=digest,
            duration_ms=duration_ms,
        )

    assertions: list[AssertionResult] = []
    ops = compute_diff(initial_document or {}, final_document)
    failures: list[AssertionResult] = []
    for rule in spec.rules:
        allowed = [rule.scope + "/" + allow for allow in rule.allow_paths]
        scope_missing = resolve(final_document, rule.scope) is MISSING and (
            initial_document is None or resolve(initial_document, rule.scope) is MISSING
        )
        if scope_missing:
            violations: list[str] = []
        else:
            violations = [
                op["path"]
                for op in ops
                if _is_within(str(op["path"]), rule.scope)
                and not any(_is_within(str(op["path"]), allow) for allow in allowed)
            ]
        if not violations:
            assertions.append(
                AssertionResult(
                    path=rule.scope,
                    operator=rule.operator,
                    status=GraderStatus.PASS,
                    expected={"unchanged_except": list(rule.allow_paths)},
                    actual={"changed_paths": []},
                    explanation=f"scope {rule.scope} unchanged",
                )
            )
        else:
            # report every violating path individually (design W2)
            for path in violations:
                assertions.append(
                    AssertionResult(
                        path=path,
                        operator=rule.operator,
                        status=GraderStatus.FAIL,
                        expected={"unchanged_except": list(rule.allow_paths)},
                        actual={"changed": True, "scope": rule.scope},
                        explanation=f"disallowed change at {path} under scope {rule.scope}",
                    )
                )
                failures.append(assertions[-1])

    status = GraderStatus.FAIL if failures else GraderStatus.PASS
    evidence: list[Any] = []
    for assertion in failures:
        evidence.append(f"state.final.json#{assertion.path}")
        evidence.append(f"state.initial.json#{assertion.path}")

    rule_count = len(spec.rules)
    first: AssertionResult | None = failures[0] if failures else None
    if first is None:
        explanation = f"{rule_count}/{rule_count} invariant rules held"
    else:
        explanation = (
            f"invariant violated at {first.path}: disallowed change under {first.actual['scope']}"
        )
    return GraderResult(
        grader_id=spec.id,
        status=status,
        critical=spec.critical,
        explanation=explanation,
        grader_type="invariant",
        expected=first.expected if first else None,
        actual=first.actual if first else None,
        evidence=tuple(evidence),
        assertions=tuple(assertions),
        grader_digest=digest,
        duration_ms=max(0, (time.monotonic_ns() - started) // 1_000_000),
    )
