"""Content-addressed artifact storage inside a run directory."""

from __future__ import annotations

import hashlib
from pathlib import Path

from reprocase.results.models import ArtifactRef


class ArtifactStore:
    def __init__(self, run_dir: Path) -> None:
        self.run_dir = run_dir
        self.created: list[ArtifactRef] = []

    def put(self, content: bytes, *, media_type: str) -> ArtifactRef:
        digest = hashlib.sha256(content).hexdigest()
        relative = Path("artifacts") / "sha256" / digest[:2] / digest
        target = self.run_dir / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        if not target.exists():
            target.write_bytes(content)
        ref = ArtifactRef(
            media_type=media_type,
            path=relative.as_posix(),
            size_bytes=len(content),
            sha256=digest,
        )
        if not any(existing.sha256 == digest for existing in self.created):
            self.created.append(ref)
        return ref

    def put_text(self, content: str, *, media_type: str = "text/plain") -> ArtifactRef:
        return self.put(content.encode("utf-8"), media_type=media_type)
