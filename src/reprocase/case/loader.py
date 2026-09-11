"""YAML loading, reference resolution, and validation."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml
from pydantic import TypeAdapter, ValidationError

from reprocase.case.digests import case_digest
from reprocase.case.models import EnvironmentDefinition, EvalCase
from reprocase.errors import CaseValidationError, FailureCode

_ENVIRONMENT_ADAPTER: TypeAdapter[EnvironmentDefinition] = TypeAdapter(EnvironmentDefinition)


@dataclass(frozen=True)
class LoadedCase:
    path: Path
    root: Path
    case: EvalCase
    environment_path: Path
    environment: EnvironmentDefinition
    digest: str
    compose_path: Path | None = None


def _read_yaml(path: Path) -> dict[str, Any]:
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise CaseValidationError(
            f"Referenced file does not exist: {path}",
            code=FailureCode.CASE_REFERENCE_MISSING,
            details={"path": str(path)},
        ) from exc
    except yaml.YAMLError as exc:
        raise CaseValidationError(
            f"Invalid YAML in {path}: {exc}",
            details={"path": str(path)},
        ) from exc

    if not isinstance(raw, dict):
        raise CaseValidationError(
            f"Expected a YAML mapping at document root: {path}",
            details={"path": str(path)},
        )
    return raw


def _format_validation_error(path: Path, exc: ValidationError) -> CaseValidationError:
    errors: list[dict[str, Any]] = []
    messages: list[str] = []
    for error in exc.errors(include_url=False):
        location = ".".join(str(part) for part in error["loc"])
        message = str(error["msg"])
        errors.append({"location": location, "message": message, "type": error["type"]})
        messages.append(f"{location}: {message}")
    return CaseValidationError(
        f"Schema validation failed for {path}: " + "; ".join(messages),
        details={"path": str(path), "errors": errors},
    )


def _resolve_reference(root: Path, reference: Path) -> Path:
    candidate = (root / reference).resolve()
    try:
        candidate.relative_to(root)
    except ValueError as exc:
        raise CaseValidationError(
            f"Reference escapes the EvalCase root: {reference}",
            code=FailureCode.CASE_REFERENCE_OUTSIDE_ROOT,
            details={"root": str(root), "reference": str(reference)},
        ) from exc
    return candidate


def load_case(path: Path | str) -> LoadedCase:
    case_path = Path(path).expanduser().resolve()
    root = case_path.parent
    raw_case = _read_yaml(case_path)
    try:
        case = EvalCase.model_validate(raw_case)
    except ValidationError as exc:
        raise _format_validation_error(case_path, exc) from exc

    environment_path = _resolve_reference(root, case.environment.definition)
    raw_environment = _read_yaml(environment_path)
    try:
        environment = _ENVIRONMENT_ADAPTER.validate_python(raw_environment)
    except ValidationError as exc:
        raise _format_validation_error(environment_path, exc) from exc

    if environment.driver != case.environment.driver:
        raise CaseValidationError(
            "Environment driver does not match the EvalCase reference",
            details={
                "case_driver": case.environment.driver,
                "environment_driver": environment.driver,
            },
        )

    compose_path: Path | None = None
    extra_files: dict[str, str] = {}
    if case.environment.compose_file is not None:
        compose_path = _resolve_reference(root, case.environment.compose_file)
        try:
            extra_files["compose_file"] = compose_path.read_text(encoding="utf-8")
        except FileNotFoundError as exc:
            raise CaseValidationError(
                f"Referenced file does not exist: {compose_path}",
                code=FailureCode.CASE_REFERENCE_MISSING,
                details={"path": str(compose_path)},
            ) from exc

    normalized_case = case.model_dump(mode="json")
    normalized_environment = environment.model_dump(mode="json")
    digest = case_digest(normalized_case, normalized_environment, extra_files=extra_files)
    return LoadedCase(
        path=case_path,
        root=root,
        case=case,
        environment_path=environment_path,
        environment=environment,
        digest=digest,
        compose_path=compose_path,
    )
