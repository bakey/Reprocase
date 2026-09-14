"""Environment self-checks and stale-project detection for `reprocase doctor`."""

from __future__ import annotations

import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path

STALE_PROJECT_PREFIX = "reprocase_"


@dataclass(frozen=True)
class DoctorCheck:
    name: str
    ok: bool
    detail: str


def _run(argv: list[str], timeout: float = 15.0) -> str | None:
    try:
        result = subprocess.run(argv, capture_output=True, text=True, timeout=timeout, check=False)
    except (OSError, subprocess.TimeoutExpired):
        return None
    return result.stdout.strip() if result.returncode == 0 else None


def find_stale_projects(project_lines: str) -> list[str]:
    """Compose project names with the reprocase prefix that still have containers."""

    return sorted(
        {
            line.strip()
            for line in project_lines.splitlines()
            if line.strip().startswith(STALE_PROJECT_PREFIX)
        }
    )


def run_doctor() -> tuple[list[DoctorCheck], list[str]]:
    checks: list[DoctorCheck] = [
        DoctorCheck("python >= 3.12", sys.version_info >= (3, 12), sys.version.split()[0])
    ]
    docker_present = shutil.which("docker") is not None
    server = (
        _run(["docker", "version", "--format", "{{.Server.Version}}"]) if docker_present else None
    )
    checks.append(DoctorCheck("docker daemon", server is not None, server or "unavailable"))
    compose = _run(["docker", "compose", "version", "--short"]) if docker_present else None
    checks.append(DoctorCheck("docker compose", compose is not None, compose or "unavailable"))

    writable = False
    try:
        with tempfile.TemporaryFile(dir="."):
            writable = True
    except OSError:
        writable = False
    checks.append(DoctorCheck("writable working directory", writable, str(Path.cwd())))

    stale: list[str] = []
    if server is not None:
        lines = _run(
            [
                "docker",
                "ps",
                "-a",
                "--filter",
                "label=com.docker.compose.project",
                "--format",
                '{{.Label "com.docker.compose.project"}}',
            ]
        )
        stale = find_stale_projects(lines or "")
    return checks, stale
