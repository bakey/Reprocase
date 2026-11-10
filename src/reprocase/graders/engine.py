"""Grader engine: build graders from case specs and run them sequentially."""

from __future__ import annotations

from typing import Any

from reprocase.case.models import (
    FakeGraderSpec,
    GraderSpec,
    InvariantGraderSpec,
    StateAssertionGraderSpec,
    TemporalConstraintGraderSpec,
    TestCommandGraderSpec,
    ToolPolicyGraderSpec,
)
from reprocase.graders.invariant import evaluate_invariant
from reprocase.graders.protocol import ExecFn, GraderContext
from reprocase.graders.state_assertion import evaluate_state_assertions
from reprocase.graders.temporal import evaluate_temporal_constraints
from reprocase.graders.test_command import TestCommandGrader
from reprocase.graders.tool_policy import evaluate_tool_policy
from reprocase.recording.artifacts import ArtifactStore
from reprocase.recording.events import EventRecorder
from reprocase.results.models import GraderResult, GraderStatus
from reprocase.runner.fakes import FakeGrader


async def run_graders(
    specs: tuple[GraderSpec, ...],
    context: GraderContext,
    *,
    exec_fn: ExecFn,
    artifacts: ArtifactStore,
    recorder: EventRecorder,
) -> list[GraderResult]:
    results: list[GraderResult] = []
    for spec in specs:
        recorder.emit("grader.started", source="grader_engine", payload={"grader_id": spec.id})
        try:
            if isinstance(spec, FakeGraderSpec):
                graded = FakeGrader(spec).grade()
            elif isinstance(spec, StateAssertionGraderSpec):
                graded = evaluate_state_assertions(
                    spec,
                    initial_document=context.initial_document,
                    final_document=context.final_document,
                )
            elif isinstance(spec, TestCommandGraderSpec):
                grader = TestCommandGrader(spec, exec_fn=exec_fn, artifacts=artifacts)
                graded = await grader.grade(context)
            elif isinstance(spec, InvariantGraderSpec):
                graded = evaluate_invariant(
                    spec,
                    initial_document=context.initial_document,
                    final_document=context.final_document,
                )
            elif isinstance(spec, TemporalConstraintGraderSpec):
                graded = evaluate_temporal_constraints(spec, final_document=context.final_document)
            elif isinstance(spec, ToolPolicyGraderSpec):
                graded = evaluate_tool_policy(spec, events=context.events)
            else:  # pragma: no cover - defensive against future spec types
                raise TypeError(f"unsupported grader spec: {type(spec).__name__}")
        except Exception as exc:  # a broken grader is an ERROR, never an agent FAIL
            graded = GraderResult(
                grader_id=spec.id,
                status=GraderStatus.ERROR,
                critical=getattr(spec, "critical", True),
                explanation=f"grader raised {type(exc).__name__}: {exc}",
                grader_type=getattr(spec, "type", "unknown"),
            )
        results.append(graded)
        event_type = "grader.failed" if graded.status == GraderStatus.ERROR else "grader.completed"
        recorder.emit(
            event_type,
            source="grader_engine",
            payload={
                "grader_id": spec.id,
                "status": graded.status,
                "explanation": graded.explanation,
            },
        )
    return results


def grader_failure_attribution(graded: GraderResult) -> dict[str, Any]:
    """Details for the FailureRecord derived from a non-passing grader."""

    return {
        "grader_id": graded.grader_id,
        "grader_type": graded.grader_type,
        "expected": graded.expected,
        "actual": graded.actual,
        "evidence": list(graded.evidence),
    }
