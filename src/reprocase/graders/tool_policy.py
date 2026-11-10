"""Tool policy grader (W2): trajectory rules over the run's events.jsonl.

Data source: ``tool.call.started`` events for forbidden/occurrence rules (a
call that started is a real attempt), ``tool.call.completed`` for required
and ordering rules (the call must have succeeded). ``args_match`` supports
exact values and the ``"*"`` wildcard. ``ordering`` rules require every
completed ``after`` call to be preceded by a completed ``before`` call.
``patterns.no_progress_repeat`` is warning-level: it is recorded on PASS
trajectories too and never changes the verdict.
"""

from __future__ import annotations

import time
from typing import Any

from reprocase.case.digests import canonical_json, sha256_digest
from reprocase.case.models import ToolPolicyGraderSpec
from reprocase.results.models import AssertionResult, GraderResult, GraderStatus


def _args_match(arguments: dict[str, Any], pattern: dict[str, Any]) -> bool:
    for key, expected in pattern.items():
        if expected == "*":
            if key not in arguments:
                return False
        elif arguments.get(key) != expected:
            return False
    return True


def _tool_events(events: tuple[dict[str, Any], ...], event_type: str) -> list[dict[str, Any]]:
    return [event for event in events if event.get("type") == event_type]


def evaluate_tool_policy(
    spec: ToolPolicyGraderSpec,
    *,
    events: tuple[dict[str, Any], ...],
) -> GraderResult:
    started = time.monotonic_ns()
    digest = sha256_digest(spec.model_dump(mode="json"))
    started_events = _tool_events(events, "tool.call.started")
    completed_events = _tool_events(events, "tool.call.completed")

    assertions: list[AssertionResult] = []
    warnings: list[str] = []
    required_evidence: list[Any] = []

    for rule in spec.policy.forbidden:
        matches = [
            event
            for event in started_events
            if event["payload"].get("tool") == rule.tool
            and _args_match(event["payload"].get("arguments", {}), rule.args_match)
        ]
        count = len(matches)
        if count <= rule.max_occurrences:
            assertions.append(
                AssertionResult(
                    path=f"forbidden/{rule.tool}",
                    operator="max_occurrences",
                    status=GraderStatus.PASS,
                    expected={"max_occurrences": rule.max_occurrences},
                    actual={"count": count},
                    explanation=f"{rule.tool} called {count}x (limit {rule.max_occurrences})",
                )
            )
        else:
            first_violation = matches[rule.max_occurrences]
            assertions.append(
                AssertionResult(
                    path=f"forbidden/{rule.tool}",
                    operator="max_occurrences",
                    status=GraderStatus.FAIL,
                    expected={"max_occurrences": rule.max_occurrences},
                    actual={
                        "count": count,
                        "first_violation_sequence": first_violation.get("sequence"),
                    },
                    explanation=(
                        f"{rule.tool} called {count} time(s), exceeding the limit of "
                        f"{rule.max_occurrences}; first violation at event "
                        f"#{first_violation.get('sequence')}"
                    ),
                )
            )

    for required_rule in spec.policy.required:
        # completed events carry no arguments; pair each with the latest
        # started event of the same tool before it to check args_match
        started_by_tool: dict[str, list[dict[str, Any]]] = {}
        for event in started_events:
            started_by_tool.setdefault(str(event["payload"].get("tool")), []).append(event)
        tool_attempts = started_by_tool.get(required_rule.tool, [])
        required_matches: list[dict[str, Any]] = []
        for event in completed_events:
            if event["payload"].get("tool") != required_rule.tool:
                continue
            prior = [
                started
                for started in tool_attempts
                if int(started["sequence"]) < int(event["sequence"])
            ]
            arguments = prior[-1]["payload"].get("arguments", {}) if prior else {}
            if _args_match(arguments, required_rule.args_match):
                required_matches.append(event)
        if required_matches:
            assertions.append(
                AssertionResult(
                    path=f"required/{required_rule.tool}",
                    operator="required",
                    status=GraderStatus.PASS,
                    expected={"tool": required_rule.tool, "args_match": required_rule.args_match},
                    actual={"first_sequence": required_matches[0].get("sequence")},
                    explanation=(
                        f"{required_rule.tool} completed "
                        f"(event #{required_matches[0].get('sequence')})"
                    ),
                )
            )
        else:
            # F3: point at the last attempt of the tool (which never completed),
            # or at the end of the event stream when the tool was never called
            if tool_attempts:
                last_seq = tool_attempts[-1].get("sequence")
                required_actual: dict[str, Any] = {
                    "completed": 0,
                    "last_attempt_sequence": last_seq,
                }
                evidence_ref: Any = f"events.jsonl#sequence={last_seq}"
                detail = f"last attempt at event #{last_seq} never completed"
            else:
                required_actual = {"completed": 0, "last_attempt_sequence": None}
                evidence_ref = "events.jsonl#end"
                detail = "never attempted"
            assertions.append(
                AssertionResult(
                    path=f"required/{required_rule.tool}",
                    operator="required",
                    status=GraderStatus.FAIL,
                    expected={"tool": required_rule.tool, "args_match": required_rule.args_match},
                    actual=required_actual,
                    explanation=(
                        f"{required_rule.tool} never completed with matching arguments ({detail})"
                    ),
                )
            )
            required_evidence.append(evidence_ref)

    for order_rule in spec.policy.ordering:
        before_sequences = [
            int(event["sequence"])
            for event in completed_events
            if event["payload"].get("tool") == order_rule.before
        ]
        after_occurrences = [
            event for event in completed_events if event["payload"].get("tool") == order_rule.after
        ]
        path = f"ordering/{order_rule.before}->{order_rule.after}"
        if not after_occurrences:
            # the anchored obligation never arose: vacuous pass (documented
            # semantics, mirroring temporal_constraint)
            assertions.append(
                AssertionResult(
                    path=path,
                    operator="ordering",
                    status=GraderStatus.PASS,
                    expected={"before": order_rule.before, "after": order_rule.after},
                    actual={"after_completed": 0},
                    explanation=(
                        f"{order_rule.after} never completed; ordering obligation "
                        f"did not arise (vacuous pass)"
                    ),
                )
            )
            continue
        violations = [
            event
            for event in after_occurrences
            if not any(seq < int(event["sequence"]) for seq in before_sequences)
        ]
        if violations:
            first = violations[0]
            assertions.append(
                AssertionResult(
                    path=path,
                    operator="ordering",
                    status=GraderStatus.FAIL,
                    expected={"before": order_rule.before, "after": order_rule.after},
                    actual={
                        "after_completed": len(after_occurrences),
                        "unpreceded": len(violations),
                        "first_violation_sequence": first.get("sequence"),
                    },
                    explanation=(
                        f"{order_rule.after} completed at event #{first.get('sequence')} "
                        f"without a preceding {order_rule.before} completion"
                    ),
                )
            )
        else:
            assertions.append(
                AssertionResult(
                    path=path,
                    operator="ordering",
                    status=GraderStatus.PASS,
                    expected={"before": order_rule.before, "after": order_rule.after},
                    actual={"after_completed": len(after_occurrences)},
                    explanation=(
                        f"every {order_rule.after} completion is preceded by {order_rule.before}"
                    ),
                )
            )

    for pattern in spec.policy.patterns:
        repeat_rule = pattern.no_progress_repeat
        runs: list[tuple[bytes, list[dict[str, Any]]]] = []
        for event in started_events:
            signature = canonical_json(
                {
                    "tool": event["payload"].get("tool"),
                    "arguments": event["payload"].get("arguments", {}),
                }
            )
            if runs and runs[-1][0] == signature:
                runs[-1][1].append(event)
            else:
                runs.append((signature, [event]))
        for _signature, group in runs:
            if len(group) >= repeat_rule.max_identical_calls:
                tool = group[0]["payload"].get("tool")
                warnings.append(
                    f"no_progress_repeat: {tool} called {len(group)}x in a row with "
                    f"identical arguments (events "
                    f"#{group[0].get('sequence')}-#{group[-1].get('sequence')})"
                )

    failures = [a for a in assertions if a.status == GraderStatus.FAIL]
    status = GraderStatus.FAIL if failures else GraderStatus.PASS
    evidence: list[Any] = []
    for assertion in failures:
        seq = assertion.actual.get("first_violation_sequence")
        if seq is not None:
            evidence.append(f"events.jsonl#sequence={seq}")
    evidence.extend(required_evidence)
    if failures:
        first_failure = failures[0]
        explanation = first_failure.explanation
        expected: Any = first_failure.expected
        actual: Any = first_failure.actual
    else:
        explanation = f"{len(assertions)} policy rule(s) satisfied"
        if warnings:
            explanation += f"; {len(warnings)} warning-level pattern(s) recorded"
        expected = None
        actual = None
    return GraderResult(
        grader_id=spec.id,
        status=status,
        critical=spec.critical,
        explanation=explanation,
        grader_type="tool_policy",
        expected=expected,
        actual=actual,
        evidence=tuple(evidence),
        assertions=tuple(assertions),
        warnings=tuple(warnings),
        grader_digest=digest,
        duration_ms=max(0, (time.monotonic_ns() - started) // 1_000_000),
    )
