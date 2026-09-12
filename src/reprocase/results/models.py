"""Versioned M0 run output contracts."""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from reprocase.errors import FailureAttribution, FailureCode


class StrictOutputModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class RunState(StrEnum):
    CREATED = "CREATED"
    VALIDATING = "VALIDATING"
    PREPARING_ENVIRONMENT = "PREPARING_ENVIRONMENT"
    STARTING_ENVIRONMENT = "STARTING_ENVIRONMENT"
    RESTORING_ENVIRONMENT = "RESTORING_ENVIRONMENT"
    HEALTHCHECKING = "HEALTHCHECKING"
    CAPTURING_INITIAL_STATE = "CAPTURING_INITIAL_STATE"
    LOADING_AGENT_AND_TOOLS = "LOADING_AGENT_AND_TOOLS"
    RUNNING_AGENT = "RUNNING_AGENT"
    CAPTURING_FINAL_STATE = "CAPTURING_FINAL_STATE"
    GRADING = "GRADING"
    COLLECTING_ARTIFACTS = "COLLECTING_ARTIFACTS"
    TEARING_DOWN = "TEARING_DOWN"
    WRITING_RESULT = "WRITING_RESULT"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"
    COMPLETED = "COMPLETED"
    COMPLETED_WITH_ERROR = "COMPLETED_WITH_ERROR"


class RunStatus(StrEnum):
    COMPLETED = "COMPLETED"
    COMPLETED_WITH_ERROR = "COMPLETED_WITH_ERROR"
    CANCELLED = "CANCELLED"


class Verdict(StrEnum):
    PASS = "PASS"
    FAIL = "FAIL"
    INVALID = "INVALID"


class GraderStatus(StrEnum):
    PASS = "PASS"
    FAIL = "FAIL"
    ERROR = "ERROR"
    SKIPPED = "SKIPPED"


class CaseIdentity(StrictOutputModel):
    id: str
    version: str
    digest: str
    path: str


class SourceIdentity(StrictOutputModel):
    python: str
    platform: str
    git_commit: str | None = None
    git_dirty: bool | None = None


class CodeIdentity(StrictOutputModel):
    """Digest of the code behind an entrypoint (relative paths only, §6.2)."""

    path: str
    sha256: str


class AgentIdentity(StrictOutputModel):
    adapter: str
    entrypoint: str | None = None
    code: CodeIdentity | None = None
    provider: str | None = None
    provider_code: CodeIdentity | None = None


class Usage(StrictOutputModel):
    """Observed usage; unknown model metrics stay null, never zero (§13.3)."""

    tool_calls: int = Field(default=0, ge=0)
    model_calls: int | None = Field(default=None, ge=0)
    input_tokens: int | None = Field(default=None, ge=0)
    output_tokens: int | None = Field(default=None, ge=0)
    cost_usd: float | None = Field(default=None, ge=0)


class ArtifactRef(StrictOutputModel):
    media_type: str
    path: str
    size_bytes: int = Field(ge=0)
    sha256: str


class StateSnapshot(StrictOutputModel):
    schema_version: Literal["reprocase.state/v0"] = "reprocase.state/v0"
    captured_at: datetime
    phase: Literal["initial", "final"]
    canonicalization_digest: str
    content_digest: str
    artifact: ArtifactRef


class RunManifest(StrictOutputModel):
    schema_version: Literal["reprocase.run-manifest/v0"] = "reprocase.run-manifest/v0"
    run_id: str
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    case: CaseIdentity
    source: SourceIdentity
    budget: dict[str, int]
    seed: int = 0
    agent: AgentIdentity | None = None
    reproducibility_level: Literal["R0", "R1"] = "R0"
    components: tuple[
        Literal[
            "fake_environment",
            "fake_agent",
            "fake_grader",
            "compose_environment",
            "scripted_actor",
            "deterministic_graders",
            "python_agent",
            "tool_broker",
        ],
        ...,
    ] = (
        "fake_environment",
        "fake_agent",
        "fake_grader",
    )


class FailureRecord(StrictOutputModel):
    attribution: FailureAttribution
    code: FailureCode
    message: str
    details: dict[str, Any] = Field(default_factory=dict)


class AssertionResult(StrictOutputModel):
    path: str
    operator: str
    status: GraderStatus
    expected: Any = None
    actual: Any = None
    explanation: str = ""


class GraderResult(StrictOutputModel):
    grader_id: str
    status: GraderStatus
    critical: bool
    explanation: str
    grader_type: str = "fake"
    score: float | None = None
    expected: Any = None
    actual: Any = None
    evidence: tuple[Any, ...] = ()
    assertions: tuple[AssertionResult, ...] = ()
    grader_digest: str = ""
    duration_ms: int = Field(default=0, ge=0)


class RunResult(StrictOutputModel):
    schema_version: Literal["reprocase.run-result/v0"] = "reprocase.run-result/v0"
    run_id: str
    status: RunStatus
    verdict: Verdict
    final_state: RunState
    case: CaseIdentity
    started_at: datetime
    finished_at: datetime
    duration_ms: int = Field(ge=0)
    reproducibility_level: Literal["R0", "R1"] = "R0"
    agent_output: Any = None
    usage: Usage | None = None
    states: tuple[StateSnapshot, ...] = ()
    state_diff: ArtifactRef | None = None
    graders: tuple[GraderResult, ...] = ()
    artifacts: tuple[ArtifactRef, ...] = ()
    primary_failure: FailureRecord | None = None
    secondary_failures: tuple[FailureRecord, ...] = ()
    warnings: tuple[str, ...] = ()
