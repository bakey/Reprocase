"""Internal environment diagnostics: repeated restore/capture cycles."""

from __future__ import annotations

import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from uuid import uuid4

import yaml
from pydantic import TypeAdapter, ValidationError

from reprocase.case.loader import load_case, resolve_restore_fixture
from reprocase.case.models import ComposeEnvironmentDefinition, EnvironmentDefinition
from reprocase.environments.compose import ComposeEnvironmentDriver, resolve_compose_file
from reprocase.environments.protocol import EnvironmentHandle
from reprocase.errors import (
    CaseValidationError,
    EnvironmentDriverError,
    FailureCode,
)

_ENVIRONMENT_ADAPTER: TypeAdapter[EnvironmentDefinition] = TypeAdapter(EnvironmentDefinition)


@dataclass(frozen=True)
class CycleOutcome:
    cycle: int
    digest: str | None
    duration_ms: int
    failure_code: FailureCode | None = None
    failure_message: str | None = None


@dataclass(frozen=True)
class ComposeTarget:
    """A resolved env-check target: definition, compose file, slug, fixture."""

    definition: ComposeEnvironmentDefinition
    compose_file: Path
    slug: str
    fixture: str | None = None


@dataclass(frozen=True)
class EnvCheckReport:
    cycles: tuple[CycleOutcome, ...]

    @property
    def failures(self) -> tuple[CycleOutcome, ...]:
        return tuple(cycle for cycle in self.cycles if cycle.failure_code is not None)

    @property
    def digests(self) -> tuple[str, ...]:
        return tuple(cycle.digest for cycle in self.cycles if cycle.digest is not None)

    @property
    def digests_identical(self) -> bool:
        digests = self.digests
        return len(digests) == len(self.cycles) and len(set(digests)) == 1


def _resolve_compose_file(definition: ComposeEnvironmentDefinition, base: Path) -> Path:
    return resolve_compose_file(definition, base)


def load_compose_target(path: Path | str) -> ComposeTarget:
    """Load an environment.yaml or EvalCase for env-check, resolving the fixture."""

    target = Path(path).expanduser().resolve()
    try:
        raw: Any = yaml.safe_load(target.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise CaseValidationError(f"Cannot read {target}: {exc}") from exc

    if isinstance(raw, dict) and raw.get("kind") == "EvalCase":
        loaded = load_case(target)
        if not isinstance(loaded.environment, ComposeEnvironmentDefinition):
            raise CaseValidationError(
                "env-check requires a docker_compose environment",
                code=FailureCode.CASE_UNSUPPORTED_DRIVER,
                details={"driver": loaded.environment.driver},
            )
        compose_file = loaded.compose_path or _resolve_compose_file(
            loaded.environment, loaded.environment_path.parent
        )
        return ComposeTarget(
            definition=loaded.environment,
            compose_file=compose_file,
            slug=loaded.case.metadata.id,
            fixture=loaded.fixture,
        )

    try:
        definition = _ENVIRONMENT_ADAPTER.validate_python(raw)
    except ValidationError as exc:
        raise CaseValidationError(f"Schema validation failed for {target}: {exc}") from exc
    if not isinstance(definition, ComposeEnvironmentDefinition):
        raise CaseValidationError(
            "env-check requires a docker_compose environment",
            code=FailureCode.CASE_UNSUPPORTED_DRIVER,
            details={"driver": definition.driver},
        )
    compose_file = _resolve_compose_file(definition, target.parent)
    return ComposeTarget(
        definition=definition,
        compose_file=compose_file,
        slug=target.parent.name,
        fixture=resolve_restore_fixture(case_fixture=None, environment=definition),
    )


async def run_environment_check(
    target: ComposeTarget,
    *,
    cycles: int,
    work_root: Path,
    allow_unsafe: bool = False,
    fixture_override: str | None = None,
) -> EnvCheckReport:
    definition, compose_file, slug = target.definition, target.compose_file, target.slug
    fixture = fixture_override if fixture_override is not None else target.fixture
    if fixture is not None and not definition.restore.references_fixture:
        raise CaseValidationError(
            "A fixture was provided but the restore command contains no "
            "${FIXTURE} placeholder to receive it",
            code=FailureCode.CASE_SCHEMA_INVALID,
            details={"fixture": fixture},
        )
    outcomes: list[CycleOutcome] = []
    for cycle in range(1, cycles + 1):
        started = time.monotonic_ns()
        driver = ComposeEnvironmentDriver(
            definition,
            compose_file=compose_file,
            slug=slug,
            run_suffix=uuid4().hex[:8],
            work_root=work_root,
            allow_unsafe=allow_unsafe,
            fixture=fixture,
        )
        handle = None
        prepared = False
        digest: str | None = None
        failure: EnvironmentDriverError | None = None
        try:
            await driver.prepare()
            prepared = True
            handle = await driver.start()
            await driver.restore(handle)
            await driver.healthcheck(handle)
            captured = await driver.capture_state(handle)
            digest = captured.content_digest
            await driver.collect_logs(handle)
        except EnvironmentDriverError as exc:
            failure = exc
        finally:
            # A failed `up` can still leave containers behind, so attempt
            # teardown whenever prepare completed, even without a handle.
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
                except EnvironmentDriverError as exc:
                    if failure is None:
                        failure = exc
        duration_ms = max(0, (time.monotonic_ns() - started) // 1_000_000)
        outcomes.append(
            CycleOutcome(
                cycle=cycle,
                digest=digest,
                duration_ms=duration_ms,
                failure_code=failure.code if failure is not None else None,
                failure_message=failure.message if failure is not None else None,
            )
        )
    return EnvCheckReport(cycles=tuple(outcomes))
