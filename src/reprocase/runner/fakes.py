"""Deterministic contract fixtures used only by M0."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from typing import Any

from reprocase.case.models import FakeAgentConfig, FakeEnvironmentConfig, FakeGraderSpec
from reprocase.errors import FailureAttribution, FailureCode, ReprocaseError
from reprocase.results.models import GraderResult, GraderStatus


class FakeEnvironment:
    def __init__(self, config: FakeEnvironmentConfig) -> None:
        self.config = config
        self.started = False
        self.state: dict[str, Any] = {}

    def prepare(self) -> None:
        return None

    def start(self) -> None:
        if self.config.fail_on_start:
            raise ReprocaseError(
                "Fake environment was configured to fail on start",
                code=FailureCode.ENV_FAKE_START_FAILED,
                attribution=FailureAttribution.ENVIRONMENT_ERROR,
            )
        self.started = True

    def restore(self) -> None:
        self.state = deepcopy(self.config.initial_state)

    def healthcheck(self) -> None:
        if not self.started or not self.config.healthy:
            raise ReprocaseError(
                "Fake environment healthcheck failed",
                code=FailureCode.ENV_FAKE_HEALTHCHECK_FAILED,
                attribution=FailureAttribution.ENVIRONMENT_ERROR,
            )

    def capture_state(self) -> dict[str, Any]:
        return deepcopy(self.state)

    def teardown(self) -> None:
        self.started = False


@dataclass(frozen=True)
class FakeAgentResult:
    output: Any


class FakeAgent:
    def __init__(self, config: FakeAgentConfig) -> None:
        self.config = config

    def run(self, task: dict[str, Any]) -> FakeAgentResult:
        if self.config.outcome == "error":
            raise ReprocaseError(
                self.config.message or "Fake agent execution failed",
                code=FailureCode.AGENT_FAKE_EXECUTION_FAILED,
                attribution=FailureAttribution.AGENT_FAILURE,
                details={"task_keys": sorted(task)},
            )
        return FakeAgentResult(output=deepcopy(self.config.output))


class FakeGrader:
    def __init__(self, spec: FakeGraderSpec) -> None:
        self.spec = spec

    def grade(self) -> GraderResult:
        return GraderResult(
            grader_id=self.spec.id,
            status=GraderStatus(self.spec.outcome),
            critical=self.spec.critical,
            explanation=self.spec.explanation,
        )
