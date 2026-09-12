"""Shared ``module:function`` entrypoint loading for trusted local code."""

from __future__ import annotations

import importlib
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any, cast

from reprocase.errors import CaseValidationError, FailureCode


def load_callable(entrypoint: str, import_root: Path) -> Callable[..., Any]:
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
                f"Cannot import module {module_name!r} from {root}: {exc}",
                code=FailureCode.CASE_ENTRYPOINT_INVALID,
                details={"entrypoint": entrypoint, "import_root": root},
            ) from exc
    finally:
        sys.path.remove(root)
    target = getattr(module, function_name, None)
    if not callable(target):
        raise CaseValidationError(
            f"Entrypoint {entrypoint!r} is not callable",
            code=FailureCode.CASE_ENTRYPOINT_INVALID,
            details={"entrypoint": entrypoint},
        )
    return cast(Callable[..., Any], target)
