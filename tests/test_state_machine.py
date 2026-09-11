from __future__ import annotations

import pytest

from reprocase.errors import InvalidTransitionError
from reprocase.results.models import RunState
from reprocase.runner.state_machine import RunStateMachine


def test_happy_path_transitions() -> None:
    machine = RunStateMachine()
    path = [
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
    ]

    for state in path:
        machine.transition(state)

    assert machine.current == RunState.COMPLETED


def test_rejects_skipping_phases() -> None:
    machine = RunStateMachine()

    with pytest.raises(InvalidTransitionError):
        machine.transition(RunState.RUNNING_AGENT)


def test_failure_path_can_still_write_result() -> None:
    machine = RunStateMachine()
    machine.transition(RunState.VALIDATING)
    machine.transition(RunState.FAILED)
    machine.transition(RunState.COLLECTING_ARTIFACTS)
    machine.transition(RunState.TEARING_DOWN)
    machine.transition(RunState.WRITING_RESULT)
    machine.transition(RunState.COMPLETED_WITH_ERROR)

    assert machine.current == RunState.COMPLETED_WITH_ERROR


def test_cancelled_path_can_reach_cancelled_terminal_state() -> None:
    machine = RunStateMachine()
    machine.transition(RunState.VALIDATING)
    machine.transition(RunState.CANCELLED)
    machine.transition(RunState.COLLECTING_ARTIFACTS)
    machine.transition(RunState.TEARING_DOWN)
    machine.transition(RunState.WRITING_RESULT)
    machine.transition(RunState.CANCELLED)

    assert machine.current == RunState.CANCELLED
