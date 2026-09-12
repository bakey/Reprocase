from __future__ import annotations

from typing import Any

import pytest

from reprocase.errors import CaseValidationError, FailureAttribution, FailureCode
from reprocase.tools.validation import validate_tools


class StubTool:
    def __init__(self, name: str = "order.get", schema: Any = None) -> None:
        self.name = name
        self.description = "stub"
        self.input_schema = schema if schema is not None else {"type": "object"}

    async def invoke(self, arguments: Any) -> Any:
        return None


def test_valid_tools_pass() -> None:
    validate_tools(
        [StubTool(), StubTool("refund.create", {"type": "object"})],
        ("order.get", "refund.create"),
    )


def test_invalid_schema_is_case_error() -> None:
    with pytest.raises(CaseValidationError) as excinfo:
        validate_tools([StubTool(schema={"type": "nonsense"})], ("order.get",))
    assert excinfo.value.code == FailureCode.CASE_SCHEMA_INVALID
    assert excinfo.value.attribution == FailureAttribution.CASE_ERROR


def test_non_mapping_schema_is_case_error() -> None:
    with pytest.raises(CaseValidationError):
        validate_tools([StubTool(schema="not-a-schema")], ("order.get",))


def test_invalid_tool_name_is_case_error() -> None:
    with pytest.raises(CaseValidationError) as excinfo:
        validate_tools([StubTool(name="OrderGet")], ("OrderGet",))
    assert excinfo.value.code == FailureCode.CASE_SCHEMA_INVALID


def test_allowlist_must_reference_supplied_tools() -> None:
    with pytest.raises(CaseValidationError) as excinfo:
        validate_tools([StubTool()], ("order.get", "ghost.tool"))
    assert "ghost.tool" in str(excinfo.value.details["missing"])
