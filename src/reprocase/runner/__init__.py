"""Local orchestration.

``run_case`` is imported lazily so that importing any ``reprocase.runner.*``
submodule (or ``reprocase.graders.*``, which pulls in ``runner.fakes``) as the
process's first import cannot trigger a circular import through the
orchestrator's grader imports.
"""

from __future__ import annotations

from typing import Any

__all__ = ["run_case"]


def __getattr__(name: str) -> Any:
    if name == "run_case":
        from reprocase.runner.orchestrator import run_case

        return run_case
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
