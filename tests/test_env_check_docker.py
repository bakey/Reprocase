from __future__ import annotations

import asyncio
import subprocess
from pathlib import Path
from typing import Any

import pytest
import yaml
from conftest import SUPPORT_WORLD_DIR, SUPPORT_WORLD_ENV, requires_docker
from typer.testing import CliRunner

from reprocase.cli import app
from reprocase.environments.check import load_compose_target, run_environment_check
from reprocase.errors import FailureCode

GOLDEN_DIGEST = "sha256:5189f4f2dd66c9edf848040762f2492c2569a99f6dcccec3bf9511e2f868c753"

pytestmark = [requires_docker, pytest.mark.docker]


def _residual_resources() -> list[str]:
    leftovers: list[str] = []
    commands = {
        "container": ["docker", "ps", "-a", "--filter", "name=reprocase", "-q"],
        "network": ["docker", "network", "ls", "--filter", "name=reprocase", "-q"],
        "volume": ["docker", "volume", "ls", "--filter", "name=reprocase", "-q"],
    }
    for resource, argv in commands.items():
        output = subprocess.run(argv, capture_output=True, text=True, check=False).stdout.strip()
        if output:
            leftovers.append(f"{resource}: {output}")
    return leftovers


def _write_variant(tmp_path: Path, mutate: Any) -> Path:
    base = yaml.safe_load(SUPPORT_WORLD_ENV.read_text(encoding="utf-8"))
    mutate(base)
    base["compose"]["file"] = str(SUPPORT_WORLD_DIR / "compose.yaml")
    target = tmp_path / "environment.yaml"
    target.write_text(yaml.safe_dump(base), encoding="utf-8")
    return target


def _run_once(target: Path, work_root: Path) -> Any:
    definition, compose_file, slug = load_compose_target(target)
    return asyncio.run(
        run_environment_check(definition, compose_file, cycles=1, slug=slug, work_root=work_root)
    )


def test_single_cycle_digest_and_cleanup(tmp_path: Path) -> None:
    report = _run_once(SUPPORT_WORLD_ENV, tmp_path / "env")

    assert not report.failures
    assert report.digests == (GOLDEN_DIGEST,)
    assert _residual_resources() == []


def test_restore_failure_is_classified(tmp_path: Path) -> None:
    def mutate(doc: dict[str, Any]) -> None:
        doc["restore"]["command"] = ["python", "-c", "import sys; sys.exit(3)"]

    report = _run_once(_write_variant(tmp_path, mutate), tmp_path / "env")

    assert len(report.failures) == 1
    assert report.failures[0].failure_code == FailureCode.ENV_RESTORE_FAILED
    assert _residual_resources() == []


def test_healthcheck_timeout_is_classified(tmp_path: Path) -> None:
    def mutate(doc: dict[str, Any]) -> None:
        doc["healthcheck"]["url"] = "http://127.0.0.1:${API_PORT}/no-such-route"
        doc["healthcheck"]["timeout_seconds"] = 3

    report = _run_once(_write_variant(tmp_path, mutate), tmp_path / "env")

    assert len(report.failures) == 1
    assert report.failures[0].failure_code == FailureCode.ENV_HEALTHCHECK_FAILED
    assert _residual_resources() == []


def test_invalid_state_capture_output_is_classified(tmp_path: Path) -> None:
    def mutate(doc: dict[str, Any]) -> None:
        doc["state"]["capture"]["command"] = ["python", "-c", "print('not json')"]

    report = _run_once(_write_variant(tmp_path, mutate), tmp_path / "env")

    assert len(report.failures) == 1
    assert report.failures[0].failure_code == FailureCode.ENV_STATE_INVALID
    assert _residual_resources() == []


def test_failing_state_capture_command_is_classified(tmp_path: Path) -> None:
    def mutate(doc: dict[str, Any]) -> None:
        doc["state"]["capture"]["command"] = ["python", "-c", "import sys; sys.exit(1)"]

    report = _run_once(_write_variant(tmp_path, mutate), tmp_path / "env")

    assert len(report.failures) == 1
    assert report.failures[0].failure_code == FailureCode.ENV_STATE_CAPTURE_FAILED
    assert _residual_resources() == []


def test_env_check_cli(tmp_path: Path) -> None:
    runner = CliRunner()
    result = runner.invoke(
        app,
        [
            "env-check",
            str(SUPPORT_WORLD_ENV),
            "--cycles",
            "1",
            "--work-root",
            str(tmp_path / "env"),
        ],
    )

    assert result.exit_code == 0, result.output
    assert GOLDEN_DIGEST in result.output
    assert "OK 1/1 identical digests" in result.output
    assert _residual_resources() == []
