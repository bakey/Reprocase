"""Build-time validation of provider-supplied tools (before any agent runs).

Provider defects and case misconfiguration are CASE_ERRORs that make the run
INVALID; they must never surface as agent business failures.
"""

from __future__ import annotations

import inspect
import re
from collections.abc import Mapping
from typing import Any

import jsonschema

from reprocase.errors import CaseValidationError, FailureCode
from reprocase.tools.protocol import Tool

TOOL_NAME_PATTERN = re.compile(r"^[a-z][a-z0-9_]*\.[a-z][a-z0-9_]*$")


def _invalid(message: str, tool: Any) -> CaseValidationError:
    return CaseValidationError(
        message,
        code=FailureCode.CASE_SCHEMA_INVALID,
        details={"tool": repr(tool)},
    )


def validate_tools(tools: list[Tool], allow: tuple[str, ...]) -> None:
    for tool in tools:
        name = getattr(tool, "name", None)
        if not isinstance(name, str) or not TOOL_NAME_PATTERN.fullmatch(name):
            raise _invalid(f"Provider returned a tool with an invalid name: {name!r}", tool)
        if not isinstance(getattr(tool, "description", None), str):
            raise _invalid(f"Tool {name!r} has no string description", tool)
        schema_value = getattr(tool, "input_schema", None)
        if not isinstance(schema_value, Mapping):
            raise _invalid(f"Tool {name!r} has a non-mapping input schema", tool)
        invoke = getattr(tool, "invoke", None)
        if not callable(invoke):
            raise _invalid(f"Tool {name!r} has no callable invoke", tool)
        if not inspect.iscoroutinefunction(invoke):
            raise _invalid(
                f"Tool {name!r} must define 'async def invoke' (broker awaits every invocation)",
                tool,
            )
        schema: dict[str, Any] = dict(schema_value)
        try:
            jsonschema.validators.validator_for(schema).check_schema(schema)
        except jsonschema.SchemaError as exc:
            raise _invalid(
                f"Tool {name!r} has an invalid input schema: {exc.message}", tool
            ) from exc
    known = {tool.name for tool in tools}
    missing = [name for name in allow if name not in known]
    if missing:
        raise CaseValidationError(
            f"tools.allow references tools the provider did not supply: {missing}",
            code=FailureCode.CASE_SCHEMA_INVALID,
            details={"missing": missing, "available": sorted(known)},
        )
