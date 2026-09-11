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


class CaseIdentity(StrictOutputModel):
    id: str
    version: str
    digest: str
    path: str


class SourceIdentity(StrictOutputModel):
    python: str
    platform: str


class RunManifest(StrictOutputModel):
    schema_version: Literal["reprocase.run-manifest/v0"] = "reprocase.run-manifest/v0"
    run_id: str
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    case: CaseIdentity
    source: SourceIdentity
    budget: dict[str, int]
    reproducibility_level: Literal["R0"] = "R0"
    components: tuple[Literal["fake_environment", "fake_agent", "fake_grader"], ...] = (
        "fake_environment",
        "fake_agent",
        "fake_grader",
    )


class FailureRecord(StrictOutputModel):
    attribution: FailureAttribution
    code: FailureCode
    message: str
    details: dict[str, Any] = Field(default_factory=dict)


class GraderResult(StrictOutputModel):
    grader_id: str
    status: GraderStatus
    critical: bool
    explanation: str


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
    agent_output: Any = None
    graders: tuple[GraderResult, ...] = ()
    primary_failure: FailureRecord | None = None
    secondary_failures: tuple[FailureRecord, ...] = ()
    warnings: tuple[str, ...] = ()
