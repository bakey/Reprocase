# Authoring guide: EvalCases, environments, agents, and graders

This guide explains how to write the four artefacts a Reprocase evaluation is
made of: the **EvalCase** (the versioned task definition), the **environment**
(the resettable world the agent acts on), the **agent** (the code under test),
and the **graders** (the deterministic verdict). Everything below is exercised
by the shipped examples in `examples/support-world/`; consult them alongside
this guide.

## The evaluation loop

```text
validate the case
  -> prepare, start, restore, healthcheck the environment
  -> capture the initial state
  -> run the agent (all tool calls through the Tool Broker)
  -> capture the final state and diff it against the initial state
  -> run graders (state assertions, then test commands)
  -> collect logs, tear down, write result.json / events.jsonl / report.html
```

The verdict is decided by **final state and deterministic checks**, never by
the agent's own claims. Every run leaves an evidence bundle under
`.reprocase/runs/<run_id>/` that stands on its own.

## Writing an EvalCase

An EvalCase is a strict YAML document (`api_version: reprocase.dev/v0`,
`kind: EvalCase`). Unknown fields are rejected. The required shape:

```yaml
api_version: reprocase.dev/v0
kind: EvalCase

metadata:
  id: support.refund.standard      # [a-z0-9][a-z0-9._-]{2,127}
  version: 0.1.0                   # semantic version of THIS case definition
  title: Standard refund for ORD-1001
  tags: [support, refund]

task:
  input:                           # what the agent receives as its task argument
    message: "请为订单 ORD-1001 办理退款"
  context:                         # auxiliary, non-secret context
    locale: zh-CN

agent:
  adapter: python                  # fake | scripted | python
  entrypoint: agents.my_agent:run  # module:function, resolved against the case directory

environment:
  driver: docker_compose           # fake | docker_compose
  definition: environment.yaml     # environment document, same directory tree

tools:                             # required for python agents
  provider: tools.my_provider:create_tools
  allow:                           # allowlist; anything else is rejected before the environment
    - order.get
    - refund.create

budgets:
  wall_time_seconds: 60            # agent wall time before AGENT_TIMEOUT
  max_tool_calls: 12               # total tool-call budget
  tool_timeout_seconds: 10         # per-call timeout -> TOOL_TIMEOUT

graders:                           # at least one; all must pass
  - id: refund-created
    type: state_assertion
    critical: true
    assertions:
      - path: /refunds/RF-9001/status
        operator: eq
        expected: completed
```

Rules worth knowing:

- **References stay inside the case directory.** `environment.definition`,
  `compose.file`, agent/tool modules, and grader code resolve relative to the
  case file; `..` escapes are rejected at validation time.
- **Every case has a digest.** The loader canonicalises the case and the
  referenced environment content into a stable `sha256:` digest that lands in
  the run manifest — the same inputs always produce the same identity.
- **Python agents require a `tools` section**; scripted actors do not (they
  receive the environment handle directly and are test fixtures, not agents).
- Validate before running: `uv run reprocase validate path/to/case.yaml`.

## Writing an environment

The environment document (`api_version: reprocase.dev/environment/v0`) tells
the runner how to create a **resettable world**. For Compose:

```yaml
api_version: reprocase.dev/environment/v0
driver: docker_compose

compose:
  file: compose.yaml
  project_prefix: reprocase        # per-run project names are derived from this
  startup_timeout_seconds: 90
  port_variables:                  # host ports are allocated per run and injected
    API_PORT: 8000                 # through a dedicated env file — never hardcode them

restore:                           # deterministic reset command: the heart of R1
  strategy: command
  service: api
  command: ["python", "-m", "app.reset", "/fixtures/refund.json"]

healthcheck:
  type: http
  url: "http://127.0.0.1:${API_PORT}/health"

state:                             # how the runner observes business state
  capture:
    service: api
    command: ["python", "-m", "app.export_state"]   # one JSON document on stdout
  canonicalization:               # make captures comparable across runs
    remove_paths: ["/metadata/generated_at", "/runtime"]
    sort_arrays_by:
      /emails: id
      /audit_log: sequence
```

Design rules that keep restores reproducible:

1. **The restore command must be a pure function of the fixture.** It runs
   after `up` on every run; anything it leaves behind becomes part of your
   initial state digest.
2. **Export the full logical state as one JSON document.** The runner
   canonicalises it (drops `remove_paths`, sorts arrays, sorts keys) and
   digests it; 20 consecutive restores should yield 20 identical digests.
3. **Strip nondeterminism in `canonicalization`**, not in the export command —
   the ignore rules are part of the evidence and covered by the digest.
4. **Compose safety**: privileged mode, host network/PID/IPC, docker socket
   mounts, host root/home/workspace binds, added capabilities, and unpinned
   remote includes are rejected before anything starts (`--allow-unsafe`
   overrides — don't).
5. Volumes are deleted on teardown; nothing leaks between runs.

Use `reprocase env-check environment.yaml --cycles 20` to prove stability.

## Writing an agent

A Python agent is an async `module:function` entrypoint with the shape:

```python
async def run(task, tools, context):
    order = await tools.call("order.get", {"order_id": "ORD-1001"})
    ...
    return {"message": "done"}  # any JSON-serialisable output
```

- `task` is `task.input` from the case; `context` carries `run_id`, `seed`,
  `tool_names`, and an `emit` callback for custom `agent.message` /
  `agent.model.usage` events.
- **`tools.call(name, arguments)` is the only way to touch the world.** The
  broker enforces the allowlist, validates arguments against the tool's JSON
  Schema, decrements the call budget atomically, applies the per-tool timeout,
  and records every call as structured events.
- Rejections surface as a `ToolCallError` raised into your coroutine. You may
  catch it and continue; if it escapes, it becomes the run's `AGENT_FAILURE`
  with the original code (`TOOL_NOT_ALLOWED`, `TOOL_ARGUMENT_INVALID`,
  `TOOL_TIMEOUT`, `AGENT_BUDGET_EXCEEDED`, ...). Infrastructure errors raised
  by the environment or provider stay `INVALID` — they are never your agent's
  business failure.
- Catching a timeout and degrading safely is correct behaviour — see
  `examples/support-world/agents/resilient_agent.py`.
- Agents run as trusted host code in the runner process. Keep entrypoints
  self-contained inside the case directory (the loader origin-checks every
  module level); extra dependencies belong in the environment image.

Tool providers are `module:function` factories building tool objects from the
`EnvironmentHandle` (service URLs carry the per-run ports):

```python
def create_tools(environment):
    base_url = environment.service_urls["api"]
    return [HttpTool(base_url, name="order.get", ..., input_schema={...}), ...]
```

Each tool needs a `name`, `description`, a legal JSON Schema `input_schema`,
and an async `invoke(arguments) -> JSON-serialisable result`.

## Writing graders

Two deterministic grader types exist in Phase 1; the verdict is all-pass —
**any** FAIL fails the run, and `critical` graders are never compensated.

### `state_assertion` — RFC 6901 pointers over the final state

Operators: `eq`, `not_eq`, `exists`, `absent`, `contains`, `gte`, `lte`,
`unchanged` (compares against the initial state), `changed`, `length_eq`.
Float `eq`/`not_eq` require an explicit `tolerance` (otherwise the assertion
is an ERROR — an invalid eval, not a business failure).

Assert **non-target** entities with `unchanged` — catching a wrong-entity
side effect is exactly what final-state grading is for:

```yaml
- id: non-target-orders-unchanged
  type: state_assertion
  assertions:
    - path: /orders/ORD-2002
      operator: unchanged
```

### `test_command` — in-container policy checks

```yaml
- id: policy-tests
  type: test_command
  service: api
  command: ["python", "-m", "evals.grade_refund", "ORD-1001"]
  timeout_seconds: 20
```

The command runs inside the Compose service without a shell. Exit codes: `0`
= pass (stdout must be one JSON document), `1` = FAIL (valid evaluation
failure), anything else = ERROR (the grader itself failed; the run becomes
INVALID). stdout/stderr are stored as content-addressed artifacts and shown
in the report. Write these for invariants that are awkward as single
pointers: "exactly one completed refund", cross-entity linkage, audit
expectations (see `evals/grade_approval.py`).

## Reading the evidence bundle

```text
.reprocase/runs/<run_id>/
  manifest.json        run identity: case digest, agent/tool code digests, git state
  events.jsonl         append-only ordered events (run, environment, tool, agent)
  result.json          verdict, failure attribution, grader results, usage, warnings
  report.html          human-readable single-file report (regenerate: reprocase report)
  state.initial.json   canonical initial state
  state.final.json     canonical final state
  state.diff.json      RFC 6902-style diff between the two
  artifacts/           content-addressed grader output, environment logs
```

Failure attribution tells you whose fault it was: `AGENT_FAILURE` (timeout,
exception, policy breach caught by a grader), `ENVIRONMENT_ERROR`,
`GRADER_ERROR`, `CASE_ERROR`, or `RUNNER_ERROR`. Environment/grader/runner
failures yield `INVALID` — an invalid experiment — never an agent FAIL.

## Checklist for a good EvalCase

1. One clear business task, phrased the way a user would phrase it.
2. A correct agent and **at least one deliberately wrong agent** (wrong
   entity, skipped step, policy violation) that the graders must distinguish.
3. Assert the target change, the notification/communication side effects,
   and the non-target entities staying unchanged.
4. Include one `test_command` grader for cross-entity invariants.
5. Budgets tight enough that a stuck agent actually times out.
6. `reprocase validate` passes and `env-check --cycles 20` is stable.
