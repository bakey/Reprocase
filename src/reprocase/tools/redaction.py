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


def scrub_secrets(value: Any, secrets: tuple[str, ...] | list[str]) -> Any:
    """Value-based scrubbing: replace known secret substrings inside every
    string of an arbitrary JSON-like structure. Key-based redaction cannot
    catch a secret echoed into free text (e.g. a model repeating an API key),
    so the runner also scrubs the concrete secret values it knows from the
    environment. Values shorter than 8 characters are ignored: real API keys
    are far longer, and scrubbing short dev placeholders (e.g. "test") would
    shred ordinary words and destroy evidence readability.
    """

    usable = [secret for secret in secrets if secret and len(secret) >= 8]

    def walk(node: Any) -> Any:
        if isinstance(node, str):
            scrubbed = node
            for secret in usable:
                scrubbed = scrubbed.replace(secret, REDACTED)
            return scrubbed
        if isinstance(node, dict):
            return {key: walk(item) for key, item in node.items()}
        if isinstance(node, list | tuple):
            return [walk(item) for item in node]
        return node

    return walk(value) if usable else value
