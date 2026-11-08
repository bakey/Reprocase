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


def residual_resources(grace_seconds: float = 30.0) -> list[str]:
    """Reprocase-labeled Docker leftovers, polling briefly for daemon lag.

    ``docker compose down -v`` can return before the daemon finishes removing
    container/network/volume records under load (observed on Docker Desktop
    for macOS), so a single immediate check flakes. A real leak never
    disappears, so polling for a bounded grace keeps the zero-residue
    contract strict while tolerating slow removal.
    """
    import time

    commands = {
        "container": ["docker", "ps", "-a", "--filter", "name=reprocase", "-q"],
        "network": ["docker", "network", "ls", "--filter", "name=reprocase", "-q"],
        "volume": ["docker", "volume", "ls", "--filter", "name=reprocase", "-q"],
    }

    def collect() -> list[str]:
        leftovers: list[str] = []
        for resource, argv in commands.items():
            completed = subprocess.run(argv, capture_output=True, text=True, check=False)
            output = completed.stdout.strip()
            if output:
                leftovers.append(f"{resource}: {output}")
        return leftovers

    deadline = time.monotonic() + grace_seconds
    leftovers = collect()
    while leftovers and time.monotonic() < deadline:
        time.sleep(1)
        leftovers = collect()
    return leftovers
