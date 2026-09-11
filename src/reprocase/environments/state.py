"""State capture canonicalization (design sections 7.5 and 10.1)."""

from __future__ import annotations

import json
from typing import Any

from reprocase.case.digests import sha256_digest
from reprocase.case.models import CanonicalizationSpec
from reprocase.environments.protocol import CapturedState
from reprocase.errors import EnvironmentDriverError, FailureCode


def _parse_pointer(pointer: str) -> list[str]:
    if not pointer.startswith("/"):
        raise EnvironmentDriverError(
            f"Canonicalization path is not an RFC 6901 pointer: {pointer!r}",
            code=FailureCode.ENV_STATE_INVALID,
            details={"path": pointer},
        )
    return [token.replace("~1", "/").replace("~0", "~") for token in pointer[1:].split("/")]


def _state_invalid(message: str, *, pointer: str) -> EnvironmentDriverError:
    return EnvironmentDriverError(
        message,
        code=FailureCode.ENV_STATE_INVALID,
        details={"path": pointer},
    )


def _resolve_parent(document: Any, tokens: list[str], pointer: str) -> tuple[Any, str]:
    current = document
    for token in tokens[:-1]:
        if isinstance(current, dict) and token in current:
            current = current[token]
        elif isinstance(current, list) and token.isdigit() and int(token) < len(current):
            current = current[int(token)]
        else:
            raise _state_invalid(
                f"Path does not exist in captured state: {pointer}", pointer=pointer
            )
    return current, tokens[-1]


def _remove_path(document: Any, pointer: str) -> None:
    tokens = _parse_pointer(pointer)
    current: Any = document
    for token in tokens[:-1]:
        if isinstance(current, dict):
            current = current.get(token)
        elif isinstance(current, list) and token.isdigit() and int(token) < len(current):
            current = current[int(token)]
        else:
            return
    if current is None:
        return
    last = tokens[-1]
    if isinstance(current, dict):
        current.pop(last, None)
    elif isinstance(current, list) and last.isdigit() and int(last) < len(current):
        current.pop(int(last))


def _sort_array(document: Any, pointer: str, key: str) -> None:
    tokens = _parse_pointer(pointer)
    parent, last = _resolve_parent(document, tokens, pointer)
    target: Any = None
    if isinstance(parent, dict) and last in parent:
        target = parent[last]
    elif isinstance(parent, list) and last.isdigit() and int(last) < len(parent):
        target = parent[int(last)]
    else:
        raise _state_invalid(f"Path does not exist in captured state: {pointer}", pointer=pointer)
    if not isinstance(target, list):
        raise _state_invalid(f"sort_arrays_by target is not an array: {pointer}", pointer=pointer)
    for element in target:
        if not isinstance(element, dict) or key not in element:
            raise _state_invalid(
                f"sort_arrays_by elements at {pointer} must be objects containing {key!r}",
                pointer=pointer,
            )
    target.sort(key=lambda element: json.dumps(element[key], sort_keys=True))


def parse_state_document(raw: str) -> dict[str, Any]:
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise EnvironmentDriverError(
            f"State capture output is not a valid JSON document: {exc}",
            code=FailureCode.ENV_STATE_INVALID,
            details={"error": str(exc), "output_preview": raw[:500]},
        ) from exc
    if not isinstance(parsed, dict):
        raise EnvironmentDriverError(
            "State capture output must be a single JSON object",
            code=FailureCode.ENV_STATE_INVALID,
            details={"type": type(parsed).__name__},
        )
    return parsed


def canonicalize_state(raw: str, spec: CanonicalizationSpec) -> CapturedState:
    document = parse_state_document(raw)
    for pointer in spec.remove_paths:
        _remove_path(document, pointer)
    for pointer, key in spec.sort_arrays_by.items():
        _sort_array(document, pointer, key)
    return CapturedState(
        state=document,
        content_digest=sha256_digest(document),
        canonicalization_digest=sha256_digest(spec.model_dump(mode="json")),
    )
