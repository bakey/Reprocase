"""RFC 6902-style JSON Patch diff between canonical states (ADR-007, section 10.2).

``replace`` operations carry an additional ``from_value`` member for
readability; the document is tagged with its own schema version.
"""

from __future__ import annotations

from typing import Any

SCHEMA_VERSION = "reprocase.state-diff/v0"


def _escape(token: str) -> str:
    return token.replace("~", "~0").replace("/", "~1")


def _walk(base: str, initial: Any, final: Any, ops: list[dict[str, Any]]) -> None:
    if isinstance(initial, dict) and isinstance(final, dict):
        for key in sorted(initial):
            path = f"{base}/{_escape(str(key))}"
            if key not in final:
                ops.append({"op": "remove", "path": path})
            else:
                _walk(path, initial[key], final[key], ops)
        for key in sorted(final):
            if key not in initial:
                ops.append(
                    {"op": "add", "path": f"{base}/{_escape(str(key))}", "value": final[key]}
                )
        return
    if isinstance(initial, list) and isinstance(final, list):
        shared = min(len(initial), len(final))
        for index in range(shared):
            _walk(f"{base}/{index}", initial[index], final[index], ops)
        for index in range(shared, len(initial)):
            ops.append({"op": "remove", "path": f"{base}/{index}"})
        for index in range(shared, len(final)):
            ops.append({"op": "add", "path": f"{base}/{index}", "value": final[index]})
        return
    if initial != final:
        ops.append({"op": "replace", "path": base, "from_value": initial, "value": final})


def compute_diff(initial: Any, final: Any) -> list[dict[str, Any]]:
    ops: list[dict[str, Any]] = []
    _walk("", initial, final, ops)
    return ops


def diff_document(initial: Any, final: Any) -> dict[str, Any]:
    return {"schema_version": SCHEMA_VERSION, "ops": compute_diff(initial, final)}
