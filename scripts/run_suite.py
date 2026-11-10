"""Run a set of EvalCases in isolated subprocesses and aggregate a Markdown
report (M9 task-pack suite runner).

Each case runs via the installed ``reprocase`` CLI in its own process,
sidestepping the known same-process package-name collision between example
directories. Evidence bundles are kept under ``--runs-root`` (default
``.reprocase/suite-runs``) so regressions stay investiguable and CI can
collect them as artifacts.

Verdicts come from each run's own ``result.json`` whenever one exists — a
FAIL or INVALID run is a valid outcome, not a suite error. Only a missing
result (e.g. the runner crashed before writing one) becomes an ERROR row.
Exit code is non-zero when any case does not PASS (gate semantics).

Usage:
    python scripts/run_suite.py examples/acme-world/task-*.yaml
    python scripts/run_suite.py --output suite-report.md <cases...>
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]


@dataclass(frozen=True)
class CaseOutcome:
    case: str
    verdict: str
    status: str
    duration_ms: int
    tool_calls: int | None
    model: str | None
    run_id: str
    bundle: str


def _reprocase_binary() -> str:
    candidate = Path(sys.executable).parent / "reprocase"
    if candidate.exists():
        return str(candidate)
    raise SystemExit("reprocase CLI not found next to the active interpreter; activate the venv")


def _run_case(case: Path, runs_root: Path) -> CaseOutcome:
    # one output directory per case: the freshest result.json inside it is
    # unambiguously this case's run, valid for PASS/FAIL/INVALID alike
    case_output = runs_root / case.stem
    case_output.mkdir(parents=True, exist_ok=True)
    completed = subprocess.run(
        [_reprocase_binary(), "run", str(case), "--output", str(case_output)],
        capture_output=True,
        text=True,
        cwd=ROOT,
        check=False,
    )
    result_files = sorted(case_output.glob("*/result.json"), key=lambda p: p.stat().st_mtime)
    if not result_files:
        print(f"[suite] {case.name}: no result.json (exit {completed.returncode})", file=sys.stderr)
        print(completed.stderr[-2000:], file=sys.stderr)
        return CaseOutcome(case.name, "ERROR", "RUNNER_ERROR", 0, None, None, "-", "-")

    result: dict[str, Any] = json.loads(result_files[-1].read_text(encoding="utf-8"))
    usage = result.get("usage") or {}
    agent = result.get("agent") or {}
    run_id = str(result.get("run_id", "-"))
    return CaseOutcome(
        case=case.name,
        verdict=str(result.get("verdict", "UNKNOWN")),
        status=str(result.get("status", "UNKNOWN")),
        duration_ms=int(result.get("duration_ms", 0)),
        tool_calls=usage.get("tool_calls"),
        model=agent.get("model"),
        run_id=run_id,
        bundle=str(result_files[-1].parent),
    )


def _markdown(outcomes: list[CaseOutcome]) -> str:
    lines = [
        "# Task-pack suite report",
        "",
        "| Case | Verdict | Status | Duration (s) | Tool calls | Model | Evidence |",
        "|---|---|---|---|---|---|---|",
    ]
    for outcome in outcomes:
        duration = f"{outcome.duration_ms / 1000:.1f}"
        tools = outcome.tool_calls if outcome.tool_calls is not None else "—"
        model = outcome.model or "—"
        evidence = f"[{outcome.run_id}]({outcome.bundle})" if outcome.run_id != "-" else "—"
        lines.append(
            f"| {outcome.case} | {outcome.verdict} | {outcome.status} | {duration} "
            f"| {tools} | {model} | {evidence} |"
        )
    passed = sum(1 for o in outcomes if o.verdict == "PASS")
    lines += ["", f"**{passed}/{len(outcomes)} cases PASS.**", ""]
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("cases", nargs="+", help="EvalCase paths (globs allowed)")
    parser.add_argument("--output", default="suite-report.md", help="Markdown report path")
    parser.add_argument(
        "--runs-root",
        default=".reprocase/suite-runs",
        help="persistent directory for evidence bundles (kept after the run)",
    )
    args = parser.parse_args(argv)

    cases: list[Path] = []
    for pattern in args.cases:
        matches = (
            sorted(ROOT.glob(pattern)) if any(c in pattern for c in "*?[") else [ROOT / pattern]
        )
        if not matches or not matches[0].exists():
            matches = sorted(Path(".").glob(pattern))
        if not matches:
            parser.error(f"no cases match: {pattern}")
        cases.extend(match for match in matches if match.is_file())

    runs_root = Path(args.runs_root)
    runs_root.mkdir(parents=True, exist_ok=True)
    outcomes = [_run_case(case, runs_root) for case in cases]

    report = _markdown(outcomes)
    Path(args.output).write_text(report, encoding="utf-8")
    print(report)
    print(f"[suite] report written to {args.output}; evidence under {runs_root}/")
    return 0 if all(o.verdict == "PASS" for o in outcomes) else 1


if __name__ == "__main__":
    raise SystemExit(main())
