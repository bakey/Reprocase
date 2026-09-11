"""Versioned event envelope and durable JSONL recorder."""

from __future__ import annotations

import json
import os
import threading
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from reprocase.errors import FailureAttribution, FailureCode, ReprocaseError
from reprocase.ids import new_event_id


class EventEnvelope(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["reprocase.event/v0"] = "reprocase.event/v0"
    event_id: str = Field(default_factory=new_event_id)
    run_id: str
    sequence: int = Field(ge=1)
    timestamp: datetime = Field(default_factory=lambda: datetime.now(UTC))
    monotonic_ns: int = Field(default_factory=time.monotonic_ns, ge=0)
    type: str
    source: str
    payload: dict[str, Any] = Field(default_factory=dict)
    attributes: dict[str, Any] = Field(default_factory=dict)


class EventRecorder:
    """Thread-safe, append-only JSONL recorder with durable writes."""

    def __init__(self, path: Path, run_id: str) -> None:
        self.path = path
        self.run_id = run_id
        self._sequence = 0
        self._lock = threading.Lock()
        self.path.touch(mode=0o600, exist_ok=False)

    def emit(
        self,
        event_type: str,
        *,
        source: str,
        payload: dict[str, Any] | None = None,
        attributes: dict[str, Any] | None = None,
    ) -> EventEnvelope:
        with self._lock:
            self._sequence += 1
            event = EventEnvelope(
                run_id=self.run_id,
                sequence=self._sequence,
                type=event_type,
                source=source,
                payload=payload or {},
                attributes=attributes or {},
            )
            try:
                with self.path.open("a", encoding="utf-8") as handle:
                    handle.write(json.dumps(event.model_dump(mode="json"), sort_keys=True))
                    handle.write("\n")
                    handle.flush()
                    os.fsync(handle.fileno())
            except OSError as exc:
                raise ReprocaseError(
                    f"Could not append event: {self.path}",
                    code=FailureCode.RUNNER_STORAGE_ERROR,
                    attribution=FailureAttribution.RUNNER_ERROR,
                    details={"path": str(self.path)},
                ) from exc
            return event
