"""Environment driver lifecycle contracts."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol


@dataclass(frozen=True)
class CommandResult:
    exit_code: int
    stdout: str
    stderr: str
    duration_ms: int


@dataclass(frozen=True)
class HealthResult:
    healthy: bool
    detail: str
    attempts: int


@dataclass(frozen=True)
class CapturedState:
    state: dict[str, Any]
    content_digest: str
    canonicalization_digest: str


@dataclass(frozen=True)
class EnvironmentHandle:
    driver: str
    project_name: str
    compose_file: Path
    env_file: Path
    work_dir: Path
    ports: dict[str, int] = field(default_factory=dict)
    service_urls: dict[str, str] = field(default_factory=dict)


class EnvironmentDriver(Protocol):
    async def prepare(self) -> None: ...

    async def start(self) -> EnvironmentHandle: ...

    async def restore(self, handle: EnvironmentHandle) -> None: ...

    async def healthcheck(self, handle: EnvironmentHandle) -> HealthResult: ...

    async def capture_state(self, handle: EnvironmentHandle) -> CapturedState: ...

    async def exec(
        self,
        handle: EnvironmentHandle,
        service: str,
        argv: list[str],
        timeout_seconds: float,
    ) -> CommandResult: ...

    async def collect_logs(self, handle: EnvironmentHandle) -> list[Path]: ...

    async def teardown(self, handle: EnvironmentHandle) -> None: ...
