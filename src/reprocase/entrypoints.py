"""Shared ``module:function`` entrypoint loading for trusted local code.

``importlib.import_module`` caches by module name in ``sys.modules``, so two
EvalCases living in different roots that both define ``agents.main`` would
collide: the second load would silently return the first root's module. To
keep every root isolated, the loader temporarily evicts any cached modules
under the entrypoint's top-level package, imports fresh from the requested
root, then restores the previous ``sys.modules`` entries. The returned
callable keeps referencing the freshly loaded module even after its
``sys.modules`` entries are restored.

The loader mutates ``sys.path``/``sys.modules`` and is therefore not
thread-safe; the runner performs loads sequentially in one thread.
"""

from __future__ import annotations

import hashlib
import importlib
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any, cast

from reprocase.errors import CaseValidationError, FailureCode
from reprocase.results.models import CodeIdentity


def load_callable(entrypoint: str, import_root: Path) -> Callable[..., Any]:
    module_name, separator, function_name = entrypoint.partition(":")
    if not separator or not module_name or not function_name:
        raise CaseValidationError(
            f"Invalid entrypoint {entrypoint!r}; expected 'module:function'",
            code=FailureCode.CASE_ENTRYPOINT_INVALID,
            details={"entrypoint": entrypoint},
        )
    root = str(import_root.resolve())
    top_level = module_name.split(".", 1)[0]
    shadowed = {
        name: module
        for name, module in sys.modules.items()
        if name == top_level or name.startswith(f"{top_level}.")
    }
    for name in shadowed:
        del sys.modules[name]
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
        # evict this root's fresh entries and restore the shadowed ones
        for name in [
            name for name in sys.modules if name == top_level or name.startswith(f"{top_level}.")
        ]:
            del sys.modules[name]
        sys.modules.update(shadowed)
    target = getattr(module, function_name, None)
    if not callable(target):
        raise CaseValidationError(
            f"Entrypoint {entrypoint!r} is not callable",
            code=FailureCode.CASE_ENTRYPOINT_INVALID,
            details={"entrypoint": entrypoint},
        )
    return cast(Callable[..., Any], target)


def module_file(module_name: str, import_root: Path) -> Path | None:
    """Resolve a dotted module name to its file inside ``import_root``."""

    parts = module_name.split(".")
    candidate = import_root.joinpath(*parts).with_suffix(".py")
    if candidate.is_file():
        return candidate
    package = import_root.joinpath(*parts) / "__init__.py"
    if package.is_file():
        return package
    return None


def code_identity(entrypoint: str, import_root: Path) -> CodeIdentity | None:
    """Digest the code behind an entrypoint.

    Covers the module file; when the module lives in a package, the digest
    covers every ``.py`` file of that package recursively, so sibling helper
    modules are included. Paths in the identity stay relative to the import
    root — host directory structure never leaks into result files (§6.2).
    """

    root = import_root.resolve()
    module_name = entrypoint.partition(":")[0]
    path = module_file(module_name, root)
    if path is None:
        return None
    files = sorted(path.parent.rglob("*.py")) if (path.parent / "__init__.py").is_file() else [path]
    digest = hashlib.sha256()
    for file in files:
        relative = file.resolve().relative_to(root).as_posix()
        digest.update(relative.encode("utf-8"))
        digest.update(b"\0")
        digest.update(file.read_bytes())
        digest.update(b"\0")
    return CodeIdentity(
        path=path.resolve().relative_to(root).as_posix(),
        sha256=digest.hexdigest(),
    )
