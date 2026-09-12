"""Local runner: M0 fake flow plus the M2 deterministic Compose evaluation loop."""

from __future__ import annotations

import asyncio
import hashlib
import json
import platform
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from reprocase.agents.protocol import AgentContext
from reprocase.agents.python_adapter import PythonAgentAdapter
from reprocase.case.loader import LoadedCase, load_case
from reprocase.case.models import (
    ComposeEnvironmentDefinition,
    FakeEnvironmentDefinition,
    FakeGraderSpec,
)
from reprocase.entrypoints import ImportIsolation, code_identity, load_callable
from reprocase.environments.compose import ComposeEnvironmentDriver, resolve_compose_file
from reprocase.environments.protocol import CommandResult, EnvironmentHandle
from reprocase.errors import (
    CaseValidationError,
    EnvironmentDriverError,
    FailureAttribution,
    FailureCode,
    ReprocaseError,
)
from reprocase.graders.engine import grader_failure_attribution, run_graders
from reprocase.graders.protocol import GraderContext
from reprocase.ids import new_run_id
from reprocase.recording.artifacts import ArtifactStore
from reprocase.recording.diff import diff_document
from reprocase.recording.events import EventRecorder
from reprocase.recording.state import StateRecorder
from reprocase.results.models import (
    AgentIdentity,
    ArtifactRef,
    CaseIdentity,
    FailureRecord,
    GraderResult,
    GraderStatus,
    RunManifest,
    RunResult,
    RunState,
    RunStatus,
    SourceIdentity,
    StateSnapshot,
    Usage,
    Verdict,
)
from reprocase.results.storage import RunStorage
from reprocase.runner.fakes import FakeAgent, FakeEnvironment, FakeGrader
from reprocase.runner.scripted import ActorContext, load_actor
from reprocase.runner.state_machine import RunStateMachine
from reprocase.tools.broker import ToolBroker
from reprocase.tools.redaction import redact
from reprocase.tools.registry import ToolRegistry
from reprocase.tools.validation import validate_tools
from reprocase.vcs import git_identity


class _RunDraft:
    def __init__(self) -> None:
        self.failures: list[FailureRecord] = []
        self.graders: list[GraderResult] = []
        self.agent_output: Any = None
        self.agent_error: str | None = None
        self.usage: Usage | None = None
        self.states: list[StateSnapshot] = []
        self.state_diff_ref: ArtifactRef | None = None
        self.artifacts: list[ArtifactRef] = []


def _failure_record(error: ReprocaseError) -> FailureRecord:
    return FailureRecord(
        attribution=error.attribution,
        code=error.code,
        message=error.message,
        details=error.details,
    )


def _case_identity(loaded: LoadedCase) -> CaseIdentity:
    return CaseIdentity(
        id=loaded.case.metadata.id,
        version=loaded.case.metadata.version,
        digest=loaded.digest,
        path=loaded.path.name,
    )


def _verdict(draft: _RunDraft) -> Verdict:
    invalid_attributions = {
        FailureAttribution.CASE_ERROR,
        FailureAttribution.ENVIRONMENT_ERROR,
        FailureAttribution.GRADER_ERROR,
        FailureAttribution.RUNNER_ERROR,
    }
    if any(failure.attribution in invalid_attributions for failure in draft.failures):
        return Verdict.INVALID
    if any(grader.status == GraderStatus.ERROR for grader in draft.graders):
        return Verdict.INVALID
    if any(failure.attribution == FailureAttribution.AGENT_FAILURE for failure in draft.failures):
        return Verdict.FAIL
    if any(grader.status == GraderStatus.FAIL for grader in draft.graders):
        return Verdict.FAIL
    return Verdict.PASS


class _RunSession:
    def __init__(self, recorder: EventRecorder) -> None:
        self.machine = RunStateMachine()
        self.recorder = recorder

    def transition(self, target: RunState) -> None:
        previous = self.machine.current
        self.machine.transition(target)
        self.recorder.emit(
            "run.state.changed",
            source="orchestrator",
            payload={"from": previous, "to": target},
        )


def _record_grader_failures(draft: _RunDraft) -> None:
    for graded in draft.graders:
        if graded.status == GraderStatus.ERROR:
            code = (
                FailureCode.GRADER_FAKE_EXECUTION_FAILED
                if graded.grader_type == "fake"
                else FailureCode.GRADER_EXECUTION_ERROR
            )
            draft.failures.append(
                FailureRecord(
                    attribution=FailureAttribution.GRADER_ERROR,
                    code=code,
                    message=graded.explanation,
                    details=grader_failure_attribution(graded),
                )
            )
        elif graded.status == GraderStatus.FAIL:
            code = (
                FailureCode.TEST_COMMAND_FAILED
                if graded.grader_type == "test_command"
                else FailureCode.GRADER_ASSERTION_FAILED
            )
            draft.failures.append(
                FailureRecord(
                    attribution=FailureAttribution.AGENT_FAILURE,
                    code=code,
                    message=graded.explanation,
                    details=grader_failure_attribution(graded),
                )
            )


def _execute_fake(
    session: _RunSession,
    loaded: LoadedCase,
    draft: _RunDraft,
) -> None:
    recorder = session.recorder
    environment: FakeEnvironment | None = None
    try:
        session.transition(RunState.PREPARING_ENVIRONMENT)
        assert isinstance(loaded.environment, FakeEnvironmentDefinition)
        environment = FakeEnvironment(loaded.environment.config)
        environment.prepare()

        session.transition(RunState.STARTING_ENVIRONMENT)
        environment.start()
        recorder.emit("environment.started", source="fake_environment")

        session.transition(RunState.RESTORING_ENVIRONMENT)
        environment.restore()
        recorder.emit("environment.restored", source="fake_environment")

        session.transition(RunState.HEALTHCHECKING)
        environment.healthcheck()
        recorder.emit("environment.healthy", source="fake_environment")

        session.transition(RunState.CAPTURING_INITIAL_STATE)
        initial_state = environment.capture_state()
        recorder.emit(
            "environment.state.captured",
            source="fake_environment",
            payload={"phase": "initial", "state": initial_state},
        )

        session.transition(RunState.LOADING_AGENT_AND_TOOLS)
        agent = FakeAgent(loaded.case.agent.config)

        session.transition(RunState.RUNNING_AGENT)
        recorder.emit("agent.started", source="fake_agent")
        try:
            agent_result = agent.run(loaded.case.task.input)
            draft.agent_output = agent_result.output
            recorder.emit(
                "agent.completed",
                source="fake_agent",
                payload={"output": redact(draft.agent_output)},
            )
        except ReprocaseError as error:
            draft.failures.append(_failure_record(error))
            draft.agent_error = error.message
            recorder.emit(
                "agent.failed",
                source="fake_agent",
                payload={"code": error.code, "message": error.message},
            )

        session.transition(RunState.CAPTURING_FINAL_STATE)
        final_state = environment.capture_state()
        recorder.emit(
            "environment.state.captured",
            source="fake_environment",
            payload={"phase": "final", "state": final_state},
        )

        session.transition(RunState.GRADING)
        for spec in loaded.case.graders:
            if not isinstance(spec, FakeGraderSpec):
                continue  # the fake flow only exercises fake graders (M0 fixtures)
            recorder.emit(
                "grader.started",
                source="fake_grader",
                payload={"grader_id": spec.id},
            )
            grader_result = FakeGrader(spec).grade()
            draft.graders.append(grader_result)
            recorder.emit(
                "grader.completed",
                source="fake_grader",
                payload=grader_result.model_dump(mode="json"),
            )
        _record_grader_failures(draft)
    finally:
        if environment is not None:
            environment.teardown()
            recorder.emit("environment.teardown.completed", source="fake_environment")


async def _run_scripted_agent(
    session: _RunSession,
    loaded: LoadedCase,
    draft: _RunDraft,
    run_id: str,
    entrypoint: str,
    handle: EnvironmentHandle,
    seed: int,
) -> None:
    recorder = session.recorder
    import_root = loaded.environment_path.parent
    with ImportIsolation(import_root):
        actor = load_actor(entrypoint, import_root)

        session.transition(RunState.RUNNING_AGENT)
        recorder.emit("agent.started", source="scripted_actor", payload={"entrypoint": entrypoint})
        base_url = (
            handle.service_urls[sorted(handle.service_urls)[0]] if handle.service_urls else None
        )
        context = ActorContext(
            run_id=run_id,
            task=loaded.case.task.input,
            base_url=base_url,
            service_urls=handle.service_urls,
            ports=handle.ports,
            seed=seed,
        )
        try:
            draft.agent_output = await asyncio.to_thread(actor, context)
            recorder.emit(
                "agent.completed",
                source="scripted_actor",
                payload={"output": redact(draft.agent_output)},
            )
        except Exception as exc:  # actor failures are agent failures, never infra failures
            message = f"Scripted actor raised {type(exc).__name__}: {exc}"
            draft.agent_error = message
            draft.failures.append(
                FailureRecord(
                    attribution=FailureAttribution.AGENT_FAILURE,
                    code=FailureCode.AGENT_EXCEPTION,
                    message=message,
                    details={"entrypoint": entrypoint},
                )
            )
            recorder.emit(
                "agent.failed",
                source="scripted_actor",
                payload={"code": FailureCode.AGENT_EXCEPTION, "message": message},
            )


async def _run_python_agent(
    session: _RunSession,
    loaded: LoadedCase,
    draft: _RunDraft,
    run_id: str,
    entrypoint: str,
    handle: EnvironmentHandle,
    seed: int,
) -> None:
    recorder = session.recorder
    import_root = loaded.environment_path.parent
    tools_spec = loaded.case.tools
    assert tools_spec is not None  # enforced by EvalCase validation

    # Import isolation spans provider loading, provider execution, adapter
    # loading, and the agent run itself: case code may import lazily at any
    # point inside that span.
    with ImportIsolation(import_root):
        provider = load_callable(tools_spec.provider, import_root)
        supplied = list(provider(handle))
        validate_tools(supplied, tools_spec.allow)  # provider defects -> CASE_ERROR/INVALID
        registry = ToolRegistry(supplied)
        broker = ToolBroker(
            registry,
            allow=tools_spec.allow,
            max_calls=loaded.case.budgets.max_tool_calls,
            tool_timeout_seconds=float(loaded.case.budgets.tool_timeout_seconds),
            recorder=recorder,
        )
        adapter = PythonAgentAdapter(entrypoint, import_root)

        async def emit_agent(payload: dict[str, Any]) -> None:
            event_type = "agent.model.usage" if "usage" in payload else "agent.message"
            recorder.emit(event_type, source="python_agent", payload=redact(payload))

        context = AgentContext(
            run_id=run_id,
            seed=seed,
            fixed_clock=None,
            tool_names=broker.tool_names,
            emit=emit_agent,
        )
        wall_time = loaded.case.budgets.wall_time_seconds
        session.transition(RunState.RUNNING_AGENT)
        recorder.emit(
            "agent.started",
            source="python_agent",
            payload={
                "entrypoint": entrypoint,
                "tools": list(broker.tool_names),
                "budget": {
                    "wall_time_seconds": wall_time,
                    "max_tool_calls": loaded.case.budgets.max_tool_calls,
                    "tool_timeout_seconds": loaded.case.budgets.tool_timeout_seconds,
                },
            },
        )
        model_usage: Usage | None = None
        try:
            async with asyncio.timeout(wall_time) as timer:
                agent_result = await adapter.run(loaded.case.task.input, broker, context)
            draft.agent_output = agent_result.output
            model_usage = agent_result.usage
            recorder.emit(
                "agent.completed",
                source="python_agent",
                payload={"output": redact(draft.agent_output), "summary": agent_result.summary},
            )
        except TimeoutError as exc:
            if timer.expired():
                message = f"Agent exceeded its wall time of {wall_time}s"
                code = FailureCode.AGENT_TIMEOUT
                event_type = "agent.timed_out"
                payload: dict[str, Any] = {
                    "wall_time_seconds": wall_time,
                    "tool_calls": broker.calls_made,
                }
            else:
                # the agent (or its SDK) raised TimeoutError itself: an ordinary
                # agent exception, not a wall-time expiry
                message = f"Agent raised TimeoutError: {exc}"
                code = FailureCode.AGENT_EXCEPTION
                event_type = "agent.failed"
                payload = {"code": FailureCode.AGENT_EXCEPTION, "message": message}
            draft.agent_error = message
            draft.failures.append(
                FailureRecord(
                    attribution=FailureAttribution.AGENT_FAILURE,
                    code=code,
                    message=message,
                    details={"entrypoint": entrypoint},
                )
            )
            recorder.emit(event_type, source="python_agent", payload=payload)
        except ReprocaseError as exc:
            if exc.attribution != FailureAttribution.AGENT_FAILURE:
                raise  # case/environment defects must not be re-labeled as agent failures
            # Structured failures escaping the agent (tool errors, invalid output)
            draft.agent_error = exc.message
            draft.failures.append(
                FailureRecord(
                    attribution=FailureAttribution.AGENT_FAILURE,
                    code=exc.code,
                    message=exc.message,
                    details=exc.details,
                )
            )
            recorder.emit(
                "agent.failed",
                source="python_agent",
                payload={"code": exc.code, "message": exc.message},
            )
        except Exception as exc:  # untrusted-shape but trusted-code agent bugs
            message = f"Agent raised {type(exc).__name__}: {exc}"
            draft.agent_error = message
            draft.failures.append(
                FailureRecord(
                    attribution=FailureAttribution.AGENT_FAILURE,
                    code=FailureCode.AGENT_EXCEPTION,
                    message=message,
                    details={"entrypoint": entrypoint},
                )
            )
            recorder.emit(
                "agent.failed",
                source="python_agent",
                payload={"code": FailureCode.AGENT_EXCEPTION, "message": message},
            )

        if broker.budget_exceeded and not any(
            failure.code == FailureCode.AGENT_BUDGET_EXCEEDED for failure in draft.failures
        ):
            message = f"Tool call budget exhausted after {broker.calls_made} calls"
            draft.agent_error = draft.agent_error or message
            draft.failures.append(
                FailureRecord(
                    attribution=FailureAttribution.AGENT_FAILURE,
                    code=FailureCode.AGENT_BUDGET_EXCEEDED,
                    message=message,
                    details={"tool_calls": broker.calls_made},
                )
            )

        draft.usage = Usage(
            tool_calls=broker.calls_made,
            model_calls=model_usage.model_calls if model_usage else None,
            input_tokens=model_usage.input_tokens if model_usage else None,
            output_tokens=model_usage.output_tokens if model_usage else None,
            cost_usd=model_usage.cost_usd if model_usage else None,
        )


async def _execute_compose(
    session: _RunSession,
    loaded: LoadedCase,
    definition: ComposeEnvironmentDefinition,
    compose_file: Path,
    storage: RunStorage,
    draft: _RunDraft,
    run_id: str,
    entrypoint: str,
    seed: int,
) -> None:
    recorder = session.recorder
    artifacts = ArtifactStore(storage.run_dir)
    driver = ComposeEnvironmentDriver(
        definition,
        compose_file=compose_file,
        slug=loaded.case.metadata.id,
        run_suffix=run_id[-10:],
        work_root=storage.run_dir / "environment",
    )
    handle: EnvironmentHandle | None = None
    prepared = False
    try:
        session.transition(RunState.PREPARING_ENVIRONMENT)
        await driver.prepare()
        prepared = True
        recorder.emit(
            "environment.prepared",
            source="compose_environment",
            payload={"compose_project": driver.project_name, "ports": driver.ports},
        )

        session.transition(RunState.STARTING_ENVIRONMENT)
        handle = await driver.start()
        recorder.emit(
            "environment.started",
            source="compose_environment",
            payload={"compose_project": handle.project_name, "service_urls": handle.service_urls},
        )

        session.transition(RunState.RESTORING_ENVIRONMENT)
        await driver.restore(handle)
        recorder.emit("environment.restored", source="compose_environment")

        session.transition(RunState.HEALTHCHECKING)
        health = await driver.healthcheck(handle)
        recorder.emit(
            "environment.healthy",
            source="compose_environment",
            payload={"detail": health.detail, "attempts": health.attempts},
        )

        session.transition(RunState.CAPTURING_INITIAL_STATE)
        states = StateRecorder(storage.run_dir, driver, handle)
        initial_snapshot, initial_document = await states.capture("initial")
        draft.states.append(initial_snapshot)
        recorder.emit(
            "environment.state.captured",
            source="compose_environment",
            payload={"phase": "initial", "content_digest": initial_snapshot.content_digest},
        )

        session.transition(RunState.LOADING_AGENT_AND_TOOLS)
        if loaded.case.agent.adapter == "scripted":
            await _run_scripted_agent(session, loaded, draft, run_id, entrypoint, handle, seed)
        else:
            await _run_python_agent(session, loaded, draft, run_id, entrypoint, handle, seed)

        session.transition(RunState.CAPTURING_FINAL_STATE)
        final_snapshot, final_document = await states.capture("final")
        draft.states.append(final_snapshot)
        recorder.emit(
            "environment.state.captured",
            source="compose_environment",
            payload={"phase": "final", "content_digest": final_snapshot.content_digest},
        )

        diff = diff_document(initial_document, final_document)
        diff_payload = json.dumps(diff, ensure_ascii=False, indent=2, sort_keys=True).encode(
            "utf-8"
        )
        (storage.run_dir / "state.diff.json").write_bytes(diff_payload)
        draft.state_diff_ref = ArtifactRef(
            media_type="application/json",
            path="state.diff.json",
            size_bytes=len(diff_payload),
            sha256=hashlib.sha256(diff_payload).hexdigest(),
        )

        session.transition(RunState.GRADING)
        grader_context = GraderContext(
            run_id=run_id,
            initial_snapshot=initial_snapshot,
            final_snapshot=final_snapshot,
            initial_document=initial_document,
            final_document=final_document,
            diff_ref=draft.state_diff_ref,
            agent_error=draft.agent_error,
        )

        async def exec_fn(service: str, argv: list[str], timeout: float) -> CommandResult:
            return await driver.exec(
                handle, service, argv, timeout, timeout_code=FailureCode.GRADER_TIMEOUT
            )

        draft.graders.extend(
            await run_graders(
                loaded.case.graders,
                grader_context,
                exec_fn=exec_fn,
                artifacts=artifacts,
                recorder=recorder,
            )
        )
        _record_grader_failures(draft)
        draft.artifacts.extend(artifacts.created)

        log_paths = await driver.collect_logs(handle)
        for log_path in log_paths:
            content = log_path.read_bytes()
            relative = log_path.relative_to(storage.run_dir).as_posix()
            draft.artifacts.append(
                ArtifactRef(
                    media_type="text/plain",
                    path=relative,
                    size_bytes=len(content),
                    sha256=hashlib.sha256(content).hexdigest(),
                )
            )
        recorder.emit(
            "environment.logs.collected",
            source="compose_environment",
            payload={"count": len(log_paths)},
        )
    finally:
        # A failed `up` can still leave containers behind: always try teardown
        # once prepare succeeded, and never let teardown mask the primary failure.
        teardown_handle = handle
        if teardown_handle is None and prepared:
            teardown_handle = EnvironmentHandle(
                driver="docker_compose",
                project_name=driver.project_name,
                compose_file=driver.compose_file,
                env_file=driver.env_file,
                work_dir=driver.work_dir,
            )
        if teardown_handle is not None:
            try:
                await driver.teardown(teardown_handle)
                recorder.emit("environment.teardown.completed", source="compose_environment")
            except EnvironmentDriverError as exc:
                draft.failures.append(_failure_record(exc))


def _validate_run_request(loaded: LoadedCase, agent_override: str | None) -> str | None:
    """Return the effective agent entrypoint, or None for the fake flow."""

    adapter = loaded.case.agent.adapter
    if isinstance(loaded.environment, FakeEnvironmentDefinition):
        if adapter != "fake" or agent_override is not None:
            raise CaseValidationError(
                "the fake environment supports only the fake agent adapter",
                code=FailureCode.CASE_UNSUPPORTED_DRIVER,
                details={"adapter": adapter},
            )
        return None
    if adapter not in {"scripted", "python"}:
        raise CaseValidationError(
            "docker_compose runs require the scripted or python agent adapter",
            code=FailureCode.CASE_UNSUPPORTED_DRIVER,
            details={"adapter": adapter},
        )
    entrypoint = agent_override or loaded.case.agent.entrypoint
    if entrypoint is None:
        raise CaseValidationError(
            f"{adapter} runs require agent.entrypoint or an --agent override",
            code=FailureCode.CASE_ENTRYPOINT_INVALID,
        )
    return entrypoint


def run_case(
    case_path: Path | str,
    output_root: Path | str = ".reprocase/runs",
    *,
    agent_override: str | None = None,
    seed: int = 0,
) -> RunResult:
    """Validate and execute one EvalCase (fake or docker_compose driver)."""

    loaded = load_case(case_path)
    entrypoint = _validate_run_request(loaded, agent_override)
    is_fake = entrypoint is None

    run_id = new_run_id()
    storage = RunStorage(Path(output_root), run_id)
    identity = _case_identity(loaded)
    git_commit, git_dirty = git_identity(loaded.root)
    source = SourceIdentity(
        python=sys.version.split()[0],
        platform=f"{platform.system()}-{platform.machine()}",
        git_commit=git_commit,
        git_dirty=git_dirty,
    )
    budget = loaded.case.budgets.model_dump(mode="json")
    import_root = loaded.environment_path.parent
    tools_spec = loaded.case.tools
    agent_identity = AgentIdentity(
        adapter=loaded.case.agent.adapter,
        entrypoint=entrypoint,
        code=code_identity(entrypoint, import_root) if entrypoint is not None else None,
        provider=tools_spec.provider if tools_spec is not None else None,
        provider_code=(
            code_identity(tools_spec.provider, import_root) if tools_spec is not None else None
        ),
    )
    if is_fake:
        manifest = RunManifest(
            run_id=run_id,
            case=identity,
            source=source,
            budget=budget,
            seed=seed,
            agent=agent_identity,
            reproducibility_level="R0",
            components=("fake_environment", "fake_agent", "fake_grader"),
        )
    elif loaded.case.agent.adapter == "scripted":
        manifest = RunManifest(
            run_id=run_id,
            case=identity,
            source=source,
            budget=budget,
            seed=seed,
            agent=agent_identity,
            reproducibility_level="R1",
            components=("compose_environment", "scripted_actor", "deterministic_graders"),
        )
    else:
        manifest = RunManifest(
            run_id=run_id,
            case=identity,
            source=source,
            budget=budget,
            seed=seed,
            agent=agent_identity,
            reproducibility_level="R1",
            components=(
                "compose_environment",
                "python_agent",
                "tool_broker",
                "deterministic_graders",
            ),
        )
    storage.write_manifest(manifest)
    recorder = EventRecorder(storage.events_path, run_id)
    session = _RunSession(recorder)
    started_at = datetime.now(UTC)
    monotonic_started = time.monotonic_ns()
    draft = _RunDraft()
    terminal_state = RunState.COMPLETED

    recorder.emit(
        "run.created",
        source="orchestrator",
        payload={"case_id": identity.id, "case_digest": identity.digest},
    )

    try:
        session.transition(RunState.VALIDATING)
        recorder.emit("case.validated", source="case_loader")
        if is_fake:
            _execute_fake(session, loaded, draft)
        else:
            assert isinstance(loaded.environment, ComposeEnvironmentDefinition)
            compose_file = loaded.compose_path or resolve_compose_file(
                loaded.environment, loaded.environment_path.parent
            )
            assert entrypoint is not None
            asyncio.run(
                _execute_compose(
                    session,
                    loaded,
                    loaded.environment,
                    compose_file,
                    storage,
                    draft,
                    run_id,
                    entrypoint,
                    seed,
                )
            )
    except KeyboardInterrupt:
        terminal_state = RunState.CANCELLED
        draft.failures.append(
            FailureRecord(
                attribution=FailureAttribution.RUNNER_ERROR,
                code=FailureCode.RUN_CANCELLED,
                message="Run cancelled by user",
            )
        )
        session.transition(RunState.CANCELLED)
        recorder.emit("run.cancelled", source="orchestrator")
    except ReprocaseError as error:
        terminal_state = RunState.COMPLETED_WITH_ERROR
        draft.failures.append(_failure_record(error))
        session.transition(RunState.FAILED)
        recorder.emit(
            "run.failed",
            source="orchestrator",
            payload={"code": error.code, "message": error.message},
        )
    except Exception as error:  # defensive boundary: preserve evidence for unknown bugs
        terminal_state = RunState.COMPLETED_WITH_ERROR
        failure = FailureRecord(
            attribution=FailureAttribution.RUNNER_ERROR,
            code=FailureCode.RUNNER_INTERNAL_ERROR,
            message=f"Unexpected runner error: {type(error).__name__}: {error}",
        )
        draft.failures.append(failure)
        session.transition(RunState.FAILED)
        recorder.emit(
            "run.failed",
            source="orchestrator",
            payload={"code": failure.code, "message": failure.message},
        )
    finally:
        if session.machine.current not in {
            RunState.COLLECTING_ARTIFACTS,
            RunState.TEARING_DOWN,
            RunState.WRITING_RESULT,
            RunState.COMPLETED,
            RunState.COMPLETED_WITH_ERROR,
        }:
            session.transition(RunState.COLLECTING_ARTIFACTS)
        recorder.emit(
            "artifacts.collected",
            source="orchestrator",
            payload={"count": len(draft.artifacts)},
        )
        if session.machine.current == RunState.COLLECTING_ARTIFACTS:
            session.transition(RunState.TEARING_DOWN)

    if session.machine.current == RunState.TEARING_DOWN:
        session.transition(RunState.WRITING_RESULT)

    verdict = _verdict(draft)
    if terminal_state == RunState.CANCELLED:
        status = RunStatus.CANCELLED
        verdict = Verdict.INVALID
    elif verdict == Verdict.INVALID or terminal_state == RunState.COMPLETED_WITH_ERROR:
        status = RunStatus.COMPLETED_WITH_ERROR
        terminal_state = RunState.COMPLETED_WITH_ERROR
        if verdict == Verdict.PASS:
            verdict = Verdict.INVALID  # an aborted run must never report PASS
    else:
        status = RunStatus.COMPLETED
        terminal_state = RunState.COMPLETED

    finished_at = datetime.now(UTC)
    warnings = ("M0 uses fake components and provides R0 evidence only.",) if is_fake else ()
    result = RunResult(
        run_id=run_id,
        status=status,
        verdict=verdict,
        final_state=terminal_state,
        case=identity,
        started_at=started_at,
        finished_at=finished_at,
        duration_ms=max(0, (time.monotonic_ns() - monotonic_started) // 1_000_000),
        reproducibility_level="R0" if is_fake else "R1",
        agent_output=draft.agent_output,
        usage=draft.usage,
        states=tuple(draft.states),
        state_diff=draft.state_diff_ref,
        graders=tuple(draft.graders),
        artifacts=tuple(draft.artifacts),
        primary_failure=draft.failures[0] if draft.failures else None,
        secondary_failures=tuple(draft.failures[1:]),
        warnings=warnings,
    )
    storage.write_result(result)
    session.transition(terminal_state)
    recorder.emit(
        "run.completed",
        source="orchestrator",
        payload={"status": status, "verdict": verdict},
    )
    return result
