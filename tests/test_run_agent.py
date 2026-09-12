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
GOLDEN_INITIAL_DIGEST = "sha256:5189f4f2dd66c9edf848040762f2492c2569a99f6dcccec3bf9511e2f868c753"

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
    assert _residual_resources() == []


def test_wrong_python_agent_fails(tmp_path: Path) -> None:
    result = run_case(AGENT_CASE, tmp_path / "runs", agent_override="agents.wrong_refund_agent:run")

    assert result.verdict == Verdict.FAIL
    assert result.status == RunStatus.COMPLETED
    failed = {grader.grader_id for grader in result.graders if grader.status == "FAIL"}
    assert {"refund-created", "non-target-orders-unchanged", "policy-tests"} <= failed
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
