"""Compose safety precheck (design section 7.6).

Runs against the rendered ``docker compose config`` output plus the raw compose
document for ``include`` entries, which the rendered config no longer shows.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from reprocase.errors import EnvironmentDriverError, FailureCode

DOCKER_SOCKET_PATHS = {"/var/run/docker.sock", "/run/docker.sock"}


@dataclass(frozen=True)
class SafetyViolation:
    rule: str
    service: str | None
    detail: str


def _bind_sources(service: dict[str, Any]) -> list[str]:
    sources: list[str] = []
    for volume in service.get("volumes") or []:
        if isinstance(volume, str):
            parts = volume.split(":")
            if parts and parts[0].startswith(("/", "~", ".")):
                sources.append(parts[0])
        elif (
            isinstance(volume, dict)
            and volume.get("type") == "bind"
            and isinstance(volume.get("source"), str)
        ):
            sources.append(volume["source"])
    return sources


def _check_includes(raw_compose: dict[str, Any]) -> list[SafetyViolation]:
    violations: list[SafetyViolation] = []
    for entry in raw_compose.get("include") or []:
        path = entry if isinstance(entry, str) else entry.get("path", "")
        paths = path if isinstance(path, list) else [path]
        for item in paths:
            if isinstance(item, str) and item.startswith(("http://", "https://")):
                violations.append(
                    SafetyViolation(
                        rule="remote-include",
                        service=None,
                        detail=f"unpinned remote include: {item}",
                    )
                )
    return violations


def check_compose_safety(
    rendered_config: dict[str, Any],
    *,
    raw_compose: dict[str, Any] | None = None,
    workspace_root: Path | None = None,
) -> list[SafetyViolation]:
    violations: list[SafetyViolation] = []
    if raw_compose is not None:
        violations.extend(_check_includes(raw_compose))

    home = Path.home().resolve()
    root = workspace_root.resolve() if workspace_root is not None else None

    services = rendered_config.get("services") or {}
    for name, service in services.items():
        if service.get("privileged") is True:
            violations.append(SafetyViolation("privileged", name, "privileged: true"))
        for key in ("network_mode", "pid", "ipc"):
            value = service.get(key)
            if isinstance(value, str) and value.split(":")[0] == "host":
                violations.append(SafetyViolation("host-namespace", name, f"{key}: {value}"))
        cap_add = service.get("cap_add") or []
        if cap_add:
            violations.append(SafetyViolation("cap-add", name, f"cap_add: {sorted(cap_add)}"))
        for source in _bind_sources(service):
            expanded = Path(source).expanduser()
            resolved = expanded if expanded.is_absolute() else None
            if source in DOCKER_SOCKET_PATHS or source.endswith("/docker.sock"):
                violations.append(
                    SafetyViolation("docker-socket", name, f"mounts docker socket: {source}")
                )
                continue
            if resolved is None:
                continue
            if resolved == resolved.parent:
                violations.append(
                    SafetyViolation("host-root-mount", name, f"mounts host root: {source}")
                )
            elif resolved == home:
                violations.append(
                    SafetyViolation("home-mount", name, f"mounts user home: {source}")
                )
            elif root is not None and resolved == root:
                violations.append(
                    SafetyViolation("workspace-mount", name, f"mounts workspace root: {source}")
                )
    return violations


def require_safe_compose(
    rendered_config: dict[str, Any],
    *,
    raw_compose: dict[str, Any] | None = None,
    workspace_root: Path | None = None,
) -> None:
    violations = check_compose_safety(
        rendered_config,
        raw_compose=raw_compose,
        workspace_root=workspace_root,
    )
    if violations:
        raise EnvironmentDriverError(
            f"Compose file rejected by safety precheck ({len(violations)} violation(s)): "
            + "; ".join(f"[{v.rule}] {v.detail}" for v in violations),
            code=FailureCode.ENV_COMPOSE_UNSAFE,
            details={
                "violations": [
                    {"rule": v.rule, "service": v.service, "detail": v.detail} for v in violations
                ]
            },
        )


def parse_rendered_config(raw: str, *, compose_file: Path) -> dict[str, Any]:
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise EnvironmentDriverError(
            f"docker compose config returned invalid JSON for {compose_file}",
            code=FailureCode.ENV_COMPOSE_INVALID,
            details={"compose_file": str(compose_file), "error": str(exc)},
        ) from exc
    if not isinstance(parsed, dict) or not isinstance(parsed.get("services"), dict):
        raise EnvironmentDriverError(
            f"docker compose config for {compose_file} has no services mapping",
            code=FailureCode.ENV_COMPOSE_INVALID,
            details={"compose_file": str(compose_file)},
        )
    return parsed
