"""Grader protocol and shared grading context."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any, Protocol

from reprocase.environments.protocol import CommandResult
from reprocase.results.models import ArtifactRef, GraderResult, StateSnapshot

# exec(service, argv, timeout_seconds) inside the running environment
ExecFn = Callable[[str, list[str], float], Awaitable[CommandResult]]


@dataclass(frozen=True)
class GraderContext:
    run_id: str
    initial_snapshot: StateSnapshot | None
    final_snapshot: StateSnapshot | None
    initial_document: dict[str, Any] | None
    final_document: dict[str, Any] | None
    diff_ref: ArtifactRef | None
    agent_error: str | None
    events: tuple[dict[str, Any], ...] = ()


class Grader(Protocol):
    async def grade(self, context: GraderContext) -> GraderResult: ...
