from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from reprocase.case.models import EnvironmentDefinition, EvalCase
from reprocase.recording.events import EventEnvelope
from reprocase.results.models import RunManifest, RunResult

ROOT = Path(__file__).resolve().parents[1]


def test_committed_json_schemas_are_current() -> None:
    schemas: dict[str, type[Any]] = {
        "evalcase-v0.schema.json": EvalCase,
        "environment-v0.schema.json": EnvironmentDefinition,
        "event-v0.schema.json": EventEnvelope,
        "run-manifest-v0.schema.json": RunManifest,
        "run-result-v0.schema.json": RunResult,
    }

    for filename, model in schemas.items():
        committed = json.loads((ROOT / "schemas" / filename).read_text(encoding="utf-8"))
        assert committed == model.model_json_schema(), filename
