"""Agent adapter contracts (design section 8.1)."""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Protocol

from reprocase.results.models import Usage

# Agents may report progress or model usage; the runner forwards these as
# agent.message / agent.model.usage events.
EmitFn = Callable[[dict[str, Any]], Awaitable[None]]


@dataclass(frozen=True)
class AgentContext:
    run_id: str
    seed: int
    fixed_clock: datetime | None
    tool_names: tuple[str, ...]
    emit: EmitFn


@dataclass(frozen=True)
class AgentResult:
    output: Any
    summary: str | None = None
    usage: Usage | None = None


class AgentAdapter(Protocol):
    async def run(
        self,
        task: Mapping[str, Any],
        tools: Any,
        context: AgentContext,
    ) -> AgentResult: ...
