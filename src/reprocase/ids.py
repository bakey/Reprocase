"""Identifier generation."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4


def new_run_id(now: datetime | None = None) -> str:
    """Return a sortable, filesystem-safe run identifier."""

    timestamp = (now or datetime.now(UTC)).astimezone(UTC).strftime("%Y%m%dT%H%M%S%fZ")
    return f"run_{timestamp}_{uuid4().hex[:10]}"


def new_event_id() -> str:
    return f"evt_{uuid4().hex}"
