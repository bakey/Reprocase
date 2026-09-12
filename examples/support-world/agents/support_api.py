"""Tiny stdlib HTTP helpers shared by the scripted actors."""

from __future__ import annotations

import json
import urllib.request
from typing import Any


def get(base_url: str, path: str) -> Any:
    with urllib.request.urlopen(f"{base_url}{path}", timeout=10) as response:
        return json.loads(response.read().decode("utf-8"))


def post(base_url: str, path: str, body: dict[str, Any]) -> Any:
    request = urllib.request.Request(
        f"{base_url}{path}",
        data=json.dumps(body).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=10) as response:
        return json.loads(response.read().decode("utf-8"))
