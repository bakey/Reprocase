# Reprocase

Reprocase is reproducible execution and evaluation infrastructure for AI agents.

This repository currently contains **Phase 1 / M0**: the contracts and local run skeleton that
later milestones will extend with Docker environments, state capture, deterministic graders, and
real agent adapters.

## What M0 includes

- Strict `EvalCase v0` and `Environment v0` schemas.
- Static and executable validation with precise field/path errors.
- A deterministic case digest over the case and referenced environment definition.
- Run IDs, lifecycle state machine, and stable failure taxonomy.
- Atomic local storage for `manifest.json` and `result.json`.
- Append-only `events.jsonl` with monotonic sequence numbers.
- Fake Environment, Agent, and Grader components for an end-to-end run.
- Committed JSON Schemas and contract tests.

M0 intentionally has no Docker or model dependency. Its purpose is to stabilize the contracts and
prove that an interrupted or failed run still produces useful evidence.

## Requirements

- Python 3.12+
- [`uv`](https://docs.astral.sh/uv/) is recommended

## Quickstart

```bash
uv sync --extra dev
uv run reprocase validate examples/fake/eval-pass.yaml
uv run reprocase run examples/fake/eval-pass.yaml
```

The run command writes an evidence bundle to `.reprocase/runs/<run_id>/`:

```text
manifest.json
events.jsonl
result.json
```

Try the other deterministic outcomes:

```bash
uv run reprocase run examples/fake/eval-fail.yaml
uv run reprocase run examples/fake/eval-invalid.yaml
```

Expected CLI exit codes:

| Code | Meaning |
|---:|---|
| 0 | Valid run with a PASS verdict |
| 1 | Valid run with a FAIL verdict |
| 2 | Invalid EvalCase or command usage |
| 3 | Environment, grader, or runner error; verdict is INVALID |
| 4 | User cancellation |

## Development

```bash
uv run pytest
uv run ruff check .
uv run mypy src
uv run python scripts/export_schemas.py --check
```

## M0 boundary

The fake components are contract fixtures, not production implementations. The next milestones are:

1. M1 — reproducible Docker Compose environment lifecycle.
2. M2 — state snapshots, diffs, and deterministic graders.
3. M3 — real Python agents and a controlled Tool Broker.

See [docs/m0.md](docs/m0.md) for the exact acceptance criteria and design decisions.

## License

Apache-2.0
