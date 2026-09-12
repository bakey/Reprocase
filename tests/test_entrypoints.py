from __future__ import annotations

from pathlib import Path

from reprocase.entrypoints import code_identity, load_callable

MAIN_V1 = "def run():\n    return 'root-a'\n"
MAIN_V2 = "def run():\n    return 'root-b'\n"

PACKAGE_INIT = ""
PACKAGE_HELPER_V1 = "VALUE = 'helper-a'\n"
PACKAGE_HELPER_V2 = "VALUE = 'helper-b'\n"
PACKAGE_MAIN = "from agents.helper import VALUE\n\n\ndef run():\n    return VALUE\n"


def _root(base: Path, name: str) -> Path:
    root = base / name
    root.mkdir()
    return root


def test_same_module_name_from_two_roots_stays_distinct(tmp_path: Path) -> None:
    root_a = _root(tmp_path, "a")
    root_b = _root(tmp_path, "b")
    (root_a / "main.py").write_text(MAIN_V1, encoding="utf-8")
    (root_b / "main.py").write_text(MAIN_V2, encoding="utf-8")

    first_a = load_callable("main:run", root_a)
    first_b = load_callable("main:run", root_b)
    second_a = load_callable("main:run", root_a)

    assert first_a() == "root-a"
    assert first_b() == "root-b"
    assert second_a() == "root-a"
    assert first_a is not first_b


def test_package_modules_with_sibling_imports_stay_distinct(tmp_path: Path) -> None:
    for name, helper in (("a", PACKAGE_HELPER_V1), ("b", PACKAGE_HELPER_V2)):
        package = _root(tmp_path, name) / "agents"
        package.mkdir()
        (package / "__init__.py").write_text(PACKAGE_INIT, encoding="utf-8")
        (package / "helper.py").write_text(helper, encoding="utf-8")
        (package / "main.py").write_text(PACKAGE_MAIN, encoding="utf-8")

    run_a = load_callable("agents.main:run", tmp_path / "a")
    run_b = load_callable("agents.main:run", tmp_path / "b")
    run_a_again = load_callable("agents.main:run", tmp_path / "a")

    # sibling imports must resolve inside each run's own root
    assert run_a() == "helper-a"
    assert run_b() == "helper-b"
    assert run_a_again() == "helper-a"


def test_sys_modules_restored_after_load(tmp_path: Path) -> None:
    import sys

    root = _root(tmp_path, "a")
    (root / "main.py").write_text(MAIN_V1, encoding="utf-8")

    load_callable("main:run", root)
    assert "main" not in sys.modules  # no residue that could shadow other roots


def test_code_identity_tracks_package_changes(tmp_path: Path) -> None:
    package = _root(tmp_path, "a") / "agents"
    package.mkdir()
    (package / "__init__.py").write_text(PACKAGE_INIT, encoding="utf-8")
    (package / "helper.py").write_text(PACKAGE_HELPER_V1, encoding="utf-8")
    (package / "main.py").write_text(PACKAGE_MAIN, encoding="utf-8")

    identity = code_identity("agents.main:run", tmp_path / "a")
    assert identity is not None
    assert identity.path == "agents/main.py"
    assert not identity.path.startswith("/")
    assert len(identity.sha256) == 64

    # a sibling module change moves the digest even though main.py is untouched
    (package / "helper.py").write_text(PACKAGE_HELPER_V2, encoding="utf-8")
    changed = code_identity("agents.main:run", tmp_path / "a")
    assert changed is not None
    assert changed.sha256 != identity.sha256


def test_code_identity_single_module_and_missing(tmp_path: Path) -> None:
    root = _root(tmp_path, "a")
    (root / "main.py").write_text(MAIN_V1, encoding="utf-8")

    identity = code_identity("main:run", root)
    assert identity is not None
    assert identity.path == "main.py"
    assert code_identity("ghost:run", root) is None


def test_git_identity_reports_commit_and_dirty() -> None:
    from reprocase.vcs import git_identity

    repo = Path(__file__).resolve().parents[1]
    commit, dirty = git_identity(repo)
    assert commit is not None
    assert len(commit) == 40
    assert dirty is not None

    assert git_identity(Path("/nonexistent-path")) == (None, None)
