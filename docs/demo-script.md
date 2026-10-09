# Demo script: Reprocase in five minutes

A narrated walkthrough for showing (or recording) the Phase 1 closed loop:
reproducible environment, controlled agent execution, state-based grading,
and failure evidence. Assumes the repo is checked out with `uv`, Docker
running, and commands executed from the repository root.

## 1. The pitch (30s)

> AI agents that call tools change real state — databases, tickets, orders.
> "The answer looked right" proves nothing. Reprocase turns an agent task
> into a versioned EvalCase: the same environment is restored before every
> run, every tool call goes through a broker with budgets, and the verdict
> comes from the final state, not the agent's claims. Every run leaves an
> evidence bundle you can read — and rerun.

## 2. A deterministic warm-up (30s)

```bash
uv run reprocase validate examples/support-world/refund-agent.yaml
uv run reprocase run examples/fake/eval-pass.yaml
```

Point out: exit code 0, and the evidence bundle path printed at the end.
`validate` catches malformed cases before anything starts.

## 3. The environment is genuinely resettable (60s)

```bash
uv run reprocase env-check examples/support-world/environment.yaml --cycles 3
```

Each cycle: start containers, run the deterministic reset, healthcheck,
capture the canonical state, tear everything down. Show the identical
`sha256:` digests — that is reproducibility level R1, and the same machinery
runs before every eval.

## 4. Correct agent passes (60s)

```bash
uv run reprocase run examples/support-world/refund-agent.yaml
```

Open the printed run directory (or `open <path>/report.html`): verdict, tool
timeline with arguments and results, state digests, the diff between initial
and final state, grader-by-grader expected vs actual, budgets and usage, and
the rerun command.

## 5. Wrong agents fail — and the report says why (90s)

```bash
uv run reprocase run examples/support-world/refund-agent.yaml \
  --agent agents.wrong_refund_agent:run        # wrong entity: ORD-2002 refunded
uv run reprocase run examples/support-world/refund-approval.yaml \
  --agent agents.approval_bypass_agent:run     # policy: high amount without approval
```

In the first report, the non-target assertion `/orders/ORD-2002 unchanged`
is the one that fails — final-state grading caught a side effect the agent's
own output never mentions. In the second, the tool call failed with the
policy 403, no refund exists, and the attribution is `AGENT_FAILURE` — the
agent's policy breach, not an infrastructure error.

## 6. Resilience is graded, not assumed (60s)

```bash
uv run reprocase run examples/support-world/tool-timeout.yaml \
  --agent agents.fragile_agent:run     # lets TOOL_TIMEOUT escape -> FAIL
uv run reprocase run examples/support-world/tool-timeout.yaml            # catches it, falls back -> PASS
```

Same slow dependency, two agents: the fragile one fails with
`AGENT_TIMEOUT`-style attribution (`TOOL_TIMEOUT`), the resilient one
degrades safely and still passes. Both reports show the timed-out call and
the fallback email in the event timeline.

## 7. Diagnostics and cancellation (30s)

```bash
uv run reprocase doctor          # Python/Docker/Compose checks + stale projects
```

Mention: Ctrl-C mid-run leaves a diagnosable CANCELLED record with a cleanup
command if teardown could not finish; nothing silent, nothing orphaned.

## Closing line

> Three cases, one runner: environment reproducibility, controlled
> execution, deterministic grading, and evidence you can attach to a bug
> report. That is the Phase 1 proposition — the rest of the roadmap (trials,
> replay, production-trace conversion) builds on this loop.
