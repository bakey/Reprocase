"""RFC 6901 JSON Pointer resolution (ADR-007)."""

from __future__ import annotations

from typing import Any

MISSING: Any = object()


def parse_pointer(pointer: str) -> list[str]:
    if pointer == "":
        return []
    if not pointer.startswith("/"):
        raise ValueError(f"not an RFC 6901 pointer: {pointer!r}")
    return [token.replace("~1", "/").replace("~0", "~") for token in pointer[1:].split("/")]


def resolve(document: Any, pointer: str) -> Any:
    """Return the value at ``pointer``, or the MISSING sentinel."""

    current = document
    for token in parse_pointer(pointer):
        if isinstance(current, dict):
            if token not in current:
                return MISSING
            current = current[token]
        elif isinstance(current, list):
            if not token.isdigit() or int(token) >= len(current):
                return MISSING
            current = current[int(token)]
        else:
            return MISSING
    return current
