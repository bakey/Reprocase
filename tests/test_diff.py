from __future__ import annotations

from reprocase.recording.diff import SCHEMA_VERSION, compute_diff, diff_document


def test_scalar_replace_carries_from_value() -> None:
    ops = compute_diff({"a": 1}, {"a": 2})
    assert ops == [{"op": "replace", "path": "/a", "from_value": 1, "value": 2}]


def test_add_and_remove_keys_sorted() -> None:
    ops = compute_diff({"b": 1, "a": 2}, {"a": 2, "c": 3})
    assert ops == [
        {"op": "remove", "path": "/b"},
        {"op": "add", "path": "/c", "value": 3},
    ]


def test_nested_and_list_changes() -> None:
    initial = {"orders": {"ORD-1": {"status": "paid"}}, "log": [1, 2]}
    final = {"orders": {"ORD-1": {"status": "refunded"}}, "log": [1, 3, 4]}
    ops = compute_diff(initial, final)
    assert {
        "op": "replace",
        "path": "/orders/ORD-1/status",
        "from_value": "paid",
        "value": "refunded",
    } in ops
    assert {"op": "replace", "path": "/log/1", "from_value": 2, "value": 3} in ops
    assert {"op": "add", "path": "/log/2", "value": 4} in ops


def test_list_removal() -> None:
    ops = compute_diff({"l": [1, 2, 3]}, {"l": [1]})
    assert {"op": "remove", "path": "/l/1"} in ops
    assert {"op": "remove", "path": "/l/2"} in ops


def test_pointer_escaping() -> None:
    ops = compute_diff({"a/b": {"~x": 1}}, {"a/b": {"~x": 2}})
    assert ops[0]["path"] == "/a~1b/~0x"


def test_no_changes_produces_empty_ops() -> None:
    assert compute_diff({"a": [1, {"b": 2}]}, {"a": [1, {"b": 2}]}) == []


def test_document_carries_schema_version() -> None:
    document = diff_document({"a": 1}, {"a": 2})
    assert document["schema_version"] == SCHEMA_VERSION
    assert len(document["ops"]) == 1
