"""Key-based redaction for event payloads and results (design section 17.1)."""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

REDACTED = "[REDACTED]"
SENSITIVE_KEYS = frozenset({"authorization", "cookie", "token", "password", "api_key"})


def redact(value: Any, *, extra_keys: Iterable[str] = ()) -> Any:
    sensitive = SENSITIVE_KEYS | {key.lower() for key in extra_keys}
    return _redact(value, sensitive)


def _redact(value: Any, sensitive: frozenset[str] | set[str]) -> Any:
    if isinstance(value, dict):
        return {
            key: REDACTED if str(key).lower() in sensitive else _redact(item, sensitive)
            for key, item in value.items()
        }
    if isinstance(value, list | tuple):
        # tuples collapse to JSON-compatible lists
        return [_redact(item, sensitive) for item in value]
    return value
