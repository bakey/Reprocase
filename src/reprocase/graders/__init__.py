"""Deterministic grading: state assertions, test commands, and the all-pass verdict."""

from __future__ import annotations

from reprocase.graders.engine import grader_failure_attribution, run_graders
from reprocase.graders.protocol import ExecFn, Grader, GraderContext
from reprocase.graders.state_assertion import evaluate_state_assertions
from reprocase.graders.test_command import TestCommandGrader, interpret_command_result

__all__ = [
    "ExecFn",
    "Grader",
    "GraderContext",
    "TestCommandGrader",
    "evaluate_state_assertions",
    "grader_failure_attribution",
    "interpret_command_result",
    "run_graders",
]
