"""Stable exceptions and failure identifiers used by the runner."""

from __future__ import annotations

from enum import StrEnum
from typing import Any


class FailureAttribution(StrEnum):
    CASE_ERROR = "CASE_ERROR"
    AGENT_FAILURE = "AGENT_FAILURE"
    ENVIRONMENT_ERROR = "ENVIRONMENT_ERROR"
    GRADER_ERROR = "GRADER_ERROR"
    RUNNER_ERROR = "RUNNER_ERROR"


class FailureCode(StrEnum):
    CASE_SCHEMA_INVALID = "CASE_SCHEMA_INVALID"
    CASE_REFERENCE_MISSING = "CASE_REFERENCE_MISSING"
    CASE_REFERENCE_OUTSIDE_ROOT = "CASE_REFERENCE_OUTSIDE_ROOT"
    CASE_UNSUPPORTED_DRIVER = "CASE_UNSUPPORTED_DRIVER"
    ENV_FAKE_START_FAILED = "ENV_FAKE_START_FAILED"
    ENV_FAKE_HEALTHCHECK_FAILED = "ENV_FAKE_HEALTHCHECK_FAILED"
    ENV_COMPOSE_UNSAFE = "ENV_COMPOSE_UNSAFE"
    ENV_COMPOSE_INVALID = "ENV_COMPOSE_INVALID"
    ENV_START_FAILED = "ENV_START_FAILED"
    ENV_RESTORE_FAILED = "ENV_RESTORE_FAILED"
    ENV_HEALTHCHECK_FAILED = "ENV_HEALTHCHECK_FAILED"
    ENV_STATE_CAPTURE_FAILED = "ENV_STATE_CAPTURE_FAILED"
    ENV_STATE_INVALID = "ENV_STATE_INVALID"
    ENV_TEARDOWN_FAILED = "ENV_TEARDOWN_FAILED"
    AGENT_FAKE_EXECUTION_FAILED = "AGENT_FAKE_EXECUTION_FAILED"
    GRADER_FAKE_EXECUTION_FAILED = "GRADER_FAKE_EXECUTION_FAILED"
    GRADER_ASSERTION_FAILED = "GRADER_ASSERTION_FAILED"
    RUNNER_INVALID_TRANSITION = "RUNNER_INVALID_TRANSITION"
    RUNNER_STORAGE_ERROR = "RUNNER_STORAGE_ERROR"
    RUNNER_INTERNAL_ERROR = "RUNNER_INTERNAL_ERROR"
    RUN_CANCELLED = "RUN_CANCELLED"


class ReprocaseError(Exception):
    """Base error carrying a stable code and attribution."""

    def __init__(
        self,
        message: str,
        *,
        code: FailureCode,
        attribution: FailureAttribution,
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.code = code
        self.attribution = attribution
        self.details = details or {}


class CaseValidationError(ReprocaseError):
    def __init__(
        self,
        message: str,
        *,
        code: FailureCode = FailureCode.CASE_SCHEMA_INVALID,
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(
            message,
            code=code,
            attribution=FailureAttribution.CASE_ERROR,
            details=details,
        )


class EnvironmentDriverError(ReprocaseError):
    def __init__(
        self,
        message: str,
        *,
        code: FailureCode,
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(
            message,
            code=code,
            attribution=FailureAttribution.ENVIRONMENT_ERROR,
            details=details,
        )


class InvalidTransitionError(ReprocaseError):
    def __init__(self, current: str, target: str) -> None:
        super().__init__(
            f"Invalid run state transition: {current} -> {target}",
            code=FailureCode.RUNNER_INVALID_TRANSITION,
            attribution=FailureAttribution.RUNNER_ERROR,
            details={"current": current, "target": target},
        )
