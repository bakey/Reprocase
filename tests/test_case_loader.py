from __future__ import annotations

from pathlib import Path

import pytest

from reprocase.case.loader import load_case
from reprocase.errors import CaseValidationError, FailureCode

ROOT = Path(__file__).resolve().parents[1]


def test_loads_valid_case_and_reference() -> None:
    loaded = load_case(ROOT / "examples/fake/eval-pass.yaml")

    assert loaded.case.metadata.id == "fake.contract.pass"
    assert loaded.environment.driver == "fake"
    assert loaded.digest.startswith("sha256:")
    assert len(loaded.digest) == 71


def test_digest_is_stable() -> None:
    first = load_case(ROOT / "examples/fake/eval-pass.yaml")
    second = load_case(ROOT / "examples/fake/eval-pass.yaml")

    assert first.digest == second.digest


def test_rejects_unknown_fields_with_precise_location(tmp_path: Path) -> None:
    environment = tmp_path / "environment.yaml"
    environment.write_text(
        "api_version: reprocase.dev/environment/v0\ndriver: fake\nconfig: {}\n",
        encoding="utf-8",
    )
    case = tmp_path / "case.yaml"
    case.write_text(
        """\
api_version: reprocase.dev/v0
kind: EvalCase
metadata:
  id: fake.invalid.field
  version: 0.1.0
  title: Invalid
task:
  input: {}
agent:
  adapter: fake
environment:
  driver: fake
  definition: environment.yaml
graders:
  - id: one
    type: fake
    outcome: PASS
    explanation: pass
unknown_field: true
""",
        encoding="utf-8",
    )

    with pytest.raises(CaseValidationError) as captured:
        load_case(case)

    assert captured.value.code == FailureCode.CASE_SCHEMA_INVALID
    assert captured.value.details["errors"][0]["location"] == "unknown_field"


def test_rejects_reference_outside_case_root(tmp_path: Path) -> None:
    outside = tmp_path / "outside.yaml"
    outside.write_text(
        "api_version: reprocase.dev/environment/v0\ndriver: fake\nconfig: {}\n",
        encoding="utf-8",
    )
    case_root = tmp_path / "case"
    case_root.mkdir()
    case = case_root / "case.yaml"
    case.write_text(
        """\
api_version: reprocase.dev/v0
kind: EvalCase
metadata:
  id: fake.outside.reference
  version: 0.1.0
  title: Invalid reference
task:
  input: {}
agent:
  adapter: fake
environment:
  driver: fake
  definition: ../outside.yaml
graders:
  - id: one
    type: fake
    outcome: PASS
    explanation: pass
""",
        encoding="utf-8",
    )

    with pytest.raises(CaseValidationError) as captured:
        load_case(case)

    assert captured.value.code == FailureCode.CASE_REFERENCE_OUTSIDE_ROOT


def test_rejects_duplicate_grader_ids(tmp_path: Path) -> None:
    environment = tmp_path / "environment.yaml"
    environment.write_text(
        "api_version: reprocase.dev/environment/v0\ndriver: fake\nconfig: {}\n",
        encoding="utf-8",
    )
    case = tmp_path / "case.yaml"
    case.write_text(
        """\
api_version: reprocase.dev/v0
kind: EvalCase
metadata:
  id: fake.duplicate.graders
  version: 0.1.0
  title: Duplicates
task:
  input: {}
agent:
  adapter: fake
environment:
  driver: fake
  definition: environment.yaml
graders:
  - id: same
    type: fake
    explanation: one
  - id: same
    type: fake
    explanation: two
""",
        encoding="utf-8",
    )

    with pytest.raises(CaseValidationError, match="grader ids must be unique"):
        load_case(case)
