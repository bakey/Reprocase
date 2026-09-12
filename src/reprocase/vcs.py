"""Git source identity for run manifests (design section 6.4)."""

from __future__ import annotations

import subprocess
from pathlib import Path


def _git(path: Path, args: list[str]) -> str | None:
    try:
        result = subprocess.run(
            ["git", *args],
            cwd=path,
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if result.returncode != 0:
        return None
    return result.stdout.strip()


def git_identity(path: Path) -> tuple[str | None, bool | None]:
    """Return (commit, dirty) for the repository containing ``path``.

    Both are None when git is unavailable or the path is not in a repository.
    """

    commit = _git(path, ["rev-parse", "HEAD"])
    if not commit:
        return None, None
    status = _git(path, ["status", "--porcelain"])
    dirty = bool(status) if status is not None else None
    return commit, dirty
