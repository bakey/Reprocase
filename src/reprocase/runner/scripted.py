"""Scripted Actor: a deterministic test fixture, not a real agent adapter.

Actors are plain synchronous Python callables resolved from ``module:function``
entrypoints against the environment definition directory. They receive an
``ActorContext`` describing the running environment and return a JSON-ish
output (or raise, which the runner attributes to AGENT_FAILURE).
"""

from __future__ import annotations

import importlib
import sys
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, cast

from reprocase.errors import CaseValidationError, FailureCode

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
    module_name, separator, function_name = entrypoint.partition(":")
    if not separator or not module_name or not function_name:
        raise CaseValidationError(
            f"Invalid entrypoint {entrypoint!r}; expected 'module:function'",
            code=FailureCode.CASE_ENTRYPOINT_INVALID,
            details={"entrypoint": entrypoint},
        )
    root = str(import_root.resolve())
    sys.path.insert(0, root)
    try:
        try:
            module = importlib.import_module(module_name)
        except ImportError as exc:
            raise CaseValidationError(
                f"Cannot import actor module {module_name!r} from {root}: {exc}",
                code=FailureCode.CASE_ENTRYPOINT_INVALID,
                details={"entrypoint": entrypoint, "import_root": root},
            ) from exc
    finally:
        sys.path.remove(root)
    actor = getattr(module, function_name, None)
    if not callable(actor):
        raise CaseValidationError(
            f"Entrypoint {entrypoint!r} is not callable",
            code=FailureCode.CASE_ENTRYPOINT_INVALID,
            details={"entrypoint": entrypoint},
        )
    return cast(ActorFn, actor)
