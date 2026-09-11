from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from pydantic import TypeAdapter

from reprocase.case.models import EnvironmentDefinition, EvalCase
from reprocase.recording.events import EventEnvelope
from reprocase.results.models import RunManifest, RunResult

ROOT = Path(__file__).resolve().parents[1]


def test_committed_json_schemas_are_current() -> None:
    schemas: dict[str, Any] = {
        "evalcase-v0.schema.json": EvalCase,
        "environment-v0.schema.json": TypeAdapter(EnvironmentDefinition),
        "event-v0.schema.json": EventEnvelope,
        "run-manifest-v0.schema.json": RunManifest,
        "run-result-v0.schema.json": RunResult,
    }

    for filename, model in schemas.items():
        committed = json.loads((ROOT / "schemas" / filename).read_text(encoding="utf-8"))
        if isinstance(model, TypeAdapter):
            assert committed == model.json_schema(), filename
        else:
            assert committed == model.model_json_schema(), filename
