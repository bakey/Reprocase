"""Scripted Actor: a deterministic test fixture, not a real agent adapter.

Actors are plain synchronous Python callables resolved from ``module:function``
entrypoints against the environment definition directory. They receive an
``ActorContext`` describing the running environment and return a JSON-ish
output (or raise, which the runner attributes to AGENT_FAILURE).
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, cast

from reprocase.entrypoints import load_callable

ActorFn = Callable[["ActorContext"], Any]


@dataclass(frozen=True)
class ActorContext:
    run_id: str
    task: dict[str, Any]
    base_url: str | None
    service_urls: dict[str, str] = field(default_factory=dict)
    ports: dict[str, int] = field(default_factory=dict)
    seed: int = 0


def load_actor(entrypoint: str, import_root: Path) -> ActorFn:
    return cast(ActorFn, load_callable(entrypoint, import_root))
