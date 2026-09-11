"""Export or verify committed JSON Schemas."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from reprocase.case.models import EnvironmentDefinition, EvalCase
from reprocase.recording.events import EventEnvelope
from reprocase.results.models import RunManifest, RunResult

ROOT = Path(__file__).resolve().parents[1]
SCHEMA_DIR = ROOT / "schemas"

SCHEMAS = {
    "evalcase-v0.schema.json": EvalCase,
    "environment-v0.schema.json": EnvironmentDefinition,
    "event-v0.schema.json": EventEnvelope,
    "run-manifest-v0.schema.json": RunManifest,
    "run-result-v0.schema.json": RunResult,
}


def render_schema(model: type[Any]) -> str:
    schema = model.model_json_schema()
    return json.dumps(schema, ensure_ascii=False, indent=2, sort_keys=True) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true", help="Fail if committed schemas are stale.")
    args = parser.parse_args()
    SCHEMA_DIR.mkdir(exist_ok=True)

    stale: list[str] = []
    for filename, model in SCHEMAS.items():
        target = SCHEMA_DIR / filename
        rendered = render_schema(model)
        if args.check:
            if not target.exists() or target.read_text(encoding="utf-8") != rendered:
                stale.append(filename)
        else:
            target.write_text(rendered, encoding="utf-8")

    if stale:
        print("Stale schemas: " + ", ".join(stale))
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
