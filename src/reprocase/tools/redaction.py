"""Key-based redaction for event payloads (design section 17.1)."""

from __future__ import annotations

from typing import Any

REDACTED = "[REDACTED]"
SENSITIVE_KEYS = frozenset({"authorization", "cookie", "token", "password", "api_key"})


def redact(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            key: REDACTED if str(key).lower() in SENSITIVE_KEYS else redact(item)
            for key, item in value.items()
        }
    if isinstance(value, list | tuple):
        # tuples collapse to JSON-compatible lists
        return [redact(item) for item in value]
    return value
