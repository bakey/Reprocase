"""Test command grader (design section 11.4)."""

from __future__ import annotations

import json
import time
from typing import Any

from reprocase.case.digests import sha256_digest
from reprocase.case.models import TestCommandGraderSpec
from reprocase.environments.protocol import CommandResult
from reprocase.errors import EnvironmentDriverError, FailureCode
from reprocase.graders.protocol import ExecFn, GraderContext
from reprocase.recording.artifacts import ArtifactStore
from reprocase.results.models import ArtifactRef, GraderResult, GraderStatus


def _parse_output(stdout: str) -> dict[str, Any] | None:
    try:
        parsed = json.loads(stdout)
    except json.JSONDecodeError:
        return None
    return parsed if isinstance(parsed, dict) else None


def interpret_command_result(
    spec: TestCommandGraderSpec,
    result: CommandResult,
    *,
    evidence_prefix: tuple[Any, ...] = (),
) -> GraderResult:
    """Map exit-code semantics to a GraderResult. Pure; artifacts are added by the caller."""

    digest = sha256_digest(spec.model_dump(mode="json"))
    base: dict[str, Any] = {
        "grader_id": spec.id,
        "critical": spec.critical,
        "grader_type": "test_command",
        "grader_digest": digest,
        "duration_ms": result.duration_ms,
    }
    output = _parse_output(result.stdout)
    stderr_tail = result.stderr.strip()[-500:]

    if result.exit_code == 0:
        if output is None:
            return GraderResult(
                **base,
                status=GraderStatus.ERROR,
                explanation="exit code 0 but stdout is not a JSON object",
                actual={"stdout_preview": result.stdout[:500]},
                evidence=evidence_prefix,
            )
        passed = output.get("passed")
        if not isinstance(passed, bool):
            return GraderResult(
                **base,
                status=GraderStatus.ERROR,
                explanation="stdout JSON lacks a boolean 'passed' field",
                actual=output,
                evidence=evidence_prefix,
            )
        return GraderResult(
            **base,
            status=GraderStatus.PASS if passed else GraderStatus.FAIL,
            explanation=str(output.get("explanation") or ("passed" if passed else "failed")),
            expected=output.get("expected"),
            actual=output.get("actual"),
            evidence=evidence_prefix + tuple(output.get("evidence") or []),
        )

    if result.exit_code == 1:
        if output is not None:
            return GraderResult(
                **base,
                status=GraderStatus.FAIL,
                explanation=str(output.get("explanation") or "evaluation failed"),
                expected=output.get("expected"),
                actual=output.get("actual"),
                evidence=evidence_prefix + tuple(output.get("evidence") or []),
            )
        return GraderResult(
            **base,
            status=GraderStatus.FAIL,
            explanation=f"evaluation failed (exit 1): {result.stdout.strip()[:500]}",
            actual={"stdout_preview": result.stdout[:500]},
            evidence=evidence_prefix,
        )

    if 2 <= result.exit_code <= 125:
        return GraderResult(
            **base,
            status=GraderStatus.ERROR,
            explanation=f"grader command errored (exit {result.exit_code}): {stderr_tail}",
            actual={"exit_code": result.exit_code, "stderr_preview": stderr_tail},
            evidence=evidence_prefix,
        )

    return GraderResult(
        **base,
        status=GraderStatus.ERROR,
        explanation=f"unexpected exit code {result.exit_code}: {stderr_tail}",
        actual={"exit_code": result.exit_code},
        evidence=evidence_prefix,
    )


class TestCommandGrader:
    def __init__(
        self,
        spec: TestCommandGraderSpec,
        *,
        exec_fn: ExecFn,
        artifacts: ArtifactStore,
    ) -> None:
        self.spec = spec
        self.exec_fn = exec_fn
        self.artifacts = artifacts

    async def grade(self, context: GraderContext) -> GraderResult:
        started = time.monotonic_ns()
        digest = sha256_digest(self.spec.model_dump(mode="json"))
        try:
            result = await self.exec_fn(
                self.spec.service,
                list(self.spec.command),
                float(self.spec.timeout_seconds),
            )
        except EnvironmentDriverError as exc:
            code = (
                FailureCode.GRADER_TIMEOUT
                if exc.code == FailureCode.GRADER_TIMEOUT
                else FailureCode.GRADER_EXECUTION_ERROR
            )
            return GraderResult(
                grader_id=self.spec.id,
                status=GraderStatus.ERROR,
                critical=self.spec.critical,
                explanation=f"grader command could not run: {exc.message}",
                grader_type="test_command",
                grader_digest=digest,
                duration_ms=max(0, (time.monotonic_ns() - started) // 1_000_000),
                actual={"failure_code": str(code)},
            )

        refs: list[ArtifactRef] = [
            self.artifacts.put_text(result.stdout, media_type="text/plain"),
            self.artifacts.put_text(result.stderr, media_type="text/plain"),
        ]
        evidence: tuple[Any, ...] = tuple(f"artifact:{ref.sha256}" for ref in refs)
        graded = interpret_command_result(self.spec, result, evidence_prefix=evidence)
        return graded.model_copy(update={"duration_ms": result.duration_ms})
