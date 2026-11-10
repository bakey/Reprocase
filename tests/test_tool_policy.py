"""Unit tests for the tool_policy grader (events.jsonl trajectory rules)."""

from __future__ import annotations

from typing import Any

from reprocase.case.models import (
    NoProgressRepeatRule,
    ToolPolicyForbiddenRule,
    ToolPolicyGraderSpec,
    ToolPolicyPattern,
    ToolPolicyRequiredRule,
    ToolPolicySpec,
)
from reprocase.graders.tool_policy import evaluate_tool_policy
from reprocase.results.models import GraderStatus


def _event(
    seq: int, event_type: str, tool: str, arguments: dict[str, Any] | None = None
) -> dict[str, Any]:
    return {
        "sequence": seq,
        "type": event_type,
        "payload": {"tool": tool, "arguments": arguments or {}},
    }


def _spec(
    forbidden: tuple[ToolPolicyForbiddenRule, ...] = (),
    required: tuple[ToolPolicyRequiredRule, ...] = (),
    patterns: tuple[ToolPolicyPattern, ...] = (),
) -> ToolPolicyGraderSpec:
    return ToolPolicyGraderSpec(
        id="tp",
        type="tool_policy",
        policy=ToolPolicySpec(forbidden=forbidden, required=required, patterns=patterns),
    )


def test_forbidden_pass_within_limit() -> None:
    events = (
        _event(1, "tool.call.started", "refund.create", {"order_id": "ORD-1"}),
        _event(2, "tool.call.completed", "refund.create"),
    )
    result = evaluate_tool_policy(
        _spec(
            forbidden=(
                ToolPolicyForbiddenRule(
                    tool="refund.create", args_match={"order_id": "*"}, max_occurrences=1
                ),
            )
        ),
        events=events,
    )
    assert result.status == GraderStatus.PASS


def test_forbidden_fail_points_at_first_violation_sequence() -> None:
    events = (
        _event(3, "tool.call.started", "refund.create", {"order_id": "ORD-1"}),
        _event(4, "tool.call.completed", "refund.create"),
        _event(7, "tool.call.started", "refund.create", {"order_id": "ORD-2"}),
        _event(8, "tool.call.completed", "refund.create"),
    )
    result = evaluate_tool_policy(
        _spec(
            forbidden=(
                ToolPolicyForbiddenRule(
                    tool="refund.create", args_match={"order_id": "*"}, max_occurrences=1
                ),
            )
        ),
        events=events,
    )
    assert result.status == GraderStatus.FAIL
    assert result.actual == {"count": 2, "first_violation_sequence": 7}
    assert result.evidence == ("events.jsonl#sequence=7",)


def test_forbidden_args_match_exact_value() -> None:
    events = (
        _event(1, "tool.call.started", "email.send", {"template": "marketing"}),
        _event(2, "tool.call.started", "email.send", {"template": "refund_complete"}),
    )
    result = evaluate_tool_policy(
        _spec(
            forbidden=(
                ToolPolicyForbiddenRule(tool="email.send", args_match={"template": "marketing"}),
            )
        ),
        events=events,
    )
    assert result.status == GraderStatus.FAIL
    assert result.actual["first_violation_sequence"] == 1


def test_required_pass_and_fail() -> None:
    events = (
        _event(1, "tool.call.started", "email.send", {"template": "refund_complete"}),
        _event(2, "tool.call.completed", "email.send"),
    )
    passing = evaluate_tool_policy(
        _spec(
            required=(
                ToolPolicyRequiredRule(
                    tool="email.send", args_match={"template": "refund_complete"}
                ),
            )
        ),
        events=events,
    )
    assert passing.status == GraderStatus.PASS
    assert passing.assertions[0].actual == {"first_sequence": 2}

    failing = evaluate_tool_policy(
        _spec(required=(ToolPolicyRequiredRule(tool="email.send"),)),
        events=(_event(1, "tool.call.started", "order.get"),),
    )
    assert failing.status == GraderStatus.FAIL
    assert failing.actual == {"completed": 0, "last_attempt_sequence": None}
    assert failing.evidence == ("events.jsonl#end",)


def test_required_ignores_calls_that_only_started() -> None:
    events = (_event(1, "tool.call.started", "email.send", {"template": "refund_complete"}),)
    result = evaluate_tool_policy(
        _spec(required=(ToolPolicyRequiredRule(tool="email.send"),)),
        events=events,
    )
    assert result.status == GraderStatus.FAIL


def test_no_progress_repeat_warns_without_failing() -> None:
    events = tuple(
        [
            *[
                _event(i, "tool.call.started", "order.get", {"order_id": "ORD-1"})
                for i in (1, 2, 3)
            ],
            _event(4, "tool.call.started", "refund.create", {"order_id": "ORD-1"}),
        ]
    )
    result = evaluate_tool_policy(
        _spec(patterns=(ToolPolicyPattern(no_progress_repeat=NoProgressRepeatRule()),)),
        events=events,
    )
    assert result.status == GraderStatus.PASS
    assert len(result.warnings) == 1
    assert "order.get" in result.warnings[0]
    assert "#1-#3" in result.warnings[0]


def test_no_progress_repeat_ignores_varied_calls() -> None:
    events = (
        _event(1, "tool.call.started", "order.get", {"order_id": "ORD-1"}),
        _event(2, "tool.call.started", "order.get", {"order_id": "ORD-2"}),
        _event(3, "tool.call.started", "order.get", {"order_id": "ORD-1"}),
    )
    result = evaluate_tool_policy(
        _spec(patterns=(ToolPolicyPattern(no_progress_repeat=NoProgressRepeatRule()),)),
        events=events,
    )
    assert result.warnings == ()


def test_required_fail_points_at_last_attempt() -> None:
    """F3: a required violation cites the last attempt that never completed."""

    events = (
        _event(3, "tool.call.started", "email.send", {"template": "refund_complete"}),
        _event(5, "tool.call.started", "email.send", {"template": "refund_complete"}),
        # neither completed
    )
    result = evaluate_tool_policy(
        _spec(required=(ToolPolicyRequiredRule(tool="email.send"),)),
        events=events,
    )
    assert result.status == GraderStatus.FAIL
    assert result.actual == {"completed": 0, "last_attempt_sequence": 5}
    assert result.evidence == ("events.jsonl#sequence=5",)
    assert "last attempt at event #5" in result.explanation


def test_required_fail_never_attempted_points_at_stream_end() -> None:
    events = (_event(3, "tool.call.started", "order.get", {"order_id": "O"}),)
    result = evaluate_tool_policy(
        _spec(required=(ToolPolicyRequiredRule(tool="email.send"),)),
        events=events,
    )
    assert result.status == GraderStatus.FAIL
    assert result.actual == {"completed": 0, "last_attempt_sequence": None}
    assert result.evidence == ("events.jsonl#end",)
    assert "never attempted" in result.explanation
