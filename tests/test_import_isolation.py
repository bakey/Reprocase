"""Regression tests for execution-time import isolation (review round 2)."""

from __future__ import annotations

import importlib
import sys
from pathlib import Path

from reprocase.entrypoints import ImportIsolation, load_callable

PACKAGE_INIT = ""
HELPER_A = "VALUE = 'helper-a'\n"
HELPER_B = "VALUE = 'helper-b'\n"
OTHER_A = "OTHER = 'other-a'\n"
OTHER_B = "OTHER = 'other-b'\n"

# function-body relative import: only works when the execution span is covered
MAIN_LAZY_RELATIVE = """\
def run():
    from .helper import VALUE

    return VALUE
"""

# function-body absolute self-import plus dynamic import_module
MAIN_LAZY_DYNAMIC = """\
import importlib


def run():
    from agents.helper import VALUE

    other = importlib.import_module("agents.other")
    return VALUE, other.OTHER
"""


def _make_root(base: Path, name: str, helper: str, other: str, main: str) -> Path:
    package = (base / name) / "agents"
    package.mkdir(parents=True)
    (package / "__init__.py").write_text(PACKAGE_INIT, encoding="utf-8")
    (package / "helper.py").write_text(helper, encoding="utf-8")
    (package / "other.py").write_text(other, encoding="utf-8")
    (package / "main.py").write_text(main, encoding="utf-8")
    return base / name


def test_function_body_relative_import_executes(tmp_path: Path) -> None:
    root = _make_root(tmp_path, "a", HELPER_A, OTHER_A, MAIN_LAZY_RELATIVE)
    with ImportIsolation(root):
        run = load_callable("agents.main:run", root)
        assert run() == "helper-a"


def test_two_cases_same_package_execute_sequentially(tmp_path: Path) -> None:
    root_a = _make_root(tmp_path, "a", HELPER_A, OTHER_A, MAIN_LAZY_RELATIVE)
    root_b = _make_root(tmp_path, "b", HELPER_B, OTHER_B, MAIN_LAZY_RELATIVE)

    with ImportIsolation(root_a):
        assert load_callable("agents.main:run", root_a)() == "helper-a"
    # the second case must not hit any residue from the first
    with ImportIsolation(root_b):
        assert load_callable("agents.main:run", root_b)() == "helper-b"
    with ImportIsolation(root_a):
        assert load_callable("agents.main:run", root_a)() == "helper-a"

    assert "agents" not in sys.modules


def test_dynamic_import_during_execution_uses_own_root(tmp_path: Path) -> None:
    root_a = _make_root(tmp_path, "a", HELPER_A, OTHER_A, MAIN_LAZY_DYNAMIC)
    root_b = _make_root(tmp_path, "b", HELPER_B, OTHER_B, MAIN_LAZY_DYNAMIC)

    with ImportIsolation(root_a):
        assert load_callable("agents.main:run", root_a)() == ("helper-a", "other-a")
    with ImportIsolation(root_b):
        assert load_callable("agents.main:run", root_b)() == ("helper-b", "other-b")


def test_host_shadowed_package_is_restored_and_never_leaks(tmp_path: Path) -> None:
    """Dangerous variant: a foreign `agents` package already in sys.modules.

    Execution inside the isolation must bind the case's own helper, and the
    foreign package must come back byte-identical afterwards.
    """

    host_root = _make_root(tmp_path, "host", "VALUE = 'host-helper'\n", OTHER_A, MAIN_LAZY_RELATIVE)
    sys.path.insert(0, str(host_root))
    try:
        foreign = importlib.import_module("agents")
        importlib.import_module("agents.helper")
    finally:
        sys.path.remove(str(host_root))
    assert sys.modules["agents"] is foreign

    try:
        case_root = _make_root(tmp_path, "case", HELPER_A, OTHER_A, MAIN_LAZY_DYNAMIC)
        with ImportIsolation(case_root):
            # the foreign package is shadowed for the whole span
            assert sys.modules.get("agents") is not foreign
            assert load_callable("agents.main:run", case_root)() == ("helper-a", "other-a")

        assert sys.modules["agents"] is foreign
        assert sys.modules["agents.helper"].VALUE == "host-helper"
    finally:
        for name in [name for name in sys.modules if name.split(".", 1)[0] == "agents"]:
            del sys.modules[name]


def test_standalone_load_keeps_validate_behavior(tmp_path: Path) -> None:
    root = _make_root(tmp_path, "a", HELPER_A, OTHER_A, MAIN_LAZY_RELATIVE)
    run = load_callable("agents.main:run", root)  # load-only isolation
    assert callable(run)
    assert "agents" not in sys.modules  # no residue for the next case


def test_isolation_is_reentrant(tmp_path: Path) -> None:
    root = _make_root(tmp_path, "a", HELPER_A, OTHER_A, MAIN_LAZY_RELATIVE)
    with ImportIsolation(root):
        with ImportIsolation(root):
            run = load_callable("agents.main:run", root)
            assert run() == "helper-a"
        # inner exit restored its own (empty) shadow set; outer span still active
        run_again = load_callable("agents.main:run", root)
        assert run_again() == "helper-a"
    assert "agents" not in sys.modules
