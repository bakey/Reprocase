"""Run a set of EvalCases in isolated subprocesses and aggregate a Markdown
report (M9 task-pack suite runner).

Each case runs via the installed ``reprocase`` CLI in its own process,
sidestepping the known same-process package-name collision between example
directories. Exit code is non-zero when any case does not PASS (gate
semantics for CI).

Usage:
    python scripts/run_suite.py examples/acme-world/task-*.yaml
    python scripts/run_suite.py --output suite-report.md <cases...>
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import tempfile
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


def _reprocase_binary() -> str:
    candidate = Path(sys.executable).parent / "reprocase"
    if candidate.exists():
        return str(candidate)
    raise SystemExit("reprocase CLI not found next to the active interpreter; activate the venv")


def _run_case(case: Path, runs_root: Path) -> CaseOutcome:
    completed = subprocess.run(
        [_reprocase_binary(), "run", str(case), "--output", str(runs_root)],
        capture_output=True,
        text=True,
        cwd=ROOT,
        check=False,
    )
    if completed.returncode != 0:
        print(f"[suite] {case.name}: runner exited {completed.returncode}", file=sys.stderr)
        print(completed.stderr[-2000:], file=sys.stderr)
        return CaseOutcome(case.name, "ERROR", "RUNNER_ERROR", 0, None, None, "-")

    run_dirs = sorted((runs_root).glob("*/result.json"), key=lambda p: p.stat().st_mtime)
    if not run_dirs:
        return CaseOutcome(case.name, "ERROR", "NO_RESULT", 0, None, None, "-")
    result: dict[str, Any] = json.loads(run_dirs[-1].read_text(encoding="utf-8"))
    usage = result.get("usage") or {}
    agent = result.get("agent") or {}
    return CaseOutcome(
        case=case.name,
        verdict=result.get("verdict", "UNKNOWN"),
        status=result.get("status", "UNKNOWN"),
        duration_ms=int(result.get("duration_ms", 0)),
        tool_calls=usage.get("tool_calls"),
        model=agent.get("model"),
        run_id=result.get("run_id", "-"),
    )


def _markdown(outcomes: list[CaseOutcome]) -> str:
    lines = [
        "# Task-pack suite report",
        "",
        "| Case | Verdict | Status | Duration (s) | Tool calls | Model | Run |",
        "|---|---|---|---|---|---|---|",
    ]
    for outcome in outcomes:
        duration = f"{outcome.duration_ms / 1000:.1f}"
        lines.append(
            f"| {outcome.case} | {outcome.verdict} | {outcome.status} | {duration} "
            f"| {outcome.tool_calls if outcome.tool_calls is not None else '—'} "
            f"| {outcome.model or '—'} | {outcome.run_id} |"
        )
    passed = sum(1 for o in outcomes if o.verdict == "PASS")
    lines += ["", f"**{passed}/{len(outcomes)} cases PASS.**", ""]
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("cases", nargs="+", help="EvalCase paths (globs allowed)")
    parser.add_argument("--output", default="suite-report.md", help="Markdown report path")
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

    with tempfile.TemporaryDirectory(prefix="reprocase-suite-") as runs_root:
        outcomes = [_run_case(case, Path(runs_root)) for case in cases]

    report = _markdown(outcomes)
    Path(args.output).write_text(report, encoding="utf-8")
    print(report)
    print(f"[suite] report written to {args.output}")
    return 0 if all(o.verdict == "PASS" for o in outcomes) else 1


if __name__ == "__main__":
    raise SystemExit(main())
