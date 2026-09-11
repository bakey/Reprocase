"""Reprocase command-line interface."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Annotated

import typer

from reprocase.case.loader import load_case
from reprocase.environments.check import load_compose_target, run_environment_check
from reprocase.errors import CaseValidationError, EnvironmentDriverError, ReprocaseError
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


@app.command("env-check")
def env_check_command(
    target: Annotated[
        Path,
        typer.Argument(exists=True, dir_okay=False, readable=True, resolve_path=True),
    ],
    cycles: Annotated[
        int,
        typer.Option("--cycles", "-n", min=1, max=1000, help="Lifecycle repetitions."),
    ] = 1,
    work_root: Annotated[
        Path,
        typer.Option("--work-root", help="Directory for per-cycle env files and logs."),
    ] = Path(".reprocase/env"),
    allow_unsafe: Annotated[
        bool,
        typer.Option("--allow-unsafe", help="Skip the Compose safety precheck."),
    ] = False,
) -> None:
    """Internal diagnostic: run the environment lifecycle and print state digests."""

    try:
        definition, compose_file, slug = load_compose_target(target)
    except CaseValidationError as error:
        _print_error(error)
        raise typer.Exit(code=2) from error

    try:
        report = asyncio.run(
            run_environment_check(
                definition,
                compose_file,
                cycles=cycles,
                slug=slug,
                work_root=work_root,
                allow_unsafe=allow_unsafe,
            )
        )
    except EnvironmentDriverError as error:
        _print_error(error)
        raise typer.Exit(code=3) from error

    for cycle in report.cycles:
        seconds = cycle.duration_ms / 1000
        if cycle.failure_code is not None:
            typer.secho(
                f"cycle {cycle.cycle:>3}  FAIL [{cycle.failure_code}] "
                f"{cycle.failure_message}  ({seconds:.1f}s)",
                fg=typer.colors.RED,
            )
        else:
            typer.echo(f"cycle {cycle.cycle:>3}  {cycle.digest}  ({seconds:.1f}s)")

    if report.failures:
        first = report.failures[0]
        typer.secho(
            f"\nFAILED {len(report.failures)}/{len(report.cycles)} cycle(s); "
            f"first failure: [{first.failure_code}]",
            fg=typer.colors.RED,
        )
        raise typer.Exit(code=3)
    if not report.digests_identical:
        typer.secho(
            f"\nMISMATCH digests differ across {len(report.cycles)} cycle(s)",
            fg=typer.colors.RED,
        )
        raise typer.Exit(code=1)
    typer.secho(
        f"\nOK {len(report.cycles)}/{len(report.cycles)} identical digests: {report.digests[0]}",
        fg=typer.colors.GREEN,
    )


if __name__ == "__main__":
    app()
