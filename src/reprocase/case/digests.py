"""Canonical serialization and content digests."""

from __future__ import annotations

import hashlib
import json
from typing import Any


def canonical_json(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    ).encode("utf-8")


def sha256_digest(value: Any) -> str:
    return f"sha256:{hashlib.sha256(canonical_json(value)).hexdigest()}"


def case_digest(case_data: dict[str, Any], environment_data: dict[str, Any]) -> str:
    return sha256_digest({"case": case_data, "environment": environment_data})
