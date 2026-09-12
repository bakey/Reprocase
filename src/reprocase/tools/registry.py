"""Tool registry: name-indexed view over the tools of one environment."""

from __future__ import annotations

from reprocase.errors import FailureAttribution, FailureCode, ReprocaseError
from reprocase.tools.protocol import Tool


class ToolRegistry:
    def __init__(self, tools: list[Tool]) -> None:
        self._tools: dict[str, Tool] = {}
        for tool in tools:
            if tool.name in self._tools:
                raise ReprocaseError(
                    f"Duplicate tool name from provider: {tool.name}",
                    code=FailureCode.CASE_ENTRYPOINT_INVALID,
                    attribution=FailureAttribution.CASE_ERROR,
                    details={"tool": tool.name},
                )
            self._tools[tool.name] = tool

    @property
    def names(self) -> tuple[str, ...]:
        return tuple(sorted(self._tools))

    def get(self, name: str) -> Tool | None:
        return self._tools.get(name)
