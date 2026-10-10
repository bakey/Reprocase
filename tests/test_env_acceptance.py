"""M1 acceptance: 20 consecutive restore/capture cycles must produce identical digests.

Marked ``slow`` so it can be deselected with ``-m "not slow"``.
"""

from __future__ import annotations

import asyncio
import subprocess
from pathlib import Path

import pytest
from conftest import SUPPORT_WORLD_ENV, requires_docker

from reprocase.environments.check import load_compose_target, run_environment_check

# Digest of the M5 fixture (ORD-3003 + approvals collection); the M1-era value
# sha256:5189f4f2... is recorded in docs/m1.md as a historical observation.
GOLDEN_DIGEST = "sha256:2e6366790224708234acdc38aa8576c688286e965a58d0617db22fc67738095a"

pytestmark = [requires_docker, pytest.mark.docker, pytest.mark.slow]


def test_twenty_cycles_identical_digests(tmp_path: Path) -> None:
    definition, compose_file, slug = load_compose_target(SUPPORT_WORLD_ENV)
    report = asyncio.run(
        run_environment_check(
            definition, compose_file, cycles=20, slug=slug, work_root=tmp_path / "env"
        )
    )

    assert not report.failures, [cycle.failure_message for cycle in report.failures]
    assert len(report.digests) == 20
    assert set(report.digests) == {GOLDEN_DIGEST}
    assert report.digests_identical

    leftovers: list[str] = []
    for argv in (
        ["docker", "ps", "-a", "--filter", "name=reprocase", "-q"],
        ["docker", "network", "ls", "--filter", "name=reprocase", "-q"],
        ["docker", "volume", "ls", "--filter", "name=reprocase", "-q"],
    ):
        output = subprocess.run(argv, capture_output=True, text=True, check=False).stdout.strip()
        if output:
            leftovers.append(output)
    assert leftovers == []
