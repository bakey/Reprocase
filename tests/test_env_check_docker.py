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
from reprocase.errors import CaseValidationError, FailureCode

# Digest of the M5 fixture (ORD-3003 + approvals collection); the M1-era value
# sha256:5189f4f2... is recorded in docs/m1.md as a historical observation.
GOLDEN_DIGEST = "sha256:2e6366790224708234acdc38aa8576c688286e965a58d0617db22fc67738095a"

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


def _run_once(
    target_path: Path, work_root: Path, fixture_override: str | None = None, cycles: int = 1
) -> Any:
    target = load_compose_target(target_path)
    return asyncio.run(
        run_environment_check(
            target, cycles=cycles, work_root=work_root, fixture_override=fixture_override
        )
    )


def test_single_cycle_digest_and_cleanup(tmp_path: Path) -> None:
    report = _run_once(SUPPORT_WORLD_ENV, tmp_path / "env")

    assert not report.failures
    assert report.digests == (GOLDEN_DIGEST,)
    assert _residual_resources() == []


def test_fixture_default_keeps_canonical_digest(tmp_path: Path) -> None:
    """The ${FIXTURE} migration with fixture_default restores the same state."""

    assert load_compose_target(SUPPORT_WORLD_ENV).fixture == "/fixtures/refund.json"
    report = _run_once(SUPPORT_WORLD_ENV, tmp_path / "env")
    assert not report.failures
    assert report.digests == (GOLDEN_DIGEST,)


def test_fixture_override_changes_digest_stably(tmp_path: Path) -> None:
    """A different fixture restores different but cycle-stable state."""

    report = _run_once(
        SUPPORT_WORLD_ENV,
        tmp_path / "env",
        fixture_override="/fixtures/refund-variant.json",
        cycles=2,
    )
    assert not report.failures
    assert len(report.digests) == 2
    assert report.digests_identical
    assert report.digests[0] != GOLDEN_DIGEST


def test_fixture_override_without_placeholder_is_rejected(tmp_path: Path) -> None:
    def mutate(doc: dict[str, Any]) -> None:
        doc["restore"]["command"] = ["python", "-m", "app.reset", "/fixtures/refund.json"]
        doc["restore"].pop("fixture_default", None)

    target = _write_variant(tmp_path, mutate)
    loaded = load_compose_target(target)
    assert loaded.fixture is None
    with pytest.raises(CaseValidationError):
        asyncio.run(
            run_environment_check(
                loaded, cycles=1, work_root=tmp_path / "env", fixture_override="/fixtures/x.json"
            )
        )


def test_restore_failure_is_classified(tmp_path: Path) -> None:
    def mutate(doc: dict[str, Any]) -> None:
        doc["restore"]["command"] = ["python", "-c", "import sys; sys.exit(3)"]
        doc["restore"].pop("fixture_default", None)

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
