from __future__ import annotations

from pathlib import Path

from typer.testing import CliRunner

from reprocase.cli import app

ROOT = Path(__file__).resolve().parents[1]
runner = CliRunner()


def test_validate_command() -> None:
    result = runner.invoke(app, ["validate", str(ROOT / "examples/fake/eval-pass.yaml")])

    assert result.exit_code == 0
    assert "PASS schema" in result.stdout
    assert "fake.contract.pass@0.1.0 is valid" in result.stdout


def test_run_exit_codes(tmp_path: Path) -> None:
    passing = runner.invoke(
        app,
        ["run", str(ROOT / "examples/fake/eval-pass.yaml"), "--output", str(tmp_path / "p")],
    )
    failing = runner.invoke(
        app,
        ["run", str(ROOT / "examples/fake/eval-fail.yaml"), "--output", str(tmp_path / "f")],
    )
    invalid = runner.invoke(
        app,
        [
            "run",
            str(ROOT / "examples/fake/eval-invalid.yaml"),
            "--output",
            str(tmp_path / "i"),
        ],
    )

    assert passing.exit_code == 0
    assert failing.exit_code == 1
    assert invalid.exit_code == 3
