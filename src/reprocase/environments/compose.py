"""Docker Compose environment driver (design section 7).

All Docker interaction goes through the ``docker compose`` CLI as argv arrays;
nothing is executed through a shell.
"""

from __future__ import annotations

import asyncio
import re
import socket
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

import yaml

from reprocase.case.models import ComposeEnvironmentDefinition
from reprocase.environments.protocol import (
    CapturedState,
    CommandResult,
    EnvironmentHandle,
    HealthResult,
)
from reprocase.environments.safety import parse_rendered_config, require_safe_compose
from reprocase.environments.state import canonicalize_state
from reprocase.environments.subprocesses import CommandTimeoutError, run_argv
from reprocase.errors import CaseValidationError, EnvironmentDriverError, FailureCode

_PORT_REFERENCE = re.compile(r"\$\{([A-Z][A-Z0-9_]*)\}")
_SLUG_CLEANUP = re.compile(r"[^a-z0-9]+")


def make_project_name(prefix: str, slug: str, run_suffix: str) -> str:
    cleaned = _SLUG_CLEANUP.sub("_", slug.lower()).strip("_") or "case"
    return f"{prefix}_{cleaned[:32]}_{run_suffix}"


def resolve_compose_file(definition: ComposeEnvironmentDefinition, base: Path) -> Path:
    if definition.compose.file is None:
        raise CaseValidationError(
            "No compose file referenced: set environment.compose_file in the EvalCase "
            "or compose.file in environment.yaml",
            code=FailureCode.CASE_REFERENCE_MISSING,
            details={"environment": str(base)},
        )
    compose_file = (base / definition.compose.file).resolve()
    if not compose_file.is_file():
        raise CaseValidationError(
            f"Referenced file does not exist: {compose_file}",
            code=FailureCode.CASE_REFERENCE_MISSING,
            details={"path": str(compose_file)},
        )
    return compose_file


def _allocate_host_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _stderr_tail(result: CommandResult, limit: int = 2000) -> str:
    return result.stderr.strip()[-limit:]


class ComposeEnvironmentDriver:
    def __init__(
        self,
        definition: ComposeEnvironmentDefinition,
        *,
        compose_file: Path,
        slug: str,
        run_suffix: str,
        work_root: Path,
        allow_unsafe: bool = False,
    ) -> None:
        self.definition = definition
        self.compose_file = compose_file.resolve()
        self.project_name = make_project_name(definition.compose.project_prefix, slug, run_suffix)
        self.work_dir = work_root.resolve() / self.project_name
        self.env_file = self.work_dir / "compose.env"
        self.allow_unsafe = allow_unsafe
        self._ports: dict[str, int] = {}
        self._service_urls: dict[str, str] = {}
        self._base_argv = [
            "docker",
            "compose",
            "-p",
            self.project_name,
            "--env-file",
            str(self.env_file),
            "-f",
            str(self.compose_file),
        ]

    @property
    def ports(self) -> dict[str, int]:
        return dict(self._ports)

    @property
    def service_urls(self) -> dict[str, str]:
        return dict(self._service_urls)

    async def _compose(
        self,
        args: list[str],
        *,
        timeout_seconds: float,
        timeout_code: FailureCode = FailureCode.ENV_COMPOSE_INVALID,
    ) -> CommandResult:
        try:
            return await run_argv(self._base_argv + args, timeout_seconds=timeout_seconds)
        except FileNotFoundError as exc:
            raise EnvironmentDriverError(
                "docker CLI is not available on PATH",
                code=FailureCode.ENV_COMPOSE_INVALID,
            ) from exc
        except CommandTimeoutError as exc:
            raise EnvironmentDriverError(
                f"docker compose {args[0]} timed out after {exc.timeout_seconds}s",
                code=timeout_code,
                details={"args": args, "compose_project": self.project_name},
            ) from exc

    def _substitute_ports(self, template: str) -> str:
        def replace(match: re.Match[str]) -> str:
            name = match.group(1)
            if name not in self._ports:
                raise EnvironmentDriverError(
                    f"URL references unknown port variable {name!r}: {template}",
                    code=FailureCode.ENV_COMPOSE_INVALID,
                    details={"url": template, "variable": name},
                )
            return str(self._ports[name])

        return _PORT_REFERENCE.sub(replace, template)

    async def prepare(self) -> None:
        self.work_dir.mkdir(parents=True, exist_ok=True)
        self._ports = {
            name: _allocate_host_port() for name in self.definition.compose.port_variables
        }
        self.env_file.write_text(
            "".join(f"{name}={port}\n" for name, port in sorted(self._ports.items())),
            encoding="utf-8",
        )
        # Fail fast if the healthcheck URL references undeclared variables.
        self._substitute_ports(self.definition.healthcheck.url)

        try:
            raw_compose = yaml.safe_load(self.compose_file.read_text(encoding="utf-8"))
        except (OSError, yaml.YAMLError) as exc:
            raise EnvironmentDriverError(
                f"Cannot read compose file {self.compose_file}: {exc}",
                code=FailureCode.ENV_COMPOSE_INVALID,
                details={"compose_file": str(self.compose_file)},
            ) from exc
        if not isinstance(raw_compose, dict):
            raise EnvironmentDriverError(
                f"Compose file is not a YAML mapping: {self.compose_file}",
                code=FailureCode.ENV_COMPOSE_INVALID,
                details={"compose_file": str(self.compose_file)},
            )

        config = await self._compose(
            ["config", "--format", "json"],
            timeout_seconds=60.0,
        )
        if config.exit_code != 0:
            raise EnvironmentDriverError(
                f"docker compose config failed: {_stderr_tail(config)}",
                code=FailureCode.ENV_COMPOSE_INVALID,
                details={"compose_file": str(self.compose_file)},
            )
        rendered = parse_rendered_config(config.stdout, compose_file=self.compose_file)
        if not self.allow_unsafe:
            require_safe_compose(rendered, raw_compose=raw_compose)
        self._service_urls = self._discover_service_urls(rendered)

    def _discover_service_urls(self, rendered: dict[str, Any]) -> dict[str, str]:
        """Map services to host URLs by matching published container ports."""

        container_ports = set(self.definition.compose.port_variables.values())
        urls: dict[str, str] = {}
        services = rendered.get("services") or {}
        for name, service in services.items():
            for published in service.get("ports") or []:
                if not isinstance(published, dict):
                    continue
                target = published.get("target")
                if target not in container_ports:
                    continue
                variable = next(
                    key
                    for key, value in self.definition.compose.port_variables.items()
                    if value == target
                )
                host_port = self._ports.get(variable)
                if host_port is not None:
                    urls[str(name)] = f"http://127.0.0.1:{host_port}"
        return urls

    async def start(self) -> EnvironmentHandle:
        timeout = float(self.definition.compose.startup_timeout_seconds) + 30.0
        result = await self._compose(
            ["up", "-d", "--wait", "--build"],
            timeout_seconds=timeout,
            timeout_code=FailureCode.ENV_START_FAILED,
        )
        if result.exit_code != 0:
            raise EnvironmentDriverError(
                f"docker compose up failed: {_stderr_tail(result)}",
                code=FailureCode.ENV_START_FAILED,
                details={"compose_project": self.project_name},
            )
        return EnvironmentHandle(
            driver="docker_compose",
            project_name=self.project_name,
            compose_file=self.compose_file,
            env_file=self.env_file,
            work_dir=self.work_dir,
            ports=dict(self._ports),
            service_urls=dict(self._service_urls),
        )

    async def exec(
        self,
        handle: EnvironmentHandle,
        service: str,
        argv: list[str],
        timeout_seconds: float,
        timeout_code: FailureCode = FailureCode.ENV_COMPOSE_INVALID,
    ) -> CommandResult:
        return await self._compose(
            ["exec", "-T", service, *argv],
            timeout_seconds=timeout_seconds,
            timeout_code=timeout_code,
        )

    async def restore(self, handle: EnvironmentHandle) -> None:
        spec = self.definition.restore
        try:
            result = await self.exec(
                handle, spec.service, list(spec.command), float(spec.timeout_seconds)
            )
        except EnvironmentDriverError as exc:
            raise EnvironmentDriverError(
                f"Restore command could not run: {exc.message}",
                code=FailureCode.ENV_RESTORE_FAILED,
                details=exc.details,
            ) from exc
        if result.exit_code != 0:
            raise EnvironmentDriverError(
                f"Restore command exited with code {result.exit_code}: {_stderr_tail(result)}",
                code=FailureCode.ENV_RESTORE_FAILED,
                details={
                    "service": spec.service,
                    "command": list(spec.command),
                    "exit_code": result.exit_code,
                },
            )

    async def healthcheck(self, handle: EnvironmentHandle) -> HealthResult:
        spec = self.definition.healthcheck
        url = self._substitute_ports(spec.url)
        deadline = time.monotonic() + spec.timeout_seconds
        attempts = 0
        last_detail = "no attempt made"
        while True:
            attempts += 1
            ok, detail = await asyncio.to_thread(self._probe_http, url)
            if ok:
                return HealthResult(healthy=True, detail=detail, attempts=attempts)
            last_detail = detail
            if time.monotonic() >= deadline:
                raise EnvironmentDriverError(
                    f"Healthcheck did not pass within {spec.timeout_seconds}s "
                    f"({attempts} attempts, last: {last_detail})",
                    code=FailureCode.ENV_HEALTHCHECK_FAILED,
                    details={"url": url, "attempts": attempts},
                )
            await asyncio.sleep(min(spec.interval_seconds, 1.0))

    @staticmethod
    def _probe_http(url: str) -> tuple[bool, str]:
        try:
            with urllib.request.urlopen(url, timeout=5.0) as response:
                status = response.status
        except (urllib.error.URLError, OSError) as exc:
            return False, f"{type(exc).__name__}: {exc}"
        if 200 <= status < 300:
            return True, f"HTTP {status}"
        return False, f"HTTP {status}"

    async def capture_state(self, handle: EnvironmentHandle) -> CapturedState:
        spec = self.definition.state.capture
        try:
            result = await self.exec(
                handle, spec.service, list(spec.command), float(spec.timeout_seconds)
            )
        except EnvironmentDriverError as exc:
            raise EnvironmentDriverError(
                f"State capture command could not run: {exc.message}",
                code=FailureCode.ENV_STATE_CAPTURE_FAILED,
                details=exc.details,
            ) from exc
        if result.exit_code != 0:
            raise EnvironmentDriverError(
                f"State capture command exited with code {result.exit_code}: "
                f"{_stderr_tail(result)}",
                code=FailureCode.ENV_STATE_CAPTURE_FAILED,
                details={
                    "service": spec.service,
                    "command": list(spec.command),
                    "exit_code": result.exit_code,
                },
            )
        return canonicalize_state(result.stdout, self.definition.state.canonicalization)

    async def collect_logs(self, handle: EnvironmentHandle) -> list[Path]:
        result = await self._compose(["logs", "--no-color"], timeout_seconds=60.0)
        if result.exit_code != 0:
            return []
        log_path = handle.work_dir / "environment.log"
        log_path.write_text(result.stdout, encoding="utf-8")
        return [log_path]

    async def teardown(self, handle: EnvironmentHandle) -> None:
        result = await self._compose(
            ["down", "--volumes", "--remove-orphans", "--timeout", "10"],
            timeout_seconds=120.0,
            timeout_code=FailureCode.ENV_TEARDOWN_FAILED,
        )
        if result.exit_code != 0:
            raise EnvironmentDriverError(
                f"docker compose down failed for project {handle.project_name}: "
                f"{_stderr_tail(result)}. Manual cleanup: docker compose -p "
                f"{handle.project_name} down --volumes --remove-orphans",
                code=FailureCode.ENV_TEARDOWN_FAILED,
                details={"compose_project": handle.project_name},
            )
