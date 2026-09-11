from __future__ import annotations

import pytest

from reprocase.case.models import CanonicalizationSpec
from reprocase.environments.state import canonicalize_state, parse_state_document
from reprocase.errors import EnvironmentDriverError, FailureCode

RAW = """{
  "metadata": {"generated_at": "2026-09-12T00:00:00Z", "schema_version": "v1"},
  "runtime": {"request_ids": ["req-0001"]},
  "emails": [
    {"id": "EM-2", "to": "b"},
    {"id": "EM-1", "to": "a"}
  ],
  "audit_log": [
    {"sequence": 2, "event": "b"},
    {"sequence": 1, "event": "a"}
  ]
}"""

SPEC = CanonicalizationSpec(
    remove_paths=("/metadata/generated_at", "/runtime"),
    sort_arrays_by={"/emails": "id", "/audit_log": "sequence"},
)


def test_canonicalization_removes_sorts_and_stabilizes_digest() -> None:
    captured = canonicalize_state(RAW, SPEC)

    assert "generated_at" not in captured.state["metadata"]
    assert "runtime" not in captured.state
    assert [email["id"] for email in captured.state["emails"]] == ["EM-1", "EM-2"]
    assert [entry["sequence"] for entry in captured.state["audit_log"]] == [1, 2]
    assert captured.content_digest.startswith("sha256:")
    assert captured.canonicalization_digest.startswith("sha256:")

    reordered = RAW.replace('"sequence": 2, "event": "b"', '"sequence": 2, "event": "b"')
    assert canonicalize_state(reordered, SPEC).content_digest == captured.content_digest


def test_remove_paths_are_tolerant_of_missing_keys() -> None:
    captured = canonicalize_state('{"a": 1}', CanonicalizationSpec(remove_paths=("/missing/x",)))
    assert captured.state == {"a": 1}


def test_invalid_json_is_state_invalid() -> None:
    with pytest.raises(EnvironmentDriverError) as excinfo:
        parse_state_document("not json {")
    assert excinfo.value.code == FailureCode.ENV_STATE_INVALID


def test_non_object_document_is_state_invalid() -> None:
    with pytest.raises(EnvironmentDriverError) as excinfo:
        parse_state_document("[1, 2]")
    assert excinfo.value.code == FailureCode.ENV_STATE_INVALID


def test_sort_target_must_exist_and_be_array_of_objects() -> None:
    spec = CanonicalizationSpec(sort_arrays_by={"/missing": "id"})
    with pytest.raises(EnvironmentDriverError) as excinfo:
        canonicalize_state('{"a": []}', spec)
    assert excinfo.value.code == FailureCode.ENV_STATE_INVALID

    bad = CanonicalizationSpec(sort_arrays_by={"/a": "id"})
    with pytest.raises(EnvironmentDriverError):
        canonicalize_state('{"a": [1, 2]}', bad)


def test_pointer_escaping() -> None:
    captured = canonicalize_state(
        '{"a/b": {"~key": 1, "keep": 2}}',
        CanonicalizationSpec(remove_paths=("/a~1b/~0key",)),
    )
    assert captured.state == {"a/b": {"keep": 2}}


def test_digest_ignores_generated_fields_across_documents() -> None:
    first = canonicalize_state(RAW, SPEC)
    second = canonicalize_state(
        RAW.replace("2026-09-12T00:00:00Z", "2027-01-01T00:00:00Z").replace("req-0001", "req-9999"),
        SPEC,
    )
    assert first.content_digest == second.content_digest
