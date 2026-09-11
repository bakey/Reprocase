"""Crash-aware local run storage."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from pydantic import BaseModel

from reprocase.errors import FailureAttribution, FailureCode, ReprocaseError


class RunStorage:
    def __init__(self, output_root: Path, run_id: str) -> None:
        self.run_id = run_id
        self.run_dir = output_root.resolve() / run_id
        try:
            self.run_dir.mkdir(parents=True, exist_ok=False, mode=0o700)
        except OSError as exc:
            raise ReprocaseError(
                f"Could not create run directory: {self.run_dir}",
                code=FailureCode.RUNNER_STORAGE_ERROR,
                attribution=FailureAttribution.RUNNER_ERROR,
                details={"path": str(self.run_dir)},
            ) from exc

    @property
    def events_path(self) -> Path:
        return self.run_dir / "events.jsonl"

    def write_manifest(self, manifest: BaseModel) -> None:
        self._write_json_atomic("manifest.json", manifest)

    def write_result(self, result: BaseModel) -> None:
        self._write_json_atomic("result.json", result)

    def _write_json_atomic(self, filename: str, value: BaseModel | dict[str, Any]) -> None:
        target = self.run_dir / filename
        temporary = self.run_dir / f".{filename}.tmp"
        payload = value.model_dump(mode="json") if isinstance(value, BaseModel) else value
        try:
            with temporary.open("w", encoding="utf-8") as handle:
                json.dump(payload, handle, ensure_ascii=False, indent=2, sort_keys=True)
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, target)
        except OSError as exc:
            temporary.unlink(missing_ok=True)
            raise ReprocaseError(
                f"Could not write run artifact: {target}",
                code=FailureCode.RUNNER_STORAGE_ERROR,
                attribution=FailureAttribution.RUNNER_ERROR,
                details={"path": str(target)},
            ) from exc
