"""Tiny JSON-file data store. Stdlib-only; runs inside the container image."""

from __future__ import annotations

import json
import os
import tempfile
import threading
from collections.abc import Callable
from pathlib import Path
from typing import Any

DB_PATH = Path(os.environ.get("SUPPORT_WORLD_DB", "/data/db.json"))
FIXTURE_PATH = Path(os.environ.get("SUPPORT_WORLD_FIXTURE", "/fixtures/refund.json"))

_LOCK = threading.Lock()


def load_fixture(fixture_path: Path) -> dict[str, Any]:
    document = json.loads(fixture_path.read_text(encoding="utf-8"))
    if not isinstance(document, dict):
        raise ValueError(f"fixture must be a JSON object: {fixture_path}")
    return document


def _write_atomic(path: Path, document: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(document, ensure_ascii=False, indent=2, sort_keys=True)
    fd, temp_name = tempfile.mkstemp(dir=path.parent, prefix=".db-", suffix=".tmp")
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        handle.write(payload + "\n")
    os.replace(temp_name, path)


def load(db_path: Path = DB_PATH) -> dict[str, Any]:
    with _LOCK:
        document = json.loads(db_path.read_text(encoding="utf-8"))
    if not isinstance(document, dict):
        raise ValueError(f"database must be a JSON object: {db_path}")
    return document


def save(document: dict[str, Any], db_path: Path = DB_PATH) -> None:
    with _LOCK:
        _write_atomic(db_path, document)


def update(mutator: Callable[[dict[str, Any]], Any], db_path: Path = DB_PATH) -> Any:
    with _LOCK:
        document = json.loads(db_path.read_text(encoding="utf-8"))
        result = mutator(document)
        _write_atomic(db_path, document)
    return result


def reset(fixture_path: Path, db_path: Path = DB_PATH) -> dict[str, Any]:
    document = load_fixture(fixture_path)
    save(document, db_path)
    return document


def ensure_initialized(db_path: Path = DB_PATH, fixture_path: Path = FIXTURE_PATH) -> None:
    if not db_path.exists():
        reset(fixture_path, db_path)


def next_request_id(document: dict[str, Any]) -> str:
    runtime = document.setdefault("runtime", {})
    request_ids = runtime.setdefault("request_ids", [])
    request_id = f"req-{len(request_ids) + 1:04d}"
    request_ids.append(request_id)
    return request_id
