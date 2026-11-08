"""Review-round robustness regressions: watchdogs, signals, fault injection."""

from __future__ import annotations

import asyncio
import json
import os
import shlex
import shutil
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest
import yaml
from conftest import SUPPORT_WORLD_DIR, requires_docker, residual_resources
from signal_helpers import interrupt_next_loop_turn

from reprocase.environments.compose import ComposeEnvironmentDriver
from reprocase.environments.protocol import EnvironmentHandle
from reprocase.errors import EnvironmentDriverError, FailureAttribution, FailureCode
from reprocase.results.models import RunStatus, Verdict
from reprocase.runner.orchestrator import run_case

AGENT_CASE = SUPPORT_WORLD_DIR / "refund-agent.yaml"
REFUND_CASE = SUPPORT_WORLD_DIR / "refund.yaml"

pytestmark = [requires_docker, pytest.mark.docker]


def _variant_case(tmp_path: Path, mutate, base: Path = AGENT_CASE) -> Path:
    environment = yaml.safe_load(
        (SUPPORT_WORLD_DIR / "environment.yaml").read_text(encoding="utf-8")
    )
    environment["compose"]["file"] = str(SUPPORT_WORLD_DIR / "compose.yaml")
    case = yaml.safe_load(base.read_text(encoding="utf-8"))
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


def _events_text(run_dir: Path) -> str:
    events_file = run_dir / "events.jsonl"
    # The run directory is created before the first event is written; pollers
    # must tolerate the gap (CI hit FileNotFoundError here).
    if not events_file.exists():
        return ""
    return events_file.read_text(encoding="utf-8")


def test_immortal_agent_still_persists_result(tmp_path: Path) -> None:
    """P1-1: an agent that never honours cancellation cannot block result.json."""

    entrypoint = _write_agent(
        tmp_path,
        "agent_immortal",
        """\
import asyncio


async def run(task, tools, context):
    while True:
        try:
            await asyncio.sleep(100)
        except asyncio.CancelledError:
            continue
""",
    )

    def mutate(environment: dict, case: dict) -> None:
        case["agent"]["entrypoint"] = entrypoint
        case["budgets"]["wall_time_seconds"] = 2

    output = tmp_path / "runs"
    started = time.monotonic()
    process = subprocess.run(
        [
            sys.executable,
            "-m",
            "reprocase.cli",
            "run",
            str(_variant_case(tmp_path, mutate)),
            "-o",
            str(output),
        ],
        capture_output=True,
        text=True,
        timeout=120,  # watchdog: must exit in bounded time
        check=False,
    )
    elapsed = time.monotonic() - started

    assert process.returncode == 1, process.stdout[-1000:]
    assert elapsed < 120
    run_dirs = [path for path in output.iterdir() if path.is_dir()]
    assert len(run_dirs) == 1, "result.json was never written (watchdog fired)"
    result = json.loads((run_dirs[0] / "result.json").read_text(encoding="utf-8"))
    assert result["primary_failure"]["code"] == "AGENT_TIMEOUT"
    assert result["primary_failure"]["details"]["ignored_cancellation"] is True
    assert any("ignored cancellation" in warning for warning in result["warnings"])
    assert residual_resources() == []


def test_custom_redaction_keys_cover_events_result_and_report(tmp_path: Path) -> None:
    """P1-2: one redaction policy for broker events, agent events, result, HTML."""

    entrypoint = _write_agent(
        tmp_path,
        "agent_leaks_secret",
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
    await context.emit({"note": "progress", "session_secret": "raw-secret-value"})
    return {"message": "done", "session_secret": "raw-secret-value"}
""",
    )

    def mutate(environment: dict, case: dict) -> None:
        case["agent"]["entrypoint"] = entrypoint
        case["output"] = {"redaction": {"keys": ["session_secret"]}}

    output = tmp_path / "runs"
    result = run_case(_variant_case(tmp_path, mutate), output)
    assert result.verdict == Verdict.PASS
    run_dir = output / result.run_id

    assert "raw-secret-value" not in (run_dir / "result.json").read_text(encoding="utf-8")
    events = _events_text(run_dir)
    assert "raw-secret-value" not in events
    assert "[REDACTED]" in events
    report = (run_dir / "report.html").read_text(encoding="utf-8")
    assert "raw-secret-value" not in report
    # regenerated report is built from the same redacted evidence
    from reprocase.results.report import write_html_report

    assert "raw-secret-value" not in write_html_report(run_dir).read_text(encoding="utf-8")
    assert residual_resources() == []


def test_sigint_stops_agent_before_teardown(tmp_path: Path) -> None:
    """P1-3: on cancellation the broker closes and the agent is cancelled first."""

    entrypoint = _write_agent(
        tmp_path,
        "agent_late_call_after_cancel",
        """\
import asyncio


async def run(task, tools, context):
    try:
        await asyncio.sleep(300)
    except asyncio.CancelledError:
        try:
            await tools.call("refund.create", {"order_id": "ORD-1001", "amount": 1})
        except Exception as exc:
            return {"message": f"late call blocked: {type(exc).__name__}"}
    return {"message": "late call went through"}
""",
    )

    def mutate(environment: dict, case: dict) -> None:
        case["agent"]["entrypoint"] = entrypoint
        case["budgets"]["wall_time_seconds"] = 300

    output = tmp_path / "runs"
    process = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "reprocase.cli",
            "run",
            str(_variant_case(tmp_path, mutate)),
            "-o",
            str(output),
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    try:
        deadline = time.monotonic() + 120
        run_dir: Path | None = None
        while time.monotonic() < deadline:
            candidates = (
                [path for path in output.iterdir() if path.is_dir()] if output.exists() else []
            )
            if candidates and "agent.started" in _events_text(candidates[0]):
                run_dir = candidates[0]
                break
            time.sleep(0.5)
        assert run_dir is not None, "agent never started"
        process.send_signal(signal.SIGINT)
        process.wait(timeout=120)
    finally:
        if process.poll() is None:
            process.kill()

    assert process.returncode == 4
    events = _events_text(run_dir)
    result = json.loads((run_dir / "result.json").read_text(encoding="utf-8"))
    assert result["status"] == "CANCELLED"
    assert result["primary_failure"]["code"] == "RUN_CANCELLED"
    # the agent's late call was rejected because the broker was already closed
    rejected = [json.loads(line) for line in events.splitlines() if "tool.call.rejected" in line]
    assert rejected, "late tool call was not rejected"
    assert "closed" in rejected[-1]["payload"]["message"]
    # and it never became a real side effect
    assert "tool.call.completed" not in events
    assert "environment.teardown.completed" in events
    assert residual_resources() == []


@pytest.mark.parametrize(
    "agent_source",
    [
        pytest.param(
            "import asyncio\n\n\nasync def run(task, tools, context):\n"
            "    raise asyncio.CancelledError\n",
            id="self-cancel",
        ),
        pytest.param(
            "import asyncio\n\n\nasync def run(task, tools, context):\n"
            "    sub = asyncio.create_task(asyncio.sleep(10))\n"
            "    sub.cancel()\n"
            "    await sub\n",
            id="awaits-cancelled-subtask",
        ),
    ],
)
def test_agent_self_cancellation_is_agent_failure(tmp_path: Path, agent_source: str) -> None:
    """P1-4: agent-side CancelledError must not abort the run without a result."""

    entrypoint = _write_agent(tmp_path, "agent_self_cancel", agent_source)

    def mutate(environment: dict, case: dict) -> None:
        case["agent"]["entrypoint"] = entrypoint

    output = tmp_path / "runs"
    result = run_case(_variant_case(tmp_path, mutate), output)

    assert result.verdict == Verdict.FAIL
    assert result.status == RunStatus.COMPLETED  # state machine completed; no crash
    assert result.primary_failure is not None
    assert result.primary_failure.attribution == FailureAttribution.AGENT_FAILURE
    assert result.primary_failure.code == FailureCode.AGENT_EXCEPTION
    assert "cancelled itself" in result.primary_failure.message
    # best-effort evidence still collected
    assert len(result.states) == 2
    assert (output / result.run_id / "result.json").is_file()
    assert residual_resources() == []


def test_sigint_during_teardown_runs_cleanup_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """P2-1/P2-2: one teardown task; cancellation waits on it, never duplicates it."""

    real_teardown = ComposeEnvironmentDriver.teardown
    calls = 0

    async def slow_failing_teardown(
        self: ComposeEnvironmentDriver, handle: EnvironmentHandle
    ) -> None:
        nonlocal calls
        calls += 1
        await interrupt_next_loop_turn()
        await real_teardown(self, handle)
        raise EnvironmentDriverError(
            "simulated teardown failure",
            code=FailureCode.ENV_TEARDOWN_FAILED,
            details={"compose_project": handle.project_name},
        )

    monkeypatch.setattr(ComposeEnvironmentDriver, "teardown", slow_failing_teardown)

    output = tmp_path / "runs"

    result = run_case(REFUND_CASE, output)

    assert calls == 1, f"teardown ran {calls} times (concurrent compose downs)"
    diagnostic = result.model_dump_json(indent=2)
    assert result.graders, diagnostic  # distinguish setup failures from cancellation failures
    assert result.status == RunStatus.CANCELLED, diagnostic
    assert result.primary_failure is not None
    assert result.primary_failure.code == FailureCode.RUN_CANCELLED, diagnostic
    teardown_failures = [
        failure
        for failure in result.secondary_failures
        if failure.code == FailureCode.ENV_TEARDOWN_FAILED
    ]
    assert len(teardown_failures) == 1
    assert residual_resources() == []


def test_lifecycle_failure_stays_primary_over_teardown_failure(tmp_path: Path, monkeypatch) -> None:
    """P2-2: the earliest conclusion-blocking error is primary, cleanup secondary."""

    real_teardown = ComposeEnvironmentDriver.teardown

    async def failing_teardown(self: ComposeEnvironmentDriver, handle: EnvironmentHandle) -> None:
        await real_teardown(self, handle)
        raise EnvironmentDriverError(
            "simulated teardown failure",
            code=FailureCode.ENV_TEARDOWN_FAILED,
            details={"compose_project": handle.project_name},
        )

    monkeypatch.setattr(ComposeEnvironmentDriver, "teardown", failing_teardown)

    def mutate(environment: dict, case: dict) -> None:
        environment["restore"]["command"] = ["python", "-c", "import sys; sys.exit(3)"]
        environment["restore"].pop("fixture_default", None)
        case["tools"]["provider"] = "ghost_provider:create_tools"  # would also fail, later

    output = tmp_path / "runs"
    result = run_case(_variant_case(tmp_path, mutate), output)

    assert result.verdict == Verdict.INVALID
    assert result.primary_failure is not None
    assert result.primary_failure.code == FailureCode.ENV_RESTORE_FAILED
    assert result.primary_failure.attribution == FailureAttribution.ENVIRONMENT_ERROR
    codes = [failure.code for failure in result.secondary_failures]
    assert codes == [FailureCode.ENV_TEARDOWN_FAILED]
    assert residual_resources() == []


def test_logs_collected_on_lifecycle_failure_and_cancel(tmp_path: Path) -> None:
    """P2-3: environment logs survive failed starts, not just happy paths."""

    def mutate(environment: dict, case: dict) -> None:
        environment["healthcheck"]["url"] = "http://127.0.0.1:${API_PORT}/no-such-route"
        environment["healthcheck"]["timeout_seconds"] = 3

    output = tmp_path / "runs"
    result = run_case(_variant_case(tmp_path, mutate), output)

    assert result.verdict == Verdict.INVALID
    assert result.primary_failure is not None
    assert result.primary_failure.code == FailureCode.ENV_HEALTHCHECK_FAILED
    log_artifacts = [a for a in result.artifacts if a.path.endswith("environment.log")]
    assert log_artifacts, "environment logs were not collected on the failure path"
    assert residual_resources() == []


def test_report_rerun_command_reproduces_execution(tmp_path: Path) -> None:
    """P2-4: the report's rerun command actually re-runs the same execution."""

    output = tmp_path / "runs"
    result = run_case(AGENT_CASE, output, agent_override="agents.wrong_refund_agent:run", seed=173)
    run_dir = output / result.run_id
    report = (run_dir / "report.html").read_text(encoding="utf-8")

    assert "examples/support-world/refund-agent.yaml" in report
    assert "--agent agents.wrong_refund_agent:run" in report
    assert "--seed 173" in report

    from reprocase.results.report import build_rerun_command

    manifest = json.loads((run_dir / "manifest.json").read_text(encoding="utf-8"))
    command = build_rerun_command(manifest, json.loads((run_dir / "result.json").read_text()))
    argv = [sys.executable, "-m", "reprocase.cli", *shlex.split(command)[1:]]
    rerun_output = tmp_path / "reruns"
    rerun = subprocess.run(
        [*argv, "-o", str(rerun_output)],
        capture_output=True,
        text=True,
        timeout=180,
        check=False,
    )
    assert rerun.returncode == 1, rerun.stdout[-500:]  # same wrong agent, same FAIL
    assert "FAIL support.refund.agent" in rerun.stdout
    assert residual_resources() == []


def test_sigint_during_log_collection_still_tears_down(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """P1-A: teardown must run even when log collection itself is cancelled."""

    real_collect = ComposeEnvironmentDriver.collect_logs
    real_teardown = ComposeEnvironmentDriver.teardown
    logs_entered = threading.Event()
    teardown_calls = 0

    async def slow_collect(self: ComposeEnvironmentDriver, handle: EnvironmentHandle) -> list[Path]:
        logs_entered.set()
        await asyncio.sleep(30)  # the SIGINT lands here
        return await real_collect(self, handle)

    async def counting_teardown(self: ComposeEnvironmentDriver, handle: EnvironmentHandle) -> None:
        nonlocal teardown_calls
        teardown_calls += 1
        await real_teardown(self, handle)

    monkeypatch.setattr(ComposeEnvironmentDriver, "collect_logs", slow_collect)
    monkeypatch.setattr(ComposeEnvironmentDriver, "teardown", counting_teardown)

    output = tmp_path / "runs"

    def interrupt_when_collecting() -> None:
        if logs_entered.wait(timeout=120):
            os.kill(os.getpid(), signal.SIGINT)

    watcher = threading.Thread(target=interrupt_when_collecting, daemon=True)
    watcher.start()
    result = run_case(REFUND_CASE, output)
    watcher.join(timeout=10)

    assert result.status == RunStatus.CANCELLED
    assert result.primary_failure is not None
    assert result.primary_failure.code == FailureCode.RUN_CANCELLED
    assert teardown_calls == 1, f"teardown ran {teardown_calls} times"
    assert result.cleanup_required is False
    assert any("log collection was interrupted" in warning for warning in result.warnings)
    assert residual_resources() == []


_SLOW_PROVIDER = """\
import asyncio
import asyncio
import json
import time
import urllib.request

DELAY = {delay}


class SlowEffectTool:
    name = "slow.effect"
    description = "side effect lands slowly in a thread"
    input_schema = {{"type": "object"}}

    def _effect(self):
        time.sleep(DELAY)
        request = urllib.request.Request(
            f"{{BASE}}/refunds",
            data=json.dumps({{"order_id": "ORD-1001", "amount": 49.9}}).encode("utf-8"),
            headers={{"Content-Type": "application/json"}},
            method="POST",
        )
        with urllib.request.urlopen(request, timeout=10) as response:
            return json.loads(response.read().decode("utf-8"))

    async def invoke(self, arguments):
        return await asyncio.to_thread(self._effect)


def create_tools(environment):
    tool = SlowEffectTool()
    tool._effect.__globals__["BASE"] = environment.service_urls["api"]
    tool.base_url = environment.service_urls["api"]

    def _effect(base_url=tool.base_url):
        time.sleep(DELAY)
        request = urllib.request.Request(
            f"{{base_url}}/refunds",
            data=json.dumps({{"order_id": "ORD-1001", "amount": 49.9}}).encode("utf-8"),
            headers={{"Content-Type": "application/json"}},
            method="POST",
        )
        with urllib.request.urlopen(request, timeout=10) as response:
            return json.loads(response.read().decode("utf-8"))

    tool._effect = _effect
    return [tool]
"""


def _slow_effect_case(tmp_path: Path, delay: int) -> Path:
    provider = _SLOW_PROVIDER.format(delay=delay, BASE="")
    (tmp_path / "slow_provider.py").write_text(provider, encoding="utf-8")
    entrypoint = _write_agent(
        tmp_path,
        "agent_slow_effect",
        """\
async def run(task, tools, context):
    await tools.call("slow.effect", {})
    return {"message": "done"}
""",
    )

    def mutate(environment: dict, case: dict) -> None:
        case["tools"]["provider"] = "slow_provider:create_tools"
        case["tools"]["allow"] = ["slow.effect"]
        case["agent"]["entrypoint"] = entrypoint
        case["budgets"]["wall_time_seconds"] = 1

    return _variant_case(tmp_path, mutate)


def test_inflight_tool_effect_lands_before_final_capture(tmp_path: Path) -> None:
    """P1-B: final state is captured only after in-flight operations finish."""

    output = tmp_path / "runs"
    result = run_case(_slow_effect_case(tmp_path, delay=2), output)

    assert result.primary_failure is None or (
        result.primary_failure.code != FailureCode.RUNNER_EVIDENCE_INCOMPLETE
    )
    assert not any("did not confirm completion" in w for w in result.warnings)
    # wall time (1s) cut the agent mid-invocation, yet the threaded side
    # effect (2s) is visible because capture waited for the drain
    diff = json.loads((output / result.run_id / "state.diff.json").read_text(encoding="utf-8"))
    paths = {op["path"] for op in diff["ops"]}
    assert "/refunds/RF-9001" in paths
    assert residual_resources() == []


def test_unconfirmed_inflight_effects_mark_evidence_incomplete(tmp_path: Path) -> None:
    """P1-B: when the drain expires, the run must not trust a changing state."""

    output = tmp_path / "runs"
    result = run_case(_slow_effect_case(tmp_path, delay=12), output)

    assert result.verdict == Verdict.INVALID
    assert result.primary_failure is not None
    assert result.primary_failure.code == FailureCode.AGENT_TIMEOUT
    secondary_codes = [failure.code for failure in result.secondary_failures]
    assert FailureCode.RUNNER_EVIDENCE_INCOMPLETE in secondary_codes
    assert any("did not confirm completion" in warning for warning in result.warnings)
    assert residual_resources() == []


def test_agent_failure_keeps_primary_over_later_cancel(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """P2-A: an earlier agent failure stays primary; the cancel lands in order."""

    real_teardown = ComposeEnvironmentDriver.teardown

    async def slow_failing_teardown(
        self: ComposeEnvironmentDriver, handle: EnvironmentHandle
    ) -> None:
        await interrupt_next_loop_turn()
        await real_teardown(self, handle)
        raise EnvironmentDriverError(
            "simulated teardown failure",
            code=FailureCode.ENV_TEARDOWN_FAILED,
            details={"compose_project": handle.project_name},
        )

    monkeypatch.setattr(ComposeEnvironmentDriver, "teardown", slow_failing_teardown)
    entrypoint = _write_agent(
        tmp_path,
        "agent_raises_value_error",
        "async def run(task, tools, context):\n    raise ValueError('agent bug')\n",
    )

    def mutate(environment: dict, case: dict) -> None:
        case["agent"]["entrypoint"] = entrypoint

    output = tmp_path / "runs"

    result = run_case(_variant_case(tmp_path, mutate), output)

    diagnostic = result.model_dump_json(indent=2)
    assert result.graders, diagnostic
    assert result.status == RunStatus.CANCELLED, diagnostic
    assert result.primary_failure is not None
    assert result.primary_failure.code == FailureCode.AGENT_EXCEPTION, diagnostic
    assert result.primary_failure.attribution == FailureAttribution.AGENT_FAILURE
    codes = [failure.code for failure in result.secondary_failures]
    assert codes[-2:] == [FailureCode.RUN_CANCELLED, FailureCode.ENV_TEARDOWN_FAILED]
    assert residual_resources() == []


def test_sigint_during_execution_stays_primary_over_teardown_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """P2-B: a cancel observed mid-execution is recorded before cleanup, so a
    failing teardown lands secondary instead of preempting RUN_CANCELLED."""

    real_teardown = ComposeEnvironmentDriver.teardown

    async def failing_teardown(self: ComposeEnvironmentDriver, handle: EnvironmentHandle) -> None:
        await real_teardown(self, handle)  # actually clean up; simulate the failure after
        raise EnvironmentDriverError(
            "simulated teardown failure",
            code=FailureCode.ENV_TEARDOWN_FAILED,
            details={"compose_project": handle.project_name},
        )

    monkeypatch.setattr(ComposeEnvironmentDriver, "teardown", failing_teardown)
    entrypoint = _write_agent(
        tmp_path,
        "agent_sleeps_until_cancelled",
        """\
import asyncio


async def run(task, tools, context):
    await asyncio.sleep(300)
    return {"message": "unreachable"}
""",
    )

    def mutate(environment: dict, case: dict) -> None:
        case["agent"]["entrypoint"] = entrypoint
        case["budgets"]["wall_time_seconds"] = 300

    output = tmp_path / "runs"

    def interrupt_when_agent_started() -> None:
        deadline = time.monotonic() + 120
        while time.monotonic() < deadline:
            candidates = (
                [path for path in output.iterdir() if path.is_dir()] if output.exists() else []
            )
            if candidates and '"agent.started"' in _events_text(candidates[0]):
                time.sleep(1.0)  # land squarely inside the agent's sleep
                os.kill(os.getpid(), signal.SIGINT)
                return
            time.sleep(0.2)

    watcher = threading.Thread(target=interrupt_when_agent_started, daemon=True)
    watcher.start()
    result = run_case(_variant_case(tmp_path, mutate), output)
    watcher.join(timeout=10)

    assert result.status == RunStatus.CANCELLED
    assert result.primary_failure is not None
    assert result.primary_failure.code == FailureCode.RUN_CANCELLED
    codes = [failure.code for failure in result.secondary_failures]
    assert codes == [FailureCode.ENV_TEARDOWN_FAILED]
    assert result.cleanup_required is True
    assert result.cleanup_command is not None
    assert residual_resources() == []


def test_sigint_during_log_collection_stays_primary_over_teardown_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """P2-B: a cancel observed in log collection is recorded before teardown,
    so a failing teardown lands secondary instead of preempting RUN_CANCELLED."""

    real_collect = ComposeEnvironmentDriver.collect_logs
    real_teardown = ComposeEnvironmentDriver.teardown
    logs_entered = threading.Event()

    async def slow_collect(self: ComposeEnvironmentDriver, handle: EnvironmentHandle) -> list[Path]:
        logs_entered.set()
        await asyncio.sleep(30)  # the SIGINT lands here
        return await real_collect(self, handle)

    async def failing_teardown(self: ComposeEnvironmentDriver, handle: EnvironmentHandle) -> None:
        await real_teardown(self, handle)  # actually clean up; simulate the failure after
        raise EnvironmentDriverError(
            "simulated teardown failure",
            code=FailureCode.ENV_TEARDOWN_FAILED,
            details={"compose_project": handle.project_name},
        )

    monkeypatch.setattr(ComposeEnvironmentDriver, "collect_logs", slow_collect)
    monkeypatch.setattr(ComposeEnvironmentDriver, "teardown", failing_teardown)

    output = tmp_path / "runs"

    def interrupt_when_collecting() -> None:
        if logs_entered.wait(timeout=120):
            os.kill(os.getpid(), signal.SIGINT)

    watcher = threading.Thread(target=interrupt_when_collecting, daemon=True)
    watcher.start()
    result = run_case(REFUND_CASE, output)
    watcher.join(timeout=10)

    assert result.status == RunStatus.CANCELLED
    assert result.primary_failure is not None
    assert result.primary_failure.code == FailureCode.RUN_CANCELLED
    codes = [failure.code for failure in result.secondary_failures]
    assert codes == [FailureCode.ENV_TEARDOWN_FAILED]
    assert result.cleanup_required is True
    assert any("log collection was interrupted" in warning for warning in result.warnings)
    assert residual_resources() == []
