"""Static audit checks for the M9 task pack (no Docker).

Every task case must carry: an audit note with the required human-review
fields, a wrong-agent registry, a declared fixture that exists, a bystander
invariant, and reference/wrong entrypoints that really exist in their modules.
"""

from __future__ import annotations

import importlib
import inspect
from pathlib import Path
from typing import Any

import pytest
import yaml

from reprocase.entrypoints import ImportIsolation

ROOT = Path(__file__).resolve().parents[1]
ACME = ROOT / "examples" / "acme-world"
CASES = sorted(ACME.glob("task-*.yaml"))
NOTES_DIR = ACME / "audit"

REQUIRED_NOTE_FIELDS = (
    ("case", str),
    ("category", str),
    ("solvability", dict),
    ("ambiguity_review", dict),
    ("answer_separation", dict),
    ("wrong_agents", list),
    ("versions", dict),
)


def _load(path: Path) -> dict[str, Any]:
    document = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert isinstance(document, dict)
    return document


@pytest.fixture(scope="module")
def acme_imports() -> Any:
    with ImportIsolation(ACME):
        yield ACME


@pytest.mark.parametrize("case_path", CASES, ids=lambda path: path.name)
def test_case_has_complete_audit_note(case_path: Path) -> None:
    note_path = NOTES_DIR / f"{case_path.stem}.notes.yaml"
    assert note_path.exists(), f"missing audit note: {note_path.name}"
    note = _load(note_path)
    case = _load(case_path)
    for field, kind in REQUIRED_NOTE_FIELDS:
        assert field in note, f"{note_path.name}: missing field {field!r}"
        assert isinstance(note[field], kind), f"{note_path.name}: field {field!r} has wrong type"
    assert note["case"] == case["metadata"]["id"], "note must reference the case id"
    assert note["solvability"].get("verdict") == "solvable"
    assert note["ambiguity_review"].get("reviewed") is True
    assert note["answer_separation"].get("checked") is True
    wrong = note["wrong_agents"]
    assert wrong, "at least one wrong agent is mandatory (distinct failure causes)"
    assert all("entrypoint" in entry and "expected_failure" in entry for entry in wrong)
    # the failure mechanism must be pinned: which graders the wrong agent trips,
    # so the audit matrix can assert it (executable constraint, not prose)
    for entry in wrong:
        declared = entry.get("expected_failed_graders")
        assert (
            isinstance(declared, list)
            and declared
            and all(isinstance(grader, str) for grader in declared)
        ), f"{case_path.name}: {entry.get('entrypoint')} needs expected_failed_graders"


@pytest.mark.parametrize("case_path", CASES, ids=lambda path: path.name)
def test_case_declares_bystander_invariant_and_existing_fixture(case_path: Path) -> None:
    case = _load(case_path)
    graders = case["graders"]
    invariants = [g for g in graders if g["type"] == "invariant"]
    assert invariants, "every task case must declare at least one invariant grader"
    bystander_scopes = {rule["scope"] for g in invariants for rule in g["rules"]}
    assert bystander_scopes, "invariant graders must declare scopes"
    fixture = case["environment"].get("fixture", "/fixtures/acme.json")
    assert (ACME / "fixtures" / Path(fixture).name).exists(), f"fixture missing: {fixture}"


@pytest.mark.parametrize("case_path", CASES, ids=lambda path: path.name)
def test_reference_and_wrong_entrypoints_exist(case_path: Path, acme_imports: Any) -> None:
    case = _load(case_path)
    entrypoint = case["agent"]["entrypoint"]
    wrong = _load(NOTES_DIR / f"{case_path.stem}.notes.yaml")["wrong_agents"]
    entrypoints = [entrypoint] + [entry["entrypoint"] for entry in wrong]
    for spec in entrypoints:
        module_name, _, function_name = spec.partition(":")
        module = importlib.import_module(module_name)
        function = getattr(module, function_name, None)
        assert function is not None, f"entrypoint missing: {spec}"
        assert inspect.iscoroutinefunction(function), f"entrypoint not async: {spec}"


def test_case_and_note_fixture_references_agree() -> None:
    for case_path in CASES:
        case = _load(case_path)
        note = _load(NOTES_DIR / f"{case_path.stem}.notes.yaml")
        declared = case["environment"].get("fixture", "/fixtures/acme.json")
        assert note["versions"]["fixture"] == declared, case_path.name
