"""Static, single-file HTML run reports (design section 15).

No template engine: the report is assembled from pre-escaped fragments.
Every dynamic string passes through ``html.escape`` — raw HTML from agent,
tool, or grader output can never reach the page.
"""

from __future__ import annotations

import html
import json
from pathlib import Path
from typing import Any

SENSITIVE_DATA_NOTICE = (
    "Run artifacts may contain task data and model inputs/outputs. "
    "Do not upload them to public issues or public CI artifacts."
)

_TIMELINE_PREFIXES = ("agent.", "tool.", "grader.", "environment.", "run.")

_CSS = """
body { font-family: ui-sans-serif, system-ui, sans-serif; margin: 2rem; color: #1a1a1a; }
h1 { font-size: 1.4rem; }
h2 { font-size: 1.05rem; margin-top: 1.6rem; border-bottom: 1px solid #ddd; }
table { border-collapse: collapse; margin: 0.5rem 0; }
th, td { border: 1px solid #ddd; padding: 0.3rem 0.6rem; text-align: left; vertical-align: top; }
th { background: #f4f4f4; }
pre { background: #f7f7f7; padding: 0.6rem; overflow-x: auto; font-size: 0.85rem; }
.verdict-PASS { color: #137333; font-weight: bold; }
.verdict-FAIL { color: #b3261e; font-weight: bold; }
.verdict-INVALID { color: #b06000; font-weight: bold; }
.notice { background: #fff4e5; border: 1px solid #e0a040; padding: 0.6rem; }
.muted { color: #666; font-size: 0.85rem; }
details { margin: 0.5rem 0; }
"""


def _esc(value: Any) -> str:
    return html.escape(str(value), quote=True)


def _pre(value: Any) -> str:
    return (
        "<pre>"
        + html.escape(json.dumps(value, ensure_ascii=False, indent=2, default=str))
        + "</pre>"
    )


def _load_json(run_dir: Path, name: str) -> Any:
    path = run_dir / name
    if not path.is_file():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def _table(headers: list[str], rows: list[list[str]]) -> str:
    head = "".join(f"<th>{_esc(header)}</th>" for header in headers)
    body = "".join("<tr>" + "".join(f"<td>{cell}</td>" for cell in row) + "</tr>" for row in rows)
    return f"<table><thead><tr>{head}</tr></thead><tbody>{body}</tbody></table>"


def _identity_section(manifest: dict[str, Any] | None, result: dict[str, Any]) -> str:
    case = result.get("case", {})
    rows = [
        ["Case", f"{_esc(case.get('id'))}@{_esc(case.get('version'))}"],
        ["Case digest", _esc(case.get("digest"))],
        ["Run", _esc(result.get("run_id"))],
        ["Reproducibility level", _esc(result.get("reproducibility_level"))],
    ]
    if manifest:
        agent = manifest.get("agent") or {}
        source = manifest.get("source") or {}
        rows += [
            ["Agent adapter", _esc(agent.get("adapter"))],
            ["Agent entrypoint", _esc(agent.get("entrypoint"))],
            ["Agent code sha256", _esc((agent.get("code") or {}).get("sha256"))],
            ["Tool provider", _esc(agent.get("provider"))],
            ["Provider code sha256", _esc((agent.get("provider_code") or {}).get("sha256"))],
            ["Python", _esc(source.get("python"))],
            ["Platform", _esc(source.get("platform"))],
            ["Git commit", _esc(source.get("git_commit"))],
            ["Git dirty", _esc(source.get("git_dirty"))],
            ["Seed", _esc(manifest.get("seed"))],
        ]
    return _table(["Field", "Value"], rows)


def _verdict_section(result: dict[str, Any]) -> str:
    verdict = str(result.get("verdict"))
    parts = [
        f'<p>Verdict: <span class="verdict-{_esc(verdict)}">{_esc(verdict)}</span> '
        f"(status {_esc(result.get('status'))})</p>"
    ]
    primary = result.get("primary_failure")
    if primary:
        parts.append(
            f"<p>Primary failure: [{_esc(primary.get('attribution'))} / "
            f"{_esc(primary.get('code'))}] {_esc(primary.get('message'))}</p>"
        )
    secondary = result.get("secondary_failures") or []
    if secondary:
        rows = [
            [
                _esc(failure.get("attribution")),
                _esc(failure.get("code")),
                _esc(failure.get("message")),
            ]
            for failure in secondary
        ]
        parts.append("<h3>Secondary failures</h3>")
        parts.append(_table(["Attribution", "Code", "Message"], rows))
    if result.get("cleanup_required"):
        parts.append(
            '<p class="notice">Cleanup required. Run: '
            f"<code>{_esc(result.get('cleanup_command'))}</code></p>"
        )
    if result.get("agent_output") is not None:
        parts.append(
            "<details><summary>Agent output</summary>"
            + _pre(result.get("agent_output"))
            + "</details>"
        )
    return "".join(parts)


def _usage_section(manifest: dict[str, Any] | None, result: dict[str, Any]) -> str:
    usage = result.get("usage") or {}
    rows = [
        ["Duration", f"{_esc(result.get('duration_ms'))} ms"],
        ["Tool calls", _esc(usage.get("tool_calls"))],
        ["Model calls", _esc(usage.get("model_calls"))],
        ["Input tokens", _esc(usage.get("input_tokens"))],
        ["Output tokens", _esc(usage.get("output_tokens"))],
        ["Cost (USD)", _esc(usage.get("cost_usd"))],
    ]
    if manifest:
        rows.append(["Budget", _esc(json.dumps(manifest.get("budget"), sort_keys=True))])
    return _table(["Metric", "Value"], rows)


def _graders_section(result: dict[str, Any]) -> str:
    rows = []
    for grader in result.get("graders") or []:
        rows.append(
            [
                _esc(grader.get("grader_id")),
                _esc(grader.get("grader_type")),
                _esc(grader.get("status")),
                _esc(grader.get("critical")),
                _esc(grader.get("explanation")),
                "<details><summary>expected / actual</summary>"
                + _pre({"expected": grader.get("expected"), "actual": grader.get("actual")})
                + "</details>",
            ]
        )
    if not rows:
        return '<p class="muted">No graders ran.</p>'
    return _table(["Grader", "Type", "Status", "Critical", "Explanation", "Detail"], rows)


def _states_section(result: dict[str, Any]) -> str:
    rows = [
        [_esc(snapshot.get("phase")), _esc(snapshot.get("content_digest"))]
        for snapshot in result.get("states") or []
    ]
    if not rows:
        return '<p class="muted">No state snapshots captured.</p>'
    return _table(["Phase", "Content digest"], rows)


def _diff_section(diff: Any) -> str:
    if diff is None:
        return '<p class="muted">No state diff available.</p>'
    return "<details><summary>State diff</summary>" + _pre(diff) + "</details>"


def _timeline_section(events: list[dict[str, Any]]) -> str:
    rows = []
    for event in events:
        event_type = str(event.get("type", ""))
        if not event_type.startswith(_TIMELINE_PREFIXES):
            continue
        payload = json.dumps(event.get("payload"), ensure_ascii=False, default=str)
        if len(payload) > 200:
            payload = payload[:200] + "…"
        rows.append([_esc(event.get("sequence")), _esc(event_type), _esc(payload)])
    if not rows:
        return '<p class="muted">No events recorded.</p>'
    return (
        "<details open><summary>Agent / tool / environment timeline</summary>"
        + _table(["#", "Event", "Payload (trimmed)"], rows)
        + "</details>"
    )


def _warnings_section(result: dict[str, Any]) -> str:
    warnings = [SENSITIVE_DATA_NOTICE, *(result.get("warnings") or [])]
    items = "".join(f"<li>{_esc(warning)}</li>" for warning in warnings)
    return f"<ul>{items}</ul>"


def _artifacts_section(result: dict[str, Any]) -> str:
    rows = []
    for artifact in result.get("artifacts") or []:
        path = str(artifact.get("path"))
        escaped = _esc(path)
        rows.append(
            [
                f'<a href="{escaped}">{escaped}</a>',
                _esc(artifact.get("media_type")),
                _esc(artifact.get("size_bytes")),
                _esc(str(artifact.get("sha256"))[:16]),
            ]
        )
    if not rows:
        return '<p class="muted">No artifacts.</p>'
    return _table(["Path", "Media type", "Bytes", "sha256 (prefix)"], rows)


def _rerun_section(result: dict[str, Any]) -> str:
    case = result.get("case", {})
    command = f"reprocase run {case.get('path')}"
    return (
        f"<p>Reproducibility level: <strong>{_esc(result.get('reproducibility_level'))}"
        "</strong></p>"
        f"<p>Re-run: <code>{_esc(command)}</code></p>"
    )


def render_report(run_dir: Path) -> str:
    result = _load_json(run_dir, "result.json")
    if result is None:
        raise FileNotFoundError(f"no result.json in {run_dir}")
    manifest = _load_json(run_dir, "manifest.json")
    diff = _load_json(run_dir, "state.diff.json")
    events_path = run_dir / "events.jsonl"
    events = (
        [json.loads(line) for line in events_path.read_text(encoding="utf-8").splitlines()]
        if events_path.is_file()
        else []
    )

    case = result.get("case", {})
    title = f"reprocase run {result.get('run_id')}"
    sections = [
        ("Verdict and attribution", _verdict_section(result)),
        ("Case, agent, environment, and source", _identity_section(manifest, result)),
        ("Budgets and usage", _usage_section(manifest, result)),
        ("Graders", _graders_section(result)),
        ("State digests", _states_section(result)),
        ("State diff", _diff_section(diff)),
        ("Timeline", _timeline_section(events)),
        ("Warnings", _warnings_section(result)),
        ("Artifacts", _artifacts_section(result)),
        ("Reproduction", _rerun_section(result)),
    ]
    body = "".join(f"<h2>{_esc(name)}</h2>{content}" for name, content in sections)
    return (
        '<!DOCTYPE html>\n<html lang="en">\n<head>\n'
        f'<meta charset="utf-8">\n<title>{_esc(title)}</title>\n'
        f"<style>{_CSS}</style>\n</head>\n<body>\n"
        f"<h1>{_esc(title)} — {_esc(case.get('id'))}@{_esc(case.get('version'))}</h1>\n"
        f"{body}\n</body>\n</html>\n"
    )


def write_html_report(run_dir: Path) -> Path:
    target = run_dir / "report.html"
    target.write_text(render_report(run_dir), encoding="utf-8")
    target.chmod(0o600)
    return target
