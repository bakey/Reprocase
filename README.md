# Reprocase

**Turn every agent failure into a reproducible eval.**

[![CI](https://github.com/PhotonDance/Reprocase/actions/workflows/ci.yml/badge.svg)](https://github.com/PhotonDance/Reprocase/actions/workflows/ci.yml)

Reprocase is open-source reproducible execution and evaluation infrastructure for AI agents. It
turns an agent task into a versioned, portable **EvalCase** that binds together the task,
environment, tools, budgets, graders, and evidence needed to answer two questions:

1. Did the agent actually complete the task correctly?
2. Can the result—or the failure—be reproduced and investigated?

The long-term goal is to make agent evaluation behave more like software testing: locally
reproducible, state-aware, debuggable, automatable in CI, and independent of any one model or agent
framework.

## Why Reprocase

Evaluating an agent is harder than evaluating a model response. A plausible final answer does not
prove that the agent changed the right record, respected an approval boundary, avoided duplicate
side effects, or left the system in a valid state.

Existing traces are useful for observability, but a trace alone usually cannot tell you:

- whether the task outcome was correct;
- whether hidden state or external systems were corrupted;
- whether the failure belongs to the agent, environment, grader, or runner;
- whether the same starting conditions can be reconstructed;
- whether a single success represents reliable behavior.

Reprocase is designed around the idea that an agent eval should be an **executable experiment**, not
just a prompt, transcript, or judge score.

## What Reprocase is building

The complete Reprocase system is intended to provide:

- **Versioned EvalCases** — portable specifications for tasks, environments, tools, budgets,
  graders, and expected invariants.
- **Reproducible environments** — isolated, resettable application states backed by Docker and
  pluggable environment drivers.
- **Controlled execution** — a Tool Broker that mediates tool access, validates arguments, enforces
  budgets and policy, and records every action.
- **State-based grading** — assertions over database state, files, APIs, side effects, invariants,
  and deterministic test commands—not only final-answer matching.
- **Evidence bundles** — manifests, events, snapshots, diffs, artifacts, verdicts, and failure
  attribution that can be inspected after every run.
- **Reliability evaluation** — repeated trials, pass-rate distributions, cost and latency metrics,
  perturbations, regression comparisons, and CI quality gates.
- **Failure replay and branching** — reconstruct a run, replay dependencies, and fork from an
  intermediate state to test a fix or a different agent policy.
- **Production-to-eval workflows** — turn sanitized production failures and traces into durable
  regression cases.

Reprocase is meant to stay model-, framework-, and tool-provider agnostic. Agent SDKs and tracing
platforms should be able to integrate through adapters instead of becoming hard dependencies.

## The EvalCase abstraction

An EvalCase describes a testable agent task and everything required to execute and verify it. The
following is an illustrative target shape; the schema will evolve before 1.0 and not every field is
implemented yet.

```yaml
api_version: reprocase.io/v0
kind: EvalCase
metadata:
  name: refund-with-approval

task:
  instruction: Refund order ORD-1042 and notify the customer.

environment:
  ref: ./environment.yaml
  seed: fixtures/refund-case.sql

agent:
  adapter: python
  entrypoint: agents.support:run

tools:
  allow:
    - orders.get
    - refunds.create
    - email.send

budgets:
  max_steps: 20
  max_tool_calls: 12
  timeout_seconds: 120

graders:
  - type: state_assertion
    target: database
    expect: refund_created_once
  - type: invariant
    expect: no_refund_without_approval
  - type: side_effect_assertion
    expect: exactly_one_customer_email
```

The intended execution flow is:

```text
EvalCase
   │
   ▼
Runner ──────► Environment Driver ──────► isolated application state
   │
   ├─────────► Agent Adapter ──► Tool Broker ──► tools and dependencies
   │
   ├─────────► State Capture + Graders
   │
   ▼
Evidence Bundle ──► verdict, attribution, report, replay inputs
```

## Design principles

| Principle | Meaning |
|---|---|
| Verify state, not rhetoric | The real outcome and side effects matter more than a persuasive final response. |
| Evidence before scores | Every verdict should be explainable from durable, inspectable evidence. |
| Reproduction before replay | Restore equivalent starting conditions before attempting exact trajectory replay. |
| Separate failure domains | Agent failures must be distinguishable from environment, grader, and runner errors. |
| Measure distributions | Stochastic agents should be evaluated across trials, not trusted after one successful run. |
| Open, portable contracts | Eval definitions and evidence formats should work across models, frameworks, and vendors. |
| Local-first, CI-native | A developer should be able to reproduce a failure locally and run the same case in automation. |

## Reproducibility levels

Reprocase treats reproducibility as a ladder rather than an all-or-nothing promise:

- **R0 — Evidence reproducibility:** preserve versioned inputs, configuration, events, outputs, and
  contracts so the run can be audited.
- **R1 — Environment reproducibility:** restore an equivalent initial environment and execute the
  case again.
- **R2 — Dependency replay:** record and replay selected tool or external dependency interactions.
- **R3 — Trajectory branching:** resume or fork from a captured intermediate state to compare
  alternative actions, models, or policies.

M0 establishes R0 contracts and evidence. Later milestones build toward R1–R3.

## Intended use cases

- Convert a production agent failure into a permanent regression test.
- Compare models, prompts, policies, or agent implementations on the same initial state.
- Gate agent releases on task success, invariants, cost, latency, and reliability thresholds.
- Diagnose whether a regression came from the agent or from its execution infrastructure.
- Validate workflow agents that modify databases, files, tickets, messages, or other external state.
- Build reusable, auditable eval suites for internal teams, vendors, or public benchmarks.

## Roadmap

The first phase is intentionally split into small milestones so that each one produces a usable,
testable increment.

| Milestone | Outcome | Status |
|---|---|---|
| M0 — Contracts and run skeleton | Versioned schemas, validation, lifecycle, evidence writer, deterministic fake run | **Complete** |
| M1 — Reproducible environment | Docker Compose lifecycle, safety precheck, dynamic ports, reset, state digests, cleanup | **Complete** |
| M2 — Deterministic evaluation loop | Scripted actor, before/after snapshots, state diff, deterministic graders | Planned |
| M3 — Real agent execution | Python agent adapter, controlled Tool Broker, budgets, tool-call evidence | Planned |
| M4 — Evidence and resilience | Reports, artifacts, cancellation, timeouts, crash recovery, stronger failure attribution | Planned |
| M5 — External alpha | Example eval pack, documentation, packaging, CI workflow, first outside users | Planned |

Beyond Phase 1, the roadmap includes enterprise workflow eval packs, multi-trial statistics,
perturbation testing, regression gates, dependency recording and replay, trajectory branching, and a
production trace-to-EvalCase workflow.

## Quickstart for the current M0

### Requirements

- Python 3.12+
- [`uv`](https://docs.astral.sh/uv/) (recommended)

Install the development environment and run a deterministic example:

```bash
uv sync --extra dev
uv run reprocase validate examples/fake/eval-pass.yaml
uv run reprocase run examples/fake/eval-pass.yaml
```

The run writes an evidence bundle to `.reprocase/runs/<run_id>/`:

```text
manifest.json   # resolved run identity, versions, inputs, and lifecycle metadata
events.jsonl    # append-only, ordered execution events
result.json     # verdict, failure attribution, metrics, and error details
```

Try the other deterministic outcomes:

```bash
uv run reprocase run examples/fake/eval-fail.yaml
uv run reprocase run examples/fake/eval-invalid.yaml
```

### M1: reproducible environment check

With a running Docker daemon, exercise the real Compose environment lifecycle (start → restore →
healthcheck → state capture → teardown) and verify that repeated restores yield identical state
digests:

```bash
uv run reprocase env-check examples/support-world/environment.yaml --cycles 3
```

Current CLI exit codes:

| Code | Meaning |
|---:|---|
| 0 | Valid run with a PASS verdict |
| 1 | Valid run with a FAIL verdict |
| 2 | Invalid EvalCase or command usage |
| 3 | Environment, grader, or runner error; verdict is INVALID |
| 4 | User cancellation |

## Repository layout

```text
src/reprocase/     Python package, contracts, runner, recording, and fake adapters
schemas/           Exported JSON Schemas for public contracts
examples/fake/     Deterministic M0 examples
examples/support-world/  Minimal Compose environment (API, fixture, reset/export commands)
tests/             Contract, runner, recording, schema, environment, and CLI tests
docs/              Milestone-specific design notes
```

## Development

```bash
uv sync --extra dev
uv run pytest
uv run ruff check .
uv run mypy src
uv run python scripts/export_schemas.py --check
```

Reprocase is at the beginning of its development. Issues, design feedback, integrations, eval cases,
and real-world failure examples are welcome—especially from teams already operating agents against
stateful tools or business workflows.

## License

[Apache License 2.0](LICENSE)
