"""Tiny JSON-file data store. Stdlib-only; runs inside the container image."""

from __future__ import annotations

import json
import os
import tempfile
import threading
from collections.abc import Callable
from pathlib import Path
from typing import Any

_LOCK = threading.Lock()


def _db_path() -> Path:
    # Read at call time so host-level tests can point the store at a tmp path.
    return Path(os.environ.get("ACME_WORLD_DB", "/data/db.json"))


def _fixture_path() -> Path:
    return Path(os.environ.get("ACME_WORLD_FIXTURE", "/fixtures/acme.json"))


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


def load(db_path: Path | None = None) -> dict[str, Any]:
    with _LOCK:
        document = json.loads((db_path or _db_path()).read_text(encoding="utf-8"))
    if not isinstance(document, dict):
        raise ValueError(f"database must be a JSON object: {db_path}")
    return document


def save(document: dict[str, Any], db_path: Path | None = None) -> None:
    with _LOCK:
        _write_atomic(db_path or _db_path(), document)


def update(mutator: Callable[[dict[str, Any]], Any], db_path: Path | None = None) -> Any:
    with _LOCK:
        path = db_path or _db_path()
        document = json.loads(path.read_text(encoding="utf-8"))
        result = mutator(document)
        _write_atomic(path, document)
    return result


def reset(fixture_path: Path, db_path: Path | None = None) -> dict[str, Any]:
    document = load_fixture(fixture_path)
    save(document, db_path)
    return document


def ensure_initialized(db_path: Path | None = None, fixture_path: Path | None = None) -> None:
    db = db_path or _db_path()
    if not db.exists():
        reset(fixture_path or _fixture_path(), db)


def next_request_id(document: dict[str, Any]) -> str:
    runtime = document.setdefault("runtime", {})
    request_ids = runtime.setdefault("request_ids", [])
    request_id = f"req-{len(request_ids) + 1:04d}"
    request_ids.append(request_id)
    return request_id
