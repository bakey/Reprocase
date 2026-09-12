"""Build-time validation of provider-supplied tools (before any agent runs).

Provider defects and case misconfiguration are CASE_ERRORs that make the run
INVALID; they must never surface as agent business failures.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any

import jsonschema

from reprocase.errors import CaseValidationError, FailureCode
from reprocase.tools.protocol import Tool

TOOL_NAME_PATTERN = re.compile(r"^[a-z][a-z0-9_]*\.[a-z][a-z0-9_]*$")


def validate_tools(tools: list[Tool], allow: tuple[str, ...]) -> None:
    for tool in tools:
        if not TOOL_NAME_PATTERN.fullmatch(tool.name):
            raise CaseValidationError(
                f"Provider returned a tool with an invalid name: {tool.name!r}",
                code=FailureCode.CASE_SCHEMA_INVALID,
                details={"tool": tool.name},
            )
        if not isinstance(tool.input_schema, Mapping):
            raise CaseValidationError(
                f"Tool {tool.name!r} has a non-mapping input schema",
                code=FailureCode.CASE_SCHEMA_INVALID,
                details={"tool": tool.name},
            )
        schema: dict[str, Any] = dict(tool.input_schema)
        try:
            jsonschema.validators.validator_for(schema).check_schema(schema)
        except jsonschema.SchemaError as exc:
            raise CaseValidationError(
                f"Tool {tool.name!r} has an invalid input schema: {exc.message}",
                code=FailureCode.CASE_SCHEMA_INVALID,
                details={"tool": tool.name},
            ) from exc
    known = {tool.name for tool in tools}
    missing = [name for name in allow if name not in known]
    if missing:
        raise CaseValidationError(
            f"tools.allow references tools the provider did not supply: {missing}",
            code=FailureCode.CASE_SCHEMA_INVALID,
            details={"missing": missing, "available": sorted(known)},
        )
