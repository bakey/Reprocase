"""Reprocase command-line interface."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Annotated

import typer

from reprocase.case.loader import load_case
from reprocase.errors import CaseValidationError, ReprocaseError
from reprocase.results.models import Verdict
from reprocase.runner.orchestrator import run_case

app = typer.Typer(
    name="reprocase",
    help="Reproducible execution and evaluation for AI agents.",
    no_args_is_help=True,
    pretty_exceptions_show_locals=False,
)


def _print_error(error: ReprocaseError) -> None:
    typer.secho(f"ERROR [{error.code}] {error.message}", fg=typer.colors.RED, err=True)
    if error.details:
        typer.echo(json.dumps(error.details, ensure_ascii=False, indent=2), err=True)


@app.command()
def validate(
    case_path: Annotated[
        Path,
        typer.Argument(exists=True, dir_okay=False, readable=True, resolve_path=True),
    ],
) -> None:
    """Validate an EvalCase and all M0 references."""

    try:
        loaded = load_case(case_path)
    except CaseValidationError as error:
        _print_error(error)
        raise typer.Exit(code=2) from error

    typer.secho("PASS schema", fg=typer.colors.GREEN)
    typer.secho("PASS referenced environment", fg=typer.colors.GREEN)
    typer.secho("PASS fake component compatibility", fg=typer.colors.GREEN)
    typer.echo(
        f"\nEvalCase {loaded.case.metadata.id}@{loaded.case.metadata.version} is valid\n"
        f"Digest: {loaded.digest}"
    )


@app.command("run")
def run_command(
    case_path: Annotated[
        Path,
        typer.Argument(exists=True, dir_okay=False, readable=True, resolve_path=True),
    ],
    output: Annotated[
        Path,
        typer.Option("--output", "-o", help="Root directory for run evidence."),
    ] = Path(".reprocase/runs"),
    json_output: Annotated[
        bool,
        typer.Option("--json", help="Print the complete result as JSON."),
    ] = False,
) -> None:
    """Execute one M0 fake EvalCase and persist its evidence bundle."""

    try:
        result = run_case(case_path, output)
    except CaseValidationError as error:
        _print_error(error)
        raise typer.Exit(code=2) from error
    except ReprocaseError as error:
        _print_error(error)
        raise typer.Exit(code=3) from error

    if json_output:
        typer.echo(json.dumps(result.model_dump(mode="json"), ensure_ascii=False, indent=2))
    else:
        color = {
            Verdict.PASS: typer.colors.GREEN,
            Verdict.FAIL: typer.colors.RED,
            Verdict.INVALID: typer.colors.YELLOW,
        }[result.verdict]
        typer.secho(
            f"{result.verdict} {result.case.id}@{result.case.version}",
            fg=color,
            bold=True,
        )
        typer.echo(f"Run: {result.run_id}")
        typer.echo(f"Evidence: {(output.resolve() / result.run_id)}")
        if result.primary_failure is not None:
            typer.echo(f"Failure: [{result.primary_failure.code}] {result.primary_failure.message}")

    if result.verdict == Verdict.FAIL:
        raise typer.Exit(code=1)
    if result.verdict == Verdict.INVALID:
        raise typer.Exit(code=3)


if __name__ == "__main__":
    app()
