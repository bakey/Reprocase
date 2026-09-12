"""Initial/final state recording on top of an environment driver."""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

from reprocase.case.digests import canonical_json
from reprocase.environments.protocol import EnvironmentDriver, EnvironmentHandle
from reprocase.results.models import ArtifactRef, StateSnapshot


class StateRecorder:
    """Captures canonical environment state into ``state.<phase>.json`` files."""

    def __init__(
        self,
        run_dir: Path,
        driver: EnvironmentDriver,
        handle: EnvironmentHandle,
    ) -> None:
        self.run_dir = run_dir
        self.driver = driver
        self.handle = handle

    async def capture(
        self, phase: Literal["initial", "final"]
    ) -> tuple[StateSnapshot, dict[str, Any]]:
        captured = await self.driver.capture_state(self.handle)
        # Keep the file byte-identical to the canonical form so the artifact
        # sha256 always equals the reported content digest.
        payload = canonical_json(captured.state)
        filename = f"state.{phase}.json"
        (self.run_dir / filename).write_bytes(payload)
        snapshot = StateSnapshot(
            captured_at=datetime.now(UTC),
            phase=phase,
            canonicalization_digest=captured.canonicalization_digest,
            content_digest=captured.content_digest,
            artifact=ArtifactRef(
                media_type="application/json",
                path=filename,
                size_bytes=len(payload),
                sha256=hashlib.sha256(payload).hexdigest(),
            ),
        )
        return snapshot, captured.state
