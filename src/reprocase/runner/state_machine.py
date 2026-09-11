"""Explicit run lifecycle state machine."""

from __future__ import annotations

from itertools import pairwise

from reprocase.errors import InvalidTransitionError
from reprocase.results.models import RunState

_HAPPY_PATH = (
    RunState.CREATED,
    RunState.VALIDATING,
    RunState.PREPARING_ENVIRONMENT,
    RunState.STARTING_ENVIRONMENT,
    RunState.RESTORING_ENVIRONMENT,
    RunState.HEALTHCHECKING,
    RunState.CAPTURING_INITIAL_STATE,
    RunState.LOADING_AGENT_AND_TOOLS,
    RunState.RUNNING_AGENT,
    RunState.CAPTURING_FINAL_STATE,
    RunState.GRADING,
    RunState.COLLECTING_ARTIFACTS,
    RunState.TEARING_DOWN,
    RunState.WRITING_RESULT,
    RunState.COMPLETED,
)

ALLOWED_TRANSITIONS: dict[RunState, frozenset[RunState]] = {
    current: frozenset({target, RunState.FAILED, RunState.CANCELLED})
    for current, target in pairwise(_HAPPY_PATH)
}
ALLOWED_TRANSITIONS[RunState.FAILED] = frozenset(
    {RunState.COLLECTING_ARTIFACTS, RunState.TEARING_DOWN, RunState.WRITING_RESULT}
)
ALLOWED_TRANSITIONS[RunState.CANCELLED] = frozenset(
    {RunState.COLLECTING_ARTIFACTS, RunState.TEARING_DOWN, RunState.WRITING_RESULT}
)
ALLOWED_TRANSITIONS[RunState.COLLECTING_ARTIFACTS] = frozenset(
    {RunState.TEARING_DOWN, RunState.FAILED, RunState.CANCELLED}
)
ALLOWED_TRANSITIONS[RunState.TEARING_DOWN] = frozenset(
    {RunState.WRITING_RESULT, RunState.FAILED, RunState.CANCELLED}
)
ALLOWED_TRANSITIONS[RunState.WRITING_RESULT] = frozenset(
    {RunState.COMPLETED, RunState.COMPLETED_WITH_ERROR, RunState.CANCELLED}
)
ALLOWED_TRANSITIONS[RunState.COMPLETED] = frozenset()
ALLOWED_TRANSITIONS[RunState.COMPLETED_WITH_ERROR] = frozenset()


class RunStateMachine:
    def __init__(self) -> None:
        self.current = RunState.CREATED

    def transition(self, target: RunState) -> RunState:
        if target not in ALLOWED_TRANSITIONS[self.current]:
            raise InvalidTransitionError(self.current, target)
        self.current = target
        return self.current
