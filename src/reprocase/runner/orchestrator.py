"""M0 local runner using deterministic fake components."""

from __future__ import annotations

import platform
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from reprocase.case.loader import LoadedCase, load_case
from reprocase.case.models import FakeEnvironmentDefinition
from reprocase.errors import (
    CaseValidationError,
    FailureAttribution,
    FailureCode,
    ReprocaseError,
)
from reprocase.ids import new_run_id
from reprocase.recording.events import EventRecorder
from reprocase.results.models import (
    CaseIdentity,
    FailureRecord,
    GraderResult,
    GraderStatus,
    RunManifest,
    RunResult,
    RunState,
    RunStatus,
    SourceIdentity,
    Verdict,
)
from reprocase.results.storage import RunStorage
from reprocase.runner.fakes import FakeAgent, FakeEnvironment, FakeGrader
from reprocase.runner.state_machine import RunStateMachine


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


def _verdict(failures: list[FailureRecord], graders: list[GraderResult]) -> Verdict:
    invalid_attributions = {
        FailureAttribution.ENVIRONMENT_ERROR,
        FailureAttribution.GRADER_ERROR,
        FailureAttribution.RUNNER_ERROR,
    }
    if any(failure.attribution in invalid_attributions for failure in failures):
        return Verdict.INVALID
    if any(grader.status == GraderStatus.ERROR for grader in graders):
        return Verdict.INVALID
    if any(failure.attribution == FailureAttribution.AGENT_FAILURE for failure in failures):
        return Verdict.FAIL
    if any(grader.status == GraderStatus.FAIL for grader in graders):
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


def run_case(case_path: Path | str, output_root: Path | str = ".reprocase/runs") -> RunResult:
    """Validate and execute one M0 fake EvalCase."""

    loaded = load_case(case_path)
    if not isinstance(loaded.environment, FakeEnvironmentDefinition):
        raise CaseValidationError(
            "reprocase run supports only the fake environment driver until M2; "
            "use reprocase env-check for docker_compose environments",
            code=FailureCode.CASE_UNSUPPORTED_DRIVER,
            details={"driver": loaded.environment.driver},
        )
    run_id = new_run_id()
    storage = RunStorage(Path(output_root), run_id)
    identity = _case_identity(loaded)
    manifest = RunManifest(
        run_id=run_id,
        case=identity,
        source=SourceIdentity(
            python=sys.version.split()[0],
            platform=f"{platform.system()}-{platform.machine()}",
        ),
        budget=loaded.case.budgets.model_dump(mode="json"),
    )
    storage.write_manifest(manifest)
    recorder = EventRecorder(storage.events_path, run_id)
    session = _RunSession(recorder)
    started_at = datetime.now(UTC)
    monotonic_started = time.monotonic_ns()
    failures: list[FailureRecord] = []
    graders: list[GraderResult] = []
    agent_output: Any = None
    environment: FakeEnvironment | None = None
    terminal_state = RunState.COMPLETED

    recorder.emit(
        "run.created",
        source="orchestrator",
        payload={"case_id": identity.id, "case_digest": identity.digest},
    )

    try:
        session.transition(RunState.VALIDATING)
        recorder.emit("case.validated", source="case_loader")

        session.transition(RunState.PREPARING_ENVIRONMENT)
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
            agent_output = agent_result.output
            recorder.emit(
                "agent.completed",
                source="fake_agent",
                payload={"output": agent_output},
            )
        except ReprocaseError as error:
            failures.append(_failure_record(error))
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
            recorder.emit(
                "grader.started",
                source="fake_grader",
                payload={"grader_id": spec.id},
            )
            grader_result = FakeGrader(spec).grade()
            graders.append(grader_result)
            recorder.emit(
                "grader.completed",
                source="fake_grader",
                payload=grader_result.model_dump(mode="json"),
            )
            if grader_result.status == GraderStatus.ERROR:
                failures.append(
                    FailureRecord(
                        attribution=FailureAttribution.GRADER_ERROR,
                        code=FailureCode.GRADER_FAKE_EXECUTION_FAILED,
                        message=grader_result.explanation,
                        details={"grader_id": grader_result.grader_id},
                    )
                )
            elif grader_result.status == GraderStatus.FAIL:
                failures.append(
                    FailureRecord(
                        attribution=FailureAttribution.AGENT_FAILURE,
                        code=FailureCode.GRADER_ASSERTION_FAILED,
                        message=grader_result.explanation,
                        details={"grader_id": grader_result.grader_id},
                    )
                )

    except KeyboardInterrupt:
        terminal_state = RunState.CANCELLED
        failures.append(
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
        failures.append(_failure_record(error))
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
        failures.append(failure)
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
        recorder.emit("artifacts.collected", source="orchestrator", payload={"count": 0})

        if session.machine.current == RunState.COLLECTING_ARTIFACTS:
            session.transition(RunState.TEARING_DOWN)
        if environment is not None:
            environment.teardown()
            recorder.emit("environment.teardown.completed", source="fake_environment")

    if session.machine.current == RunState.TEARING_DOWN:
        session.transition(RunState.WRITING_RESULT)

    verdict = _verdict(failures, graders)
    if terminal_state == RunState.CANCELLED:
        status = RunStatus.CANCELLED
        verdict = Verdict.INVALID
    elif verdict == Verdict.INVALID or terminal_state == RunState.COMPLETED_WITH_ERROR:
        status = RunStatus.COMPLETED_WITH_ERROR
        terminal_state = RunState.COMPLETED_WITH_ERROR
    else:
        status = RunStatus.COMPLETED
        terminal_state = RunState.COMPLETED

    finished_at = datetime.now(UTC)
    result = RunResult(
        run_id=run_id,
        status=status,
        verdict=verdict,
        final_state=terminal_state,
        case=identity,
        started_at=started_at,
        finished_at=finished_at,
        duration_ms=max(0, (time.monotonic_ns() - monotonic_started) // 1_000_000),
        agent_output=agent_output,
        graders=tuple(graders),
        primary_failure=failures[0] if failures else None,
        secondary_failures=tuple(failures[1:]),
        warnings=("M0 uses fake components and provides R0 evidence only.",),
    )
    storage.write_result(result)
    session.transition(terminal_state)
    recorder.emit(
        "run.completed",
        source="orchestrator",
        payload={"status": status, "verdict": verdict},
    )
    return result
