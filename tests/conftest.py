from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SUPPORT_WORLD_DIR = ROOT / "examples" / "support-world"
SUPPORT_WORLD_ENV = SUPPORT_WORLD_DIR / "environment.yaml"


def docker_available() -> bool:
    if shutil.which("docker") is None:
        return False
    try:
        result = subprocess.run(
            ["docker", "info"],
            capture_output=True,
            timeout=20,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    return result.returncode == 0


requires_docker = pytest.mark.skipif(
    not docker_available(), reason="Docker daemon is not available"
)
