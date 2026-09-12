"""Tool contracts (design section 8.2)."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any, Protocol

from reprocase.environments.protocol import EnvironmentHandle


class Tool(Protocol):
    name: str
    description: str
    input_schema: Mapping[str, Any]

    async def invoke(self, arguments: Mapping[str, Any]) -> Any: ...


# Trusted factory: builds tools bound to one running environment.
ToolProvider = Callable[[EnvironmentHandle], "list[Tool]"]
