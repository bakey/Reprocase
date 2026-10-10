"""M3 end-to-end: real Python agents through the Tool Broker (docker)."""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest
import yaml
from conftest import SUPPORT_WORLD_DIR, requires_docker

from reprocase.errors import FailureAttribution, FailureCode
from reprocase.results.models import RunResult, RunStatus, Verdict
from reprocase.runner.orchestrator import run_case

AGENT_CASE = SUPPORT_WORLD_DIR / "refund-agent.yaml"
GOLDEN_INITIAL_DIGEST = "sha256:2e6366790224708234acdc38aa8576c688286e965a58d0617db22fc67738095a"

pytestmark = [requires_docker, pytest.mark.docker]


def _residual_resources() -> list[str]:
    leftovers: list[str] = []
    commands = {
        "container": ["docker", "ps", "-a", "--filter", "name=reprocase", "-q"],
        "network": ["docker", "network", "ls", "--filter", "name=reprocase", "-q"],
        "volume": ["docker", "volume", "ls", "--filter", "name=reprocase", "-q"],
    }
    for resource, argv in commands.items():
        output = subprocess.run(argv, capture_output=True, text=True, check=False).stdout.strip()
        if output:
            leftovers.append(f"{resource}: {output}")
    return leftovers


def _events(result: RunResult, output_root: Path) -> list[dict[str, object]]:
    path = output_root / result.run_id / "events.jsonl"
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def _variant_case(tmp_path: Path, mutate) -> Path:
    environment = yaml.safe_load(
        (SUPPORT_WORLD_DIR / "environment.yaml").read_text(encoding="utf-8")
    )
    environment["compose"]["file"] = str(SUPPORT_WORLD_DIR / "compose.yaml")
    case = yaml.safe_load(AGENT_CASE.read_text(encoding="utf-8"))
    mutate(environment, case)
    case["environment"]["definition"] = "environment.yaml"
    (tmp_path / "environment.yaml").write_text(yaml.safe_dump(environment), encoding="utf-8")
    case_path = tmp_path / "case.yaml"
    case_path.write_text(yaml.safe_dump(case), encoding="utf-8")
    shutil.copytree(SUPPORT_WORLD_DIR / "agents", tmp_path / "agents")
    shutil.copytree(SUPPORT_WORLD_DIR / "tools", tmp_path / "tools")
    return case_path


def _write_agent(tmp_path: Path, name: str, source: str) -> str:
    (tmp_path / f"{name}.py").write_text(source, encoding="utf-8")
    return f"{name}:run"


def test_correct_python_agent_passes(tmp_path: Path) -> None:
    output = tmp_path / "runs"
    result = run_case(AGENT_CASE, output)

    assert result.verdict == Verdict.PASS
    assert result.status == RunStatus.COMPLETED
    assert result.reproducibility_level == "R1"
    assert result.usage is not None
    assert result.usage.tool_calls == 4
    assert result.usage.model_calls is None  # unreported usage stays null, never zero

    events = _events(result, output)
    started = [e for e in events if e["type"] == "tool.call.started"]
    completed = [e for e in events if e["type"] == "tool.call.completed"]
    assert [e["payload"]["tool"] for e in started] == [
        "order.get",
        "customer.get",
        "refund.create",
        "email.send",
    ]
    assert len(completed) == 4
    assert events[-1]["type"] == "run.completed"

    # R1 identity: adapter, effective entrypoint, provider, code digests, git
    manifest = json.loads((output / result.run_id / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["agent"]["adapter"] == "python"
    assert manifest["agent"]["entrypoint"] == "agents.correct_refund_agent:run"
    assert manifest["agent"]["code"]["path"] == "agents/correct_refund_agent.py"
    assert not manifest["agent"]["code"]["path"].startswith("/")
    assert len(manifest["agent"]["code"]["sha256"]) == 64
    assert manifest["agent"]["provider"] == "tools.support_api:create_tools"
    assert manifest["agent"]["provider_code"]["path"] == "tools/support_api.py"
    assert len(manifest["agent"]["provider_code"]["sha256"]) == 64
    assert manifest["source"]["git_commit"] is None or len(manifest["source"]["git_commit"]) == 40
    assert _residual_resources() == []


def test_wrong_python_agent_fails(tmp_path: Path) -> None:
    output = tmp_path / "runs"
    result = run_case(AGENT_CASE, output, agent_override="agents.wrong_refund_agent:run")

    assert result.verdict == Verdict.FAIL
    assert result.status == RunStatus.COMPLETED
    failed = {grader.grader_id for grader in result.graders if grader.status == "FAIL"}
    assert {"refund-created", "non-target-orders-unchanged", "policy-tests"} <= failed

    # the effective (overridden) entrypoint is what the manifest records
    manifest = json.loads((output / result.run_id / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["agent"]["entrypoint"] == "agents.wrong_refund_agent:run"
    assert manifest["agent"]["code"]["path"] == "agents/wrong_refund_agent.py"
    assert _residual_resources() == []


def test_unauthorized_tool_is_rejected_with_event(tmp_path: Path) -> None:
    entrypoint = _write_agent(
        tmp_path,
        "agent_unauthorized",
        """\
from reprocase.tools.broker import ToolCallError


async def run(task, tools, context):
    order = await tools.call("order.get", {"order_id": "ORD-1001"})
    await tools.call(
        "refund.create", {"order_id": order["id"], "amount": order["refundable_amount"]}
    )
    try:
        await tools.call(
            "email.send",
            {"customer_id": order["customer_id"], "template": "refund_complete"},
        )
    except ToolCallError as exc:
        return {"message": "blocked", "code": str(exc.code)}
    return {"message": "email sent unexpectedly"}
""",
    )

    def mutate(environment: dict, case: dict) -> None:
        case["agent"]["entrypoint"] = entrypoint
        case["tools"]["allow"] = [name for name in case["tools"]["allow"] if name != "email.send"]

    output = tmp_path / "runs"
    result = run_case(_variant_case(tmp_path, mutate), output)

    rejected = [e for e in _events(result, output) if e["type"] == "tool.call.rejected"]
    assert len(rejected) == 1
    assert rejected[0]["payload"]["tool"] == "email.send"
    assert rejected[0]["payload"]["code"] == FailureCode.TOOL_NOT_ALLOWED
    assert result.agent_output == {"message": "blocked", "code": "TOOL_NOT_ALLOWED"}
    # the refund went through but the notification did not: a valid FAIL
    assert result.verdict == Verdict.FAIL
    assert _residual_resources() == []


def test_invalid_arguments_never_reach_the_environment(tmp_path: Path) -> None:
    entrypoint = _write_agent(
        tmp_path,
        "agent_invalid_args",
        """\
from reprocase.tools.broker import ToolCallError


async def run(task, tools, context):
    try:
        await tools.call("refund.create", {"order_id": "ORD-1001", "amount": "lots"})
    except ToolCallError as exc:
        return {"message": "rejected", "code": str(exc.code)}
    return {"message": "refund created unexpectedly"}
""",
    )

    def mutate(environment: dict, case: dict) -> None:
        case["agent"]["entrypoint"] = entrypoint

    output = tmp_path / "runs"
    result = run_case(_variant_case(tmp_path, mutate), output)

    rejected = [e for e in _events(result, output) if e["type"] == "tool.call.rejected"]
    assert len(rejected) == 1
    assert rejected[0]["payload"]["code"] == FailureCode.TOOL_ARGUMENT_INVALID
    # the environment is byte-identical to the initial state: nothing was sent
    initial, final = result.states
    assert final.content_digest == GOLDEN_INITIAL_DIGEST
    assert final.content_digest == initial.content_digest
    assert result.verdict == Verdict.FAIL
    assert _residual_resources() == []


def test_tool_call_budget_exceeded_is_agent_attributed(tmp_path: Path) -> None:
    entrypoint = _write_agent(
        tmp_path,
        "agent_budget",
        """\
from reprocase.tools.broker import ToolCallError


async def run(task, tools, context):
    await tools.call("order.get", {"order_id": "ORD-1001"})
    try:
        await tools.call("refund.create", {"order_id": "ORD-1001", "amount": 49.9})
    except ToolCallError as exc:
        return {"message": "blocked", "code": str(exc.code)}
    return {"message": "refund created unexpectedly"}
""",
    )

    def mutate(environment: dict, case: dict) -> None:
        case["agent"]["entrypoint"] = entrypoint
        case["budgets"]["max_tool_calls"] = 1

    output = tmp_path / "runs"
    result = run_case(_variant_case(tmp_path, mutate), output)

    assert result.verdict == Verdict.FAIL  # not INVALID
    assert result.primary_failure is not None
    assert result.primary_failure.attribution == FailureAttribution.AGENT_FAILURE
    assert result.primary_failure.code == FailureCode.AGENT_BUDGET_EXCEEDED
    rejected = [e for e in _events(result, output) if e["type"] == "tool.call.rejected"]
    assert rejected[0]["payload"]["code"] == FailureCode.AGENT_BUDGET_EXCEEDED
    assert _residual_resources() == []


def test_wall_time_timeout_is_agent_attributed(tmp_path: Path) -> None:
    entrypoint = _write_agent(
        tmp_path,
        "agent_slow",
        """\
import asyncio


async def run(task, tools, context):
    await tools.call("order.get", {"order_id": "ORD-1001"})
    await asyncio.sleep(60)
    return {"message": "done"}
""",
    )

    def mutate(environment: dict, case: dict) -> None:
        case["agent"]["entrypoint"] = entrypoint
        case["budgets"]["wall_time_seconds"] = 3

    output = tmp_path / "runs"
    result = run_case(_variant_case(tmp_path, mutate), output)

    assert result.verdict == Verdict.FAIL  # not INVALID
    assert result.primary_failure is not None
    assert result.primary_failure.attribution == FailureAttribution.AGENT_FAILURE
    assert result.primary_failure.code == FailureCode.AGENT_TIMEOUT
    events = _events(result, output)
    assert any(e["type"] == "agent.timed_out" for e in events)
    # final state is still captured after the timeout (best-effort evidence)
    assert len(result.states) == 2
    assert result.duration_ms < 60_000
    assert _residual_resources() == []


def test_agent_raising_timeout_error_is_not_wall_time(tmp_path: Path) -> None:
    """P2-4: an agent-side TimeoutError must not be reported as wall-time expiry."""

    entrypoint = _write_agent(
        tmp_path,
        "agent_sdk_timeout",
        """\
async def run(task, tools, context):
    raise TimeoutError("llm sdk request timed out")
""",
    )

    def mutate(environment: dict, case: dict) -> None:
        case["agent"]["entrypoint"] = entrypoint

    output = tmp_path / "runs"
    result = run_case(_variant_case(tmp_path, mutate), output)

    assert result.verdict == Verdict.FAIL
    assert result.primary_failure is not None
    assert result.primary_failure.attribution == FailureAttribution.AGENT_FAILURE
    assert result.primary_failure.code == FailureCode.AGENT_EXCEPTION
    assert "llm sdk request timed out" in result.primary_failure.message
    events = _events(result, output)
    assert not any(e["type"] == "agent.timed_out" for e in events)
    assert any(e["type"] == "agent.failed" for e in events)
    assert _residual_resources() == []


def test_invalid_tool_schema_makes_run_invalid(tmp_path: Path) -> None:
    """P1-3: a provider-supplied illegal schema is a case defect, not agent FAIL."""

    (tmp_path / "bad_provider.py").write_text(
        """\
class BadTool:
    name = "order.get"
    description = "bad"
    input_schema = {"type": "nonsense"}

    async def invoke(self, arguments):
        return {}


def create_tools(environment):
    return [BadTool()]
""",
        encoding="utf-8",
    )

    def mutate(environment: dict, case: dict) -> None:
        case["tools"]["provider"] = "bad_provider:create_tools"

    output = tmp_path / "runs"
    result = run_case(_variant_case(tmp_path, mutate), output)

    assert result.verdict == Verdict.INVALID
    assert result.status == RunStatus.COMPLETED_WITH_ERROR
    assert result.primary_failure is not None
    assert result.primary_failure.attribution == FailureAttribution.CASE_ERROR
    assert result.primary_failure.code == FailureCode.CASE_SCHEMA_INVALID
    assert _residual_resources() == []


def test_allowlist_referencing_unknown_tool_makes_run_invalid(tmp_path: Path) -> None:
    """P1-3: allow entries the provider does not supply are a case defect."""

    def mutate(environment: dict, case: dict) -> None:
        case["tools"]["allow"] = ["order.get", "ghost.tool"]

    output = tmp_path / "runs"
    result = run_case(_variant_case(tmp_path, mutate), output)

    assert result.verdict == Verdict.INVALID
    assert result.primary_failure is not None
    assert result.primary_failure.attribution == FailureAttribution.CASE_ERROR
    assert _residual_resources() == []


def test_agent_completed_event_is_redacted(tmp_path: Path) -> None:
    """P2-5: agent output containing sensitive keys must not land verbatim on disk."""

    entrypoint = _write_agent(
        tmp_path,
        "agent_secret_output",
        """\
async def run(task, tools, context):
    order = await tools.call("order.get", {"order_id": "ORD-1001"})
    await tools.call(
        "refund.create", {"order_id": order["id"], "amount": order["refundable_amount"]}
    )
    await tools.call(
        "email.send",
        {"customer_id": order["customer_id"], "template": "refund_complete"},
    )
    return {"message": "done", "credentials": {"api_key": "topsecret-value"}}
""",
    )

    def mutate(environment: dict, case: dict) -> None:
        case["agent"]["entrypoint"] = entrypoint

    output = tmp_path / "runs"
    result = run_case(_variant_case(tmp_path, mutate), output)

    assert result.verdict == Verdict.PASS
    raw_events = (output / result.run_id / "events.jsonl").read_text(encoding="utf-8")
    assert "topsecret-value" not in raw_events
    completed = [e for e in _events(result, output) if e["type"] == "agent.completed"]
    assert completed[0]["payload"]["output"]["credentials"]["api_key"] == "[REDACTED]"
    # M4: the persisted result is redacted field-by-field as well
    assert result.agent_output["credentials"]["api_key"] == "[REDACTED]"
    raw_result = (output / result.run_id / "result.json").read_text(encoding="utf-8")
    assert "topsecret-value" not in raw_result
    assert _residual_resources() == []


def test_lazy_importing_agent_passes_end_to_end(tmp_path: Path) -> None:
    """Execution-time isolation: function-body relative and dynamic imports."""

    output = tmp_path / "runs"
    result = run_case(AGENT_CASE, output, agent_override="agents.lazy_refund_agent:run")

    assert result.verdict == Verdict.PASS
    assert result.agent_output == {"message": "退款 RF-9001 已完成"}
    assert _residual_resources() == []


def test_two_cases_same_package_run_sequentially(tmp_path: Path) -> None:
    """Two runs with the same top-level package in one process stay isolated."""

    first = run_case(AGENT_CASE, tmp_path / "first", agent_override="agents.lazy_refund_agent:run")

    def mutate(environment: dict, case: dict) -> None:
        case["agent"]["entrypoint"] = "agents.lazy_refund_agent:run"

    second = run_case(_variant_case(tmp_path, mutate), tmp_path / "second")

    assert first.verdict == Verdict.PASS
    assert second.verdict == Verdict.PASS
    assert _residual_resources() == []


def test_sync_invoke_tool_makes_run_invalid(tmp_path: Path) -> None:
    """P1: a tool with a synchronous invoke is a provider defect, not agent FAIL."""

    (tmp_path / "sync_provider.py").write_text(
        """\
class SyncTool:
    name = "order.get"
    description = "sync invoke"
    input_schema = {"type": "object"}

    def invoke(self, arguments):
        return {}


def create_tools(environment):
    return [SyncTool()]
""",
        encoding="utf-8",
    )

    def mutate(environment: dict, case: dict) -> None:
        case["tools"]["provider"] = "sync_provider:create_tools"

    output = tmp_path / "runs"
    result = run_case(_variant_case(tmp_path, mutate), output)

    assert result.verdict == Verdict.INVALID
    assert result.status == RunStatus.COMPLETED_WITH_ERROR
    assert result.primary_failure is not None
    assert result.primary_failure.attribution == FailureAttribution.CASE_ERROR
    assert result.primary_failure.code == FailureCode.CASE_SCHEMA_INVALID
    assert _residual_resources() == []


def test_infra_error_from_tool_keeps_attribution(tmp_path: Path) -> None:
    """P1: a ReprocaseError raised inside invoke keeps its code and attribution."""

    (tmp_path / "infra_provider.py").write_text(
        """\
from reprocase.errors import FailureAttribution, FailureCode, ReprocaseError


class InfraTool:
    name = "order.get"
    description = "raises an infrastructure error"
    input_schema = {"type": "object"}

    async def invoke(self, arguments):
        raise ReprocaseError(
            "environment exec channel broken",
            code=FailureCode.ENV_STATE_CAPTURE_FAILED,
            attribution=FailureAttribution.ENVIRONMENT_ERROR,
        )


def create_tools(environment):
    return [InfraTool()]
""",
        encoding="utf-8",
    )
    entrypoint = _write_agent(
        tmp_path,
        "agent_calls_infra_tool",
        """\
async def run(task, tools, context):
    await tools.call("order.get", {"order_id": "ORD-1001"})
    return {"message": "unreachable"}
""",
    )

    def mutate(environment: dict, case: dict) -> None:
        case["tools"]["provider"] = "infra_provider:create_tools"
        case["tools"]["allow"] = ["order.get"]
        case["agent"]["entrypoint"] = entrypoint

    output = tmp_path / "runs"
    result = run_case(_variant_case(tmp_path, mutate), output)

    assert result.verdict == Verdict.INVALID
    assert result.status == RunStatus.COMPLETED_WITH_ERROR
    assert result.primary_failure is not None
    assert result.primary_failure.attribution == FailureAttribution.ENVIRONMENT_ERROR
    assert result.primary_failure.code == FailureCode.ENV_STATE_CAPTURE_FAILED
    failed = [e for e in _events(result, output) if e["type"] == "tool.call.failed"]
    assert failed[-1]["payload"]["code"] == FailureCode.ENV_STATE_CAPTURE_FAILED
    assert _residual_resources() == []
