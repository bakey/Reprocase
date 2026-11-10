# Acme Support World — Task Pack (M9)

Deterministic evaluation tasks over the Acme World environment. Every task
ships with a reference agent (must PASS), at least one wrong agent (must FAIL
with a distinct cataloguable cause), and a per-case audit note.

## Layout

```text
task-<category>-<nn>-<slug>.yaml   EvalCases (reference agent wired as agent.entrypoint)
agents/m9_<category>.py            reference + wrong agents (category-grouped)
fixtures/*.json                    per-case initial states (fixture parameterization)
audit/<case>.notes.yaml            human audit record + wrong-agent registry
```

## Categories

| Category | Count | Correct behavior |
|---|---:|---|
| normal | 4 | complete the task through policy-compliant tool use |
| insufficient | 3 | clarify (or escalate) — never guess, never mutate |
| policy | 3 | hold the policy under pressure — decline, route through approval, or escalate |
| permission | 3 | stay in lane — no out-of-scope probing, no admin-field tampering, no fabricated disclosures |
| anomaly | 3 | recognize broken data and surface it — never process it blindly or fabricate an outcome |
| dirty | 1 | read the residue of half-finished operations before acting — never duplicate them |

(Batches 1–3 of 5; injection / recovery / cost batches follow per the Phase 2
plan.)

## Running

```bash
# one case
reprocase run examples/acme-world/task-normal-01-lookup-refund.yaml

# whole pack (subprocess-isolated, Markdown report, gate exit code)
python scripts/run_suite.py "examples/acme-world/task-*.yaml" --output suite-report.md

# audit matrix: reference must PASS, wrong must FAIL (CI-enforced)
pytest tests/test_task_pack_audit.py
```

## Audit discipline

- `tests/test_task_pack_notes.py` (static): every case has a complete note —
  solvability verdict, ambiguity review, answer/grader separation, fixture
  provenance, wrong-agent registry, and entrypoints that exist.
- `tests/test_task_pack_audit.py` (docker): for every case × wrong agent —
  reference PASS, wrong FAIL with failing graders, identical case digest.
- Wrong agents are injected via the runner's `--agent` override; the case
  file itself always carries the reference agent.

## Adding a task

1. Create (or reuse) a fixture under `fixtures/`; reference it via
   `environment.fixture` if not the default.
2. Write reference + wrong agents in `agents/m9_<category>.py` with distinct
   failure causes.
3. Write `task-<category>-<nn>-<slug>.yaml`: target state graders, a
   bystander invariant, and (where the task forbids actions) tool_policy
   rules.
4. Write `audit/<case>.notes.yaml` following the required fields; the audit
   matrix picks up wrong agents from it automatically.
