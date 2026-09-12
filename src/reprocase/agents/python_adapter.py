"""Python agent adapter: loads ``module:function`` async agents (§8.1)."""

from __future__ import annotations

import inspect
import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from reprocase.agents.protocol import AgentContext, AgentResult
from reprocase.entrypoints import load_callable
from reprocase.errors import (
    CaseValidationError,
    FailureAttribution,
    FailureCode,
    ReprocaseError,
)


class PythonAgentAdapter:
    def __init__(self, entrypoint: str, import_root: Path) -> None:
        self.entrypoint = entrypoint
        self.function = load_callable(entrypoint, import_root)
        if not inspect.iscoroutinefunction(self.function):
            raise CaseValidationError(
                f"Python agent {entrypoint!r} must be an async function",
                code=FailureCode.CASE_ENTRYPOINT_INVALID,
                details={"entrypoint": entrypoint},
            )

    async def run(
        self,
        task: Mapping[str, Any],
        tools: Any,
        context: AgentContext,
    ) -> AgentResult:
        raw = await self.function(task, tools, context)
        result = raw if isinstance(raw, AgentResult) else AgentResult(output=raw)
        try:
            json.dumps(result.output)
        except (TypeError, ValueError) as exc:
            raise ReprocaseError(
                f"Agent {self.entrypoint!r} returned a non-JSON-serializable output",
                code=FailureCode.AGENT_OUTPUT_INVALID,
                attribution=FailureAttribution.AGENT_FAILURE,
            ) from exc
        return result
