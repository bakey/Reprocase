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
    adapter: Literal["fake", "scripted", "python"]
    config: FakeAgentConfig = Field(default_factory=FakeAgentConfig)
    entrypoint: str | None = Field(default=None, min_length=3, max_length=300)

    @model_validator(mode="after")
    def entrypoint_matches_adapter(self) -> AgentSpec:
        if self.adapter in {"scripted", "python"} and self.entrypoint is None:
            raise ValueError(
                f"{self.adapter} agents require an entrypoint such as agents.refund:run"
            )
        if self.adapter == "fake" and self.entrypoint is not None:
            raise ValueError("fake agents do not accept an entrypoint")
        return self


class ToolSpec(StrictModel):
    provider: str = Field(min_length=3, max_length=300)
    allow: tuple[str, ...] = Field(min_length=1)

    @field_validator("allow")
    @classmethod
    def unique_allowed_tools(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if len(set(value)) != len(value):
            raise ValueError("tools.allow must not contain duplicates")
        for name in value:
            if not re.fullmatch(r"[a-z][a-z0-9_]*\.[a-z][a-z0-9_]*", name):
                raise ValueError(f"invalid tool name: {name!r}")
        return value


class EnvironmentRef(StrictModel):
    driver: Literal["fake", "docker_compose"]
    definition: Path
    compose_file: Path | None = None
    fixture: Annotated[str, Field(min_length=1, max_length=500)] | None = None


class BudgetSpec(StrictModel):
    wall_time_seconds: Annotated[int, Field(ge=1, le=3600)] = 120
    max_tool_calls: Annotated[int, Field(ge=0, le=10_000)] = 20
    tool_timeout_seconds: Annotated[int, Field(ge=1, le=300)] = 30


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


class InvariantRule(StrictModel):
    scope: str = Field(min_length=1, max_length=500, pattern=r"^/")
    operator: Literal["unchanged_except"] = "unchanged_except"
    allow_paths: tuple[str, ...] = ()

    @field_validator("scope")
    @classmethod
    def scope_not_root(cls, value: str) -> str:
        if value == "/":
            raise ValueError(
                "root scope '/' is meaningless for an invariant (it would assert the whole "
                "document is unchanged); to assert no changes at all, assert an empty state "
                "diff instead"
            )
        return value

    @field_validator("allow_paths")
    @classmethod
    def allow_paths_relative(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        for path in value:
            if path.startswith("/"):
                raise ValueError(
                    f"allow_paths entries are relative to the scope: use "
                    f"{path.lstrip('/')!r} relative to the scope, not the absolute {path!r}"
                )
        return value


class InvariantGraderSpec(StrictModel):
    id: str = Field(min_length=1, max_length=128)
    type: Literal["invariant"]
    rules: tuple[InvariantRule, ...] = Field(min_length=1)
    critical: bool = True


class TemporalEventRef(StrictModel):
    event: str = Field(min_length=1, max_length=128)
    entity: str | None = Field(default=None, min_length=1, max_length=128)


class TemporalConstraintRule(StrictModel):
    before: TemporalEventRef | None = None
    after: TemporalEventRef | None = None
    forbidden: TemporalEventRef | None = None

    @model_validator(mode="after")
    def well_formed_constraint(self) -> TemporalConstraintRule:
        if self.before is not None and self.after is not None and self.forbidden is None:
            return self
        if self.forbidden is not None and self.after is not None and self.before is None:
            return self
        raise ValueError(
            "each constraint needs either before+after (ordering) or forbidden+after "
            "(forbidden event must not occur after the anchor)"
        )


class TemporalConstraintGraderSpec(StrictModel):
    id: str = Field(min_length=1, max_length=128)
    type: Literal["temporal_constraint"]
    constraints: tuple[TemporalConstraintRule, ...] = Field(min_length=1)
    critical: bool = True


class ToolPolicyForbiddenRule(StrictModel):
    tool: str = Field(min_length=1, max_length=128)
    args_match: dict[str, Any] = Field(default_factory=dict)
    max_occurrences: Annotated[int, Field(ge=0)] = 0


class ToolPolicyRequiredRule(StrictModel):
    tool: str = Field(min_length=1, max_length=128)
    args_match: dict[str, Any] = Field(default_factory=dict)


class NoProgressRepeatRule(StrictModel):
    max_identical_calls: Annotated[int, Field(ge=2)] = 3
    severity: Literal["warning"] = "warning"


class ToolPolicyPattern(StrictModel):
    no_progress_repeat: NoProgressRepeatRule


class ToolPolicySpec(StrictModel):
    forbidden: tuple[ToolPolicyForbiddenRule, ...] = ()
    required: tuple[ToolPolicyRequiredRule, ...] = ()
    patterns: tuple[ToolPolicyPattern, ...] = ()


class ToolPolicyGraderSpec(StrictModel):
    id: str = Field(min_length=1, max_length=128)
    type: Literal["tool_policy"]
    policy: ToolPolicySpec
    critical: bool = True


type GraderSpec = Annotated[
    FakeGraderSpec
    | StateAssertionGraderSpec
    | TestCommandGraderSpec
    | InvariantGraderSpec
    | TemporalConstraintGraderSpec
    | ToolPolicyGraderSpec,
    Field(discriminator="type"),
]


class RedactionSpec(StrictModel):
    keys: tuple[str, ...] = ("authorization", "api_key", "password", "token", "cookie")


class OutputSpec(StrictModel):
    retain_environment_on_failure: bool = False
    redaction: RedactionSpec = Field(default_factory=RedactionSpec)


class EvalCase(StrictModel):
    api_version: Literal["reprocase.dev/v0"]
    kind: Literal["EvalCase"]
    metadata: Metadata
    task: TaskSpec
    agent: AgentSpec
    environment: EnvironmentRef
    tools: ToolSpec | None = None
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

    @model_validator(mode="after")
    def python_agents_require_tools(self) -> EvalCase:
        if self.agent.adapter == "python" and self.tools is None:
            raise ValueError("python agents require a tools section with provider and allow")
        return self


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


FIXTURE_PLACEHOLDER = "${FIXTURE}"


class RestoreSpec(StrictModel):
    strategy: Literal["command"] = "command"
    service: str = Field(min_length=1, max_length=128)
    command: tuple[str, ...] = Field(min_length=1)
    timeout_seconds: Annotated[int, Field(ge=1, le=600)] = 60
    fixture_default: Annotated[str, Field(min_length=1, max_length=500)] | None = None

    @property
    def references_fixture(self) -> bool:
        """Whether the command contains a ${FIXTURE} placeholder element."""

        return any(FIXTURE_PLACEHOLDER in argument for argument in self.command)


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

    @model_validator(mode="after")
    def fixture_placeholder_only_in_restore(self) -> ComposeEnvironmentDefinition:
        offenders: list[str] = []
        if self.compose.file is not None and FIXTURE_PLACEHOLDER in str(self.compose.file):
            offenders.append("compose.file")
        if FIXTURE_PLACEHOLDER in self.healthcheck.url:
            offenders.append("healthcheck.url")
        if any(FIXTURE_PLACEHOLDER in argument for argument in self.state.capture.command):
            offenders.append("state.capture.command")
        if offenders:
            raise ValueError(
                f"{FIXTURE_PLACEHOLDER} is only substituted in restore.command; "
                f"found in: {', '.join(offenders)}"
            )
        return self


type EnvironmentDefinition = Annotated[
    FakeEnvironmentDefinition | ComposeEnvironmentDefinition,
    Field(discriminator="driver"),
]
