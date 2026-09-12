"""Entrypoint modules must be self-contained inside the case root (review round 3)."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from reprocase.entrypoints import ImportIsolation, load_callable
from reprocase.errors import CaseValidationError, FailureAttribution, FailureCode


def test_empty_root_cannot_fall_back_to_stdlib(tmp_path: Path) -> None:
    with pytest.raises(CaseValidationError) as excinfo:
        load_callable("asyncio:run", tmp_path)
    assert excinfo.value.code == FailureCode.CASE_ENTRYPOINT_INVALID
    assert excinfo.value.attribution == FailureAttribution.CASE_ERROR
    assert "outside the import root" in excinfo.value.message


def test_installed_dependency_name_is_not_a_valid_entrypoint(tmp_path: Path) -> None:
    with pytest.raises(CaseValidationError) as excinfo:
        load_callable("jsonschema:validate", tmp_path)
    assert excinfo.value.code == FailureCode.CASE_ENTRYPOINT_INVALID


def test_namespace_package_without_init_is_rejected(tmp_path: Path) -> None:
    package = tmp_path / "agents"
    package.mkdir()
    (package / "main.py").write_text("def run():\n    return 1\n", encoding="utf-8")
    with pytest.raises(CaseValidationError) as excinfo:
        load_callable("agents.main:run", tmp_path)
    assert excinfo.value.code == FailureCode.CASE_ENTRYPOINT_INVALID
    assert "no file inside the import root" in excinfo.value.message


def test_mixed_local_leaf_with_global_parent_is_rejected(tmp_path: Path) -> None:
    """Local `agents/main.py` but a globally-resolved `agents` package: reject.

    Every level of the dotted path must come from the root, not just the leaf.
    """

    host = tmp_path / "host"
    host_package = host / "agents"
    host_package.mkdir(parents=True)
    (host_package / "__init__.py").write_text("", encoding="utf-8")
    (host_package / "main.py").write_text("def run():\n    return 'host'\n", encoding="utf-8")

    case = tmp_path / "case"
    case_package = case / "agents"
    case_package.mkdir(parents=True)
    (case_package / "main.py").write_text("def run():\n    return 'case'\n", encoding="utf-8")
    # note: no __init__.py in the case package

    sys.path.insert(0, str(host))
    try:
        with pytest.raises(CaseValidationError) as excinfo:
            load_callable("agents.main:run", case)
        assert excinfo.value.code == FailureCode.CASE_ENTRYPOINT_INVALID
        assert excinfo.value.attribution == FailureAttribution.CASE_ERROR
    finally:
        sys.path.remove(str(host))
        for name in [name for name in sys.modules if name.split(".", 1)[0] == "agents"]:
            del sys.modules[name]


def test_origin_check_also_applies_inside_active_isolation(tmp_path: Path) -> None:
    with ImportIsolation(tmp_path), pytest.raises(CaseValidationError):
        load_callable("asyncio:run", tmp_path)


def test_normal_in_root_loads_still_work(tmp_path: Path) -> None:
    package = tmp_path / "agents"
    package.mkdir()
    (package / "__init__.py").write_text("", encoding="utf-8")
    (package / "helper.py").write_text("VALUE = 42\n", encoding="utf-8")
    (package / "main.py").write_text(
        "from .helper import VALUE\n\n\ndef run():\n    return VALUE\n",
        encoding="utf-8",
    )
    (tmp_path / "standalone.py").write_text("def run():\n    return 'top'\n", encoding="utf-8")

    assert load_callable("agents.main:run", tmp_path)() == 42
    assert load_callable("standalone:run", tmp_path)() == "top"


def test_container_grader_command_does_not_use_load_callable() -> None:
    """evals.grade_refund runs as in-container argv, never through the loader."""

    source = (
        Path(__file__).resolve().parents[1] / "src/reprocase/graders/test_command.py"
    ).read_text(encoding="utf-8")
    assert "load_callable" not in source
    assert "import_module" not in source
