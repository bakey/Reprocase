"""Versioned M0 input contracts."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

CASE_ID_PATTERN = re.compile(r"^[a-z0-9][a-z0-9._-]{2,127}$")
SEMVER_PATTERN = re.compile(
    r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)"
    r"(?:-[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*)?"
    r"(?:\+[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*)?$"
)


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class Metadata(StrictModel):
    id: str
    version: str
    title: str = Field(min_length=1, max_length=200)
    tags: tuple[str, ...] = ()

    @field_validator("id")
    @classmethod
    def valid_id(cls, value: str) -> str:
        if not CASE_ID_PATTERN.fullmatch(value):
            raise ValueError("must match [a-z0-9][a-z0-9._-]{2,127}")
        return value

    @field_validator("version")
    @classmethod
    def valid_version(cls, value: str) -> str:
        if not SEMVER_PATTERN.fullmatch(value):
            raise ValueError("must be a semantic version such as 0.1.0")
        return value

    @field_validator("tags")
    @classmethod
    def unique_tags(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if len(set(value)) != len(value):
            raise ValueError("must not contain duplicate tags")
        return value


class TaskSpec(StrictModel):
    input: dict[str, Any]
    context: dict[str, Any] = Field(default_factory=dict)


class FakeAgentConfig(StrictModel):
    outcome: Literal["success", "error"] = "success"
    output: Any = None
    message: str | None = None


class AgentSpec(StrictModel):
    adapter: Literal["fake", "scripted"]
    config: FakeAgentConfig = Field(default_factory=FakeAgentConfig)
    entrypoint: str | None = Field(default=None, min_length=3, max_length=300)

    @model_validator(mode="after")
    def entrypoint_matches_adapter(self) -> AgentSpec:
        if self.adapter == "scripted" and self.entrypoint is None:
            raise ValueError("scripted agents require an entrypoint such as agents.refund:run")
        if self.adapter == "fake" and self.entrypoint is not None:
            raise ValueError("fake agents do not accept an entrypoint")
        return self


class EnvironmentRef(StrictModel):
    driver: Literal["fake", "docker_compose"]
    definition: Path
    compose_file: Path | None = None


class BudgetSpec(StrictModel):
    wall_time_seconds: Annotated[int, Field(ge=1, le=3600)] = 120
    max_tool_calls: Annotated[int, Field(ge=0, le=10_000)] = 20


class FakeGraderSpec(StrictModel):
    id: str = Field(min_length=1, max_length=128)
    type: Literal["fake"]
    outcome: Literal["PASS", "FAIL", "ERROR"] = "PASS"
    explanation: str = Field(min_length=1, max_length=2_000)
    critical: bool = True


type AssertionOperator = Literal[
    "eq",
    "not_eq",
    "exists",
    "absent",
    "contains",
    "gte",
    "lte",
    "unchanged",
    "changed",
    "length_eq",
]


class AssertionSpec(StrictModel):
    path: str = Field(min_length=1, max_length=500)
    operator: AssertionOperator
    expected: Any = None
    tolerance: Annotated[float, Field(gt=0)] | None = None


class StateAssertionGraderSpec(StrictModel):
    id: str = Field(min_length=1, max_length=128)
    type: Literal["state_assertion"]
    assertions: tuple[AssertionSpec, ...] = Field(min_length=1)
    critical: bool = True


class TestCommandGraderSpec(StrictModel):
    __test__ = False  # not a pytest test class despite the name

    id: str = Field(min_length=1, max_length=128)
    type: Literal["test_command"]
    service: str = Field(min_length=1, max_length=128)
    command: tuple[str, ...] = Field(min_length=1)
    timeout_seconds: Annotated[int, Field(ge=1, le=600)] = 30
    critical: bool = True


type GraderSpec = Annotated[
    FakeGraderSpec | StateAssertionGraderSpec | TestCommandGraderSpec,
    Field(discriminator="type"),
]


class OutputSpec(StrictModel):
    retain_environment_on_failure: bool = False


class EvalCase(StrictModel):
    api_version: Literal["reprocase.dev/v0"]
    kind: Literal["EvalCase"]
    metadata: Metadata
    task: TaskSpec
    agent: AgentSpec
    environment: EnvironmentRef
    budgets: BudgetSpec = Field(default_factory=BudgetSpec)
    graders: tuple[GraderSpec, ...] = Field(min_length=1)
    output: OutputSpec = Field(default_factory=OutputSpec)

    @field_validator("graders")
    @classmethod
    def unique_grader_ids(cls, value: tuple[GraderSpec, ...]) -> tuple[GraderSpec, ...]:
        ids = [grader.id for grader in value]
        if len(set(ids)) != len(ids):
            raise ValueError("grader ids must be unique")
        return value


class FakeEnvironmentConfig(StrictModel):
    healthy: bool = True
    fail_on_start: bool = False
    initial_state: dict[str, Any] = Field(default_factory=dict)


class ComposeSpec(StrictModel):
    file: Path | None = None
    project_prefix: str = Field(default="reprocase", pattern=r"^[a-z0-9][a-z0-9_-]{0,31}$")
    startup_timeout_seconds: Annotated[int, Field(ge=1, le=600)] = 60
    port_variables: dict[str, Annotated[int, Field(ge=1, le=65535)]] = Field(default_factory=dict)

    @field_validator("port_variables")
    @classmethod
    def valid_port_variables(cls, value: dict[str, int]) -> dict[str, int]:
        for name in value:
            if not re.fullmatch(r"[A-Z][A-Z0-9_]*", name):
                raise ValueError("port variable names must be upper-case environment names")
        return value


class RestoreSpec(StrictModel):
    strategy: Literal["command"] = "command"
    service: str = Field(min_length=1, max_length=128)
    command: tuple[str, ...] = Field(min_length=1)
    timeout_seconds: Annotated[int, Field(ge=1, le=600)] = 60


class HealthcheckSpec(StrictModel):
    type: Literal["http"] = "http"
    url: str = Field(min_length=1, max_length=500)
    timeout_seconds: Annotated[int, Field(ge=1, le=600)] = 30
    interval_seconds: Annotated[float, Field(gt=0, le=30)] = 0.5


class StateCaptureSpec(StrictModel):
    service: str = Field(min_length=1, max_length=128)
    command: tuple[str, ...] = Field(min_length=1)
    format: Literal["json"] = "json"
    timeout_seconds: Annotated[int, Field(ge=1, le=600)] = 60


class CanonicalizationSpec(StrictModel):
    remove_paths: tuple[str, ...] = ()
    sort_arrays_by: dict[str, str] = Field(default_factory=dict)


class StateSpec(StrictModel):
    capture: StateCaptureSpec
    canonicalization: CanonicalizationSpec = Field(default_factory=CanonicalizationSpec)


class FakeEnvironmentDefinition(StrictModel):
    api_version: Literal["reprocase.dev/environment/v0"]
    driver: Literal["fake"]
    config: FakeEnvironmentConfig = Field(default_factory=FakeEnvironmentConfig)


class ComposeEnvironmentDefinition(StrictModel):
    api_version: Literal["reprocase.dev/environment/v0"]
    driver: Literal["docker_compose"]
    compose: ComposeSpec = Field(default_factory=ComposeSpec)
    restore: RestoreSpec
    healthcheck: HealthcheckSpec
    state: StateSpec


type EnvironmentDefinition = Annotated[
    FakeEnvironmentDefinition | ComposeEnvironmentDefinition,
    Field(discriminator="driver"),
]
