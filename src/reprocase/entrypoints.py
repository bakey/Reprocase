"""Import isolation for trusted local code (agents, actors, tool providers).

Two different problems need two scopes:

- **Load-time isolation** is enough for validation: the module must import
  cleanly from its own root without colliding with a same-named module from
  another root.
- **Execution-time isolation** is required for actually running the code:
  agent/provider functions may import lazily inside their bodies
  (``from .helper import x``, ``importlib.import_module("agents.other")``),
  and by then the load-time context is long gone. Relative imports resolve
  through ``sys.modules['<package>']`` and absolute self-imports through
  ``sys.path`` — both must point at the case's own root for the whole
  load + provider-creation + execution span.

``ImportIsolation`` is a reentrant context manager covering that span: on
entry it shadows (saves and evicts) every cached module whose top-level
package exists as an importable in the root, and puts the root on
``sys.path``; on exit it evicts the modules imported during the span,
restores the shadowed ones, and removes the path entry. ``load_callable``
detects an already-active isolation for the same root and then imports
plainly inside it; standalone callers (validation) get a short-lived
isolation covering just the import.

The mechanism mutates process-global import state, so runs of cases sharing
top-level package names must execute sequentially in one process (the CLI
runs one case at a time).
"""

from __future__ import annotations

import hashlib
import importlib
import sys
from collections.abc import Callable
from pathlib import Path
from types import ModuleType
from typing import Any, cast

from reprocase.errors import CaseValidationError, FailureCode
from reprocase.results.models import CodeIdentity

_ACTIVE: list[ImportIsolation] = []


class ImportIsolation:
    """Reentrant import context spanning load and execution of case code."""

    def __init__(self, import_root: Path) -> None:
        self.root = import_root.resolve()
        self._tops: set[str] = set()
        self._shadowed: dict[str, ModuleType] = {}

    def local_top_levels(self) -> set[str]:
        tops: set[str] = set()
        if self.root.is_dir():
            for entry in self.root.iterdir():
                if entry.suffix == ".py":
                    tops.add(entry.stem)
                elif entry.is_dir() and (entry / "__init__.py").is_file():
                    tops.add(entry.name)
        return tops

    def __enter__(self) -> ImportIsolation:
        self._tops = self.local_top_levels()
        for name in [name for name in sys.modules if name.split(".", 1)[0] in self._tops]:
            self._shadowed[name] = sys.modules.pop(name)
        sys.path.insert(0, str(self.root))
        _ACTIVE.append(self)
        return self

    def __exit__(self, *exc_info: object) -> None:
        # everything still cached under the local tops was imported during
        # this span: evict it so the next case starts from a clean slate
        for name in [name for name in sys.modules if name.split(".", 1)[0] in self._tops]:
            del sys.modules[name]
        sys.modules.update(self._shadowed)
        if str(self.root) in sys.path:
            sys.path.remove(str(self.root))
        if _ACTIVE and _ACTIVE[-1] is self:
            _ACTIVE.pop()
        elif self in _ACTIVE:
            _ACTIVE.remove(self)


def _active_for(root: Path) -> bool:
    resolved = root.resolve()
    return any(isolation.root == resolved for isolation in _ACTIVE)


def _check_module_origin(module: ModuleType, name: str, root: Path) -> None:
    """Reject modules that did not come from inside ``root``.

    Without this, a typo'd or missing entrypoint silently falls back to
    whatever the host Python environment provides (standard library,
    site-packages) — the eval case would no longer be self-contained.
    """

    file = module.__file__
    if file is None:
        raise CaseValidationError(
            f"Entrypoint module {name!r} has no file inside the import root "
            f"(namespace packages, builtins, and frozen modules are not allowed); "
            f"check the entrypoint for typos or missing files",
            code=FailureCode.CASE_ENTRYPOINT_INVALID,
            details={"module": name, "import_root": str(root)},
        )
    origin = Path(file).resolve()
    if not origin.is_relative_to(root):
        raise CaseValidationError(
            f"Entrypoint module {name!r} resolved to {origin}, which is outside the "
            f"import root {root}; falling back to globally installed modules is not "
            f"allowed — check the entrypoint for typos or missing files",
            code=FailureCode.CASE_ENTRYPOINT_INVALID,
            details={"module": name, "origin": str(origin), "import_root": str(root)},
        )


def _import_and_resolve(entrypoint: str, import_root: Path) -> Callable[..., Any]:
    module_name, separator, function_name = entrypoint.partition(":")
    if not separator or not module_name or not function_name:
        raise CaseValidationError(
            f"Invalid entrypoint {entrypoint!r}; expected 'module:function'",
            code=FailureCode.CASE_ENTRYPOINT_INVALID,
            details={"entrypoint": entrypoint},
        )
    root_path = import_root.resolve()
    root = str(root_path)
    inserted = False
    if root not in sys.path:
        sys.path.insert(0, root)
        inserted = True
    try:
        parts = module_name.split(".")
        module: ModuleType | None = None
        for level in range(1, len(parts) + 1):
            # import level by level so a globally-resolved parent package is
            # caught even when the leaf module exists locally (or vice versa)
            name = ".".join(parts[:level])
            try:
                module = importlib.import_module(name)
            except ImportError as exc:
                raise CaseValidationError(
                    f"Cannot import module {name!r} from {root}: {exc}",
                    code=FailureCode.CASE_ENTRYPOINT_INVALID,
                    details={"entrypoint": entrypoint, "import_root": root},
                ) from exc
            _check_module_origin(module, name, root_path)
    finally:
        if inserted:
            sys.path.remove(root)
    assert module is not None
    target = getattr(module, function_name, None)
    if not callable(target):
        raise CaseValidationError(
            f"Entrypoint {entrypoint!r} is not callable",
            code=FailureCode.CASE_ENTRYPOINT_INVALID,
            details={"entrypoint": entrypoint},
        )
    return cast(Callable[..., Any], target)


def load_callable(entrypoint: str, import_root: Path) -> Callable[..., Any]:
    """Load ``module:function`` from ``import_root``.

    Inside an active ``ImportIsolation`` for the same root this imports
    plainly (modules stay cached until the span ends, so execution-time lazy
    imports keep working). Standalone callers get a short-lived isolation
    covering only the import itself.
    """

    if _active_for(import_root):
        return _import_and_resolve(entrypoint, import_root)
    with ImportIsolation(import_root):
        return _import_and_resolve(entrypoint, import_root)


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
