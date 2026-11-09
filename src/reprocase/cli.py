"""Reprocase command-line interface."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Annotated

import typer

from reprocase.case.loader import load_case
from reprocase.doctor import run_doctor
from reprocase.environments.check import load_compose_target, run_environment_check
from reprocase.errors import CaseValidationError, EnvironmentDriverError, ReprocaseError
from reprocase.results.models import GraderStatus, RunStatus, Verdict
from reprocase.results.report import write_html_report
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
    if loaded.environment.driver == "fake":
        stateful = [
            grader.id
            for grader in loaded.case.graders
            if grader.type in {"invariant", "temporal_constraint", "tool_policy"}
        ]
        if stateful:
            typer.secho(
                f"WARNING graders {stateful} require a real environment and are skipped "
                "by the fake run flow",
                fg=typer.colors.YELLOW,
            )
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
    agent: Annotated[
        str | None,
        typer.Option("--agent", help="Override the scripted actor entrypoint (module:function)."),
    ] = None,
    seed: Annotated[
        int,
        typer.Option("--seed", help="Seed recorded in the manifest and passed to the agent."),
    ] = 0,
    json_output: Annotated[
        bool,
        typer.Option("--json", help="Print the complete result as JSON."),
    ] = False,
) -> None:
    """Execute one EvalCase and persist its evidence bundle."""

    try:
        result = run_case(case_path, output, agent_override=agent, seed=seed)
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
        for snapshot in result.states:
            typer.echo(f"State {snapshot.phase}: {snapshot.content_digest}")
        for graded in result.graders:
            color = {
                GraderStatus.PASS: typer.colors.GREEN,
                GraderStatus.FAIL: typer.colors.RED,
                GraderStatus.ERROR: typer.colors.YELLOW,
                GraderStatus.SKIPPED: typer.colors.WHITE,
            }[graded.status]
            typer.secho(f"{graded.status} {graded.grader_id}", fg=color)
        if result.primary_failure is not None:
            typer.echo(f"Failure: [{result.primary_failure.code}] {result.primary_failure.message}")
        if result.cleanup_required:
            typer.secho(
                "INFRASTRUCTURE WARNING: environment teardown did not complete.\n"
                f"Manual cleanup: {result.cleanup_command}",
                fg=typer.colors.YELLOW,
            )

    if result.cleanup_required:
        raise typer.Exit(code=3)
    if result.verdict == Verdict.FAIL:
        raise typer.Exit(code=1)
    if result.status == RunStatus.CANCELLED:
        raise typer.Exit(code=4)
    if result.verdict == Verdict.INVALID:
        raise typer.Exit(code=3)


@app.command("report")
def report_command(
    run_dir: Annotated[
        Path,
        typer.Argument(exists=True, file_okay=False, readable=True, resolve_path=True),
    ],
) -> None:
    """Regenerate the static HTML report for an existing run directory."""

    try:
        target = write_html_report(run_dir)
    except (OSError, ValueError) as error:
        typer.secho(
            f"ERROR could not build report for {run_dir}: {error}",
            fg=typer.colors.RED,
            err=True,
        )
        raise typer.Exit(code=2) from error
    typer.echo(f"Report: {target}")


@app.command()
def doctor() -> None:
    """Check local dependencies and report stale reprocase compose projects."""

    checks, stale = run_doctor()
    failed = False
    for check in checks:
        if check.ok:
            typer.secho(f"OK   {check.name}: {check.detail}", fg=typer.colors.GREEN)
        else:
            typer.secho(f"FAIL {check.name}: {check.detail}", fg=typer.colors.RED)
            failed = True
    if stale:
        typer.secho(
            "\nStale reprocase compose projects found (not removed automatically):",
            fg=typer.colors.YELLOW,
        )
        for project in stale:
            typer.echo(f"  {project}")
            typer.echo(f"    cleanup: docker compose -p {project} down --volumes --remove-orphans")
    else:
        typer.echo("No stale reprocase compose projects.")
    if failed:
        raise typer.Exit(code=1)


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
    fixture: Annotated[
        str | None,
        typer.Option(
            "--fixture",
            help="Override the ${FIXTURE} restore value (container-side path).",
        ),
    ] = None,
) -> None:
    """Internal diagnostic: run the environment lifecycle and print state digests."""

    try:
        resolved = load_compose_target(target)
    except CaseValidationError as error:
        _print_error(error)
        raise typer.Exit(code=2) from error

    try:
        report = asyncio.run(
            run_environment_check(
                resolved,
                cycles=cycles,
                work_root=work_root,
                allow_unsafe=allow_unsafe,
                fixture_override=fixture,
            )
        )
    except (EnvironmentDriverError, CaseValidationError) as error:
        _print_error(error)
        raise typer.Exit(code=3 if isinstance(error, EnvironmentDriverError) else 2) from error

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
