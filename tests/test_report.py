"""Unit tests for the static HTML report and doctor helpers."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from typer.testing import CliRunner

from reprocase.cli import app
from reprocase.doctor import find_stale_projects
from reprocase.results.report import SENSITIVE_DATA_NOTICE, render_report, write_html_report

runner = CliRunner()


def _run_dir(tmp_path: Path) -> Path:
    run_dir = tmp_path / "run_x"
    run_dir.mkdir()
    (run_dir / "manifest.json").write_text(
        json.dumps(
            {
                "schema_version": "reprocase.run-manifest/v0",
                "run_id": "run_x",
                "case": {
                    "id": "support.refund.agent",
                    "version": "0.1.0",
                    "digest": "sha256:x",
                    "path": "refund-agent.yaml",
                },
                "source": {
                    "python": "3.12.0",
                    "platform": "macOS-arm64",
                    "git_commit": "abc",
                    "git_dirty": True,
                },
                "budget": {"wall_time_seconds": 30},
                "seed": 0,
                "agent": {
                    "adapter": "python",
                    "entrypoint": "agents.correct:run",
                    "code": {"path": "agents/correct.py", "sha256": "deadbeef"},
                    "provider": "tools.support_api:create_tools",
                    "provider_code": {"path": "tools/support_api.py", "sha256": "feedface"},
                },
            }
        ),
        encoding="utf-8",
    )
    (run_dir / "result.json").write_text(
        json.dumps(
            {
                "schema_version": "reprocase.run-result/v0",
                "run_id": "run_x",
                "status": "COMPLETED",
                "verdict": "FAIL",
                "final_state": "COMPLETED",
                "case": {
                    "id": "support.refund.agent",
                    "version": "0.1.0",
                    "digest": "sha256:x",
                    "path": "refund-agent.yaml",
                },
                "started_at": "2026-09-13T00:00:00Z",
                "finished_at": "2026-09-13T00:00:10Z",
                "duration_ms": 10000,
                "reproducibility_level": "R1",
                "agent_output": {"message": "<script>alert(1)</script>"},
                "usage": {"tool_calls": 3},
                "states": [],
                "graders": [
                    {
                        "grader_id": "refund-created",
                        "status": "FAIL",
                        "critical": True,
                        "explanation": "value changed <img src=x onerror=alert(1)>",
                        "grader_type": "state_assertion",
                        "expected": "completed",
                        "actual": "none",
                        "evidence": ["state.final.json#/refunds/RF-9001"],
                    }
                ],
                "artifacts": [
                    {
                        "media_type": "text/plain",
                        "path": "artifacts/sha256/48/48abc",
                        "size_bytes": 12,
                        "sha256": "48abc",
                    }
                ],
                "primary_failure": {
                    "attribution": "AGENT_FAILURE",
                    "code": "GRADER_ASSERTION_FAILED",
                    "message": "refund missing",
                    "details": {},
                },
                "warnings": ["Agent ignored cancellation"],
            }
        ),
        encoding="utf-8",
    )
    (run_dir / "state.diff.json").write_text(
        json.dumps(
            {
                "schema_version": "reprocase.state-diff/v0",
                "ops": [{"op": "add", "path": "/refunds/RF-9001", "value": {}}],
            }
        ),
        encoding="utf-8",
    )
    (run_dir / "events.jsonl").write_text(
        json.dumps(
            {
                "sequence": 1,
                "type": "agent.completed",
                "source": "python_agent",
                "payload": {"output": "<b>bold</b>"},
            }
        )
        + "\n",
        encoding="utf-8",
    )
    return run_dir


def test_report_contains_all_sections(tmp_path: Path) -> None:
    html = render_report(_run_dir(tmp_path))
    for section in (
        "Verdict and attribution",
        "Case, agent, environment, and source",
        "Budgets and usage",
        "Graders",
        "State digests",
        "State diff",
        "Timeline",
        "Warnings",
        "Artifacts",
        "Reproduction",
    ):
        assert section in html, section
    assert SENSITIVE_DATA_NOTICE in html
    assert "support.refund.agent" in html
    assert "agents.correct:run" in html
    assert "deadbeef" in html  # code identity visible
    assert "GRADER_ASSERTION_FAILED" in html
    assert "Agent ignored cancellation" in html
    assert 'href="artifacts/sha256/48/48abc"' in html
    assert "reprocase run refund-agent.yaml" in html
    assert "<details" in html  # collapsible diff


def test_report_escapes_all_dynamic_text(tmp_path: Path) -> None:
    html = render_report(_run_dir(tmp_path))
    # raw payloads must never appear as markup; escaped text is expected
    assert "<script>alert(1)</script>" not in html
    assert "<img src=x onerror=alert(1)>" not in html
    assert "<b>bold</b>" not in html
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in html
    assert "&lt;img src=x onerror=alert(1)&gt;" in html
    assert "&lt;b&gt;bold&lt;/b&gt;" in html


def test_write_html_report_and_cli(tmp_path: Path) -> None:
    run_dir = _run_dir(tmp_path)
    target = write_html_report(run_dir)
    assert target.is_file()
    assert oct(target.stat().st_mode & 0o777) == "0o600"

    result = runner.invoke(app, ["report", str(run_dir)])
    assert result.exit_code == 0
    assert "Report:" in result.stdout

    missing = runner.invoke(app, ["report", str(tmp_path)])
    assert missing.exit_code == 2


def test_find_stale_projects() -> None:
    lines = (
        "reprocase_support_refund_abc123\nother_project\n"
        "reprocase_case_x\n\nreprocase_support_refund_abc123\n"
    )
    assert find_stale_projects(lines) == ["reprocase_case_x", "reprocase_support_refund_abc123"]
    assert find_stale_projects("") == []


def test_graders_submodule_first_import_has_no_cycle() -> None:
    """Regression: importing graders first must not hit the runner cycle."""

    outcome = subprocess.run(
        [sys.executable, "-c", "import reprocase.graders.test_command"],
        capture_output=True,
        timeout=60,
        check=False,
    )
    assert outcome.returncode == 0, outcome.stderr.decode()[:500]


def test_runner_package_lazy_run_case() -> None:
    import reprocase.runner as runner_package

    assert callable(runner_package.run_case)


def test_build_rerun_command_variants() -> None:
    from reprocase.results.report import build_rerun_command

    result = {
        "case": {
            "path": "refund-agent.yaml",
            "relative_path": "examples/support-world/refund-agent.yaml",
        }
    }
    assert (
        build_rerun_command(None, result)
        == "reprocase run examples/support-world/refund-agent.yaml"
    )

    with_override = {"agent": {"entrypoint_override": "agents.wrong:run"}, "seed": 0}
    assert build_rerun_command(with_override, result).endswith("--agent agents.wrong:run")

    with_seed = {"agent": {}, "seed": 173}
    assert build_rerun_command(with_seed, result).endswith("--seed 173")

    spaced = {"case": {"path": "case.yaml", "relative_path": "my cases/refund case.yaml"}}
    command = build_rerun_command({"agent": {}, "seed": 0}, spaced)
    assert "'my cases/refund case.yaml'" in command

    # filename-only fallback for legacy run directories
    legacy = {"case": {"path": "refund.yaml"}}
    assert build_rerun_command(None, legacy) == "reprocase run refund.yaml"
