from __future__ import annotations

from typing import Any, ClassVar

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


def _valid_tool() -> StubTool:
    return StubTool()


def test_missing_invoke_is_case_error() -> None:
    class NoInvoke:
        name = "order.get"
        description = "stub"
        input_schema: ClassVar[dict[str, Any]] = {"type": "object"}

    with pytest.raises(CaseValidationError) as excinfo:
        validate_tools([NoInvoke()], ("order.get",))  # type: ignore[list-item]
    assert excinfo.value.code == FailureCode.CASE_SCHEMA_INVALID
    assert excinfo.value.attribution == FailureAttribution.CASE_ERROR


def test_non_callable_invoke_is_case_error() -> None:
    tool = _valid_tool()
    tool.invoke = "not-callable"  # type: ignore[assignment]
    with pytest.raises(CaseValidationError):
        validate_tools([tool], ("order.get",))


def test_sync_invoke_is_case_error() -> None:
    class SyncTool(StubTool):
        def invoke(self, arguments: Any) -> Any:  # type: ignore[override]
            return {}

    with pytest.raises(CaseValidationError) as excinfo:
        validate_tools([SyncTool()], ("order.get",))
    assert "async def invoke" in excinfo.value.message


def test_non_string_name_is_case_error_not_typeerror() -> None:
    tool = _valid_tool()
    tool.name = 123  # type: ignore[assignment]
    with pytest.raises(CaseValidationError) as excinfo:
        validate_tools([tool], ("order.get",))
    assert excinfo.value.code == FailureCode.CASE_SCHEMA_INVALID


def test_missing_or_non_string_description_is_case_error() -> None:
    tool = _valid_tool()
    tool.description = None  # type: ignore[assignment]
    with pytest.raises(CaseValidationError):
        validate_tools([tool], ("order.get",))

    tool2 = _valid_tool()
    delattr(tool2, "description")
    with pytest.raises(CaseValidationError):
        validate_tools([tool2], ("order.get",))


def test_bound_async_method_passes() -> None:
    class BoundTool:
        name = "order.get"
        description = "bound"

        @property
        def input_schema(self) -> dict[str, Any]:
            return {"type": "object"}

        async def invoke(self, arguments: Any) -> Any:
            return {"ok": True}

    validate_tools([BoundTool()], ("order.get",))  # type: ignore[list-item]
