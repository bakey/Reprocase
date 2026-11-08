"""M6 acceptance: Acme World digest stability, fixture variants, smoke case."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest
from conftest import requires_docker, residual_resources

from reprocase.environments.check import load_compose_target, run_environment_check
from reprocase.results.models import GraderStatus, RunStatus, Verdict
from reprocase.runner.orchestrator import run_case

ROOT = Path(__file__).resolve().parents[1]
ACME_DIR = ROOT / "examples" / "acme-world"
ACME_ENV = ACME_DIR / "environment.yaml"
ACME_SMOKE = ACME_DIR / "refund-smoke.yaml"

DEFAULT_DIGEST = "sha256:7ca57b4a672ea185daec20fb766ed131f1ea1cd246a61ce0e1eb2ac1c9c97ff0"
VARIANT_DIGEST = "sha256:fffff2caff3be5ff503884d4f29d247836caf8369e3bcfc2c78253c8c34562f5"
VARIANT_FIXTURE = "/fixtures/overdue-refund.json"

pytestmark = [requires_docker, pytest.mark.docker]


def test_default_fixture_digest_stable(tmp_path: Path) -> None:
    target = load_compose_target(ACME_ENV)
    report = asyncio.run(run_environment_check(target, cycles=2, work_root=tmp_path / "env"))
    assert not report.failures
    assert report.digests == (DEFAULT_DIGEST,) * 2
    assert residual_resources() == []


def test_variant_fixture_digest_differs_and_is_stable(tmp_path: Path) -> None:
    target = load_compose_target(ACME_ENV)
    report = asyncio.run(
        run_environment_check(
            target, cycles=2, work_root=tmp_path / "env", fixture_override=VARIANT_FIXTURE
        )
    )
    assert not report.failures
    assert report.digests == (VARIANT_DIGEST,) * 2
    assert VARIANT_DIGEST != DEFAULT_DIGEST
    assert residual_resources() == []


def test_smoke_case_passes_end_to_end(tmp_path: Path) -> None:
    output = tmp_path / "runs"
    result = run_case(ACME_SMOKE, output)

    assert result.verdict == Verdict.PASS
    assert result.status == RunStatus.COMPLETED
    assert {grader.status for grader in result.graders} == {GraderStatus.PASS}
    assert len(result.graders) == 4

    initial, final = result.states
    assert initial.content_digest == DEFAULT_DIGEST
    assert final.content_digest != initial.content_digest

    # the policy grader also asserts every side effect carries the agent actor
    events = [
        json.loads(line)
        for line in (output / result.run_id / "events.jsonl").read_text().splitlines()
    ]
    assert any(event["type"] == "tool.call.completed" for event in events)
    assert (output / result.run_id / "report.html").is_file()
    assert residual_resources() == []


@pytest.mark.slow
def test_twenty_cycles_identical_digests(tmp_path: Path) -> None:
    target = load_compose_target(ACME_ENV)
    report = asyncio.run(run_environment_check(target, cycles=20, work_root=tmp_path / "env"))
    assert not report.failures, [cycle.failure_message for cycle in report.failures]
    assert len(report.digests) == 20
    assert set(report.digests) == {DEFAULT_DIGEST}
    assert report.digests_identical
    assert residual_resources() == []
