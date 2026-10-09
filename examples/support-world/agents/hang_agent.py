"""Timeout agent: never finishes within any reasonable wall-time budget.

Shared by all three M5 cases via ``--agent agents.hang_agent:run`` to exercise
the wall-time expiry path (AGENT_TIMEOUT attribution with preserved evidence).
"""

from __future__ import annotations

import asyncio
from typing import Any


async def run(task: dict[str, Any], tools: Any, context: Any) -> dict[str, Any]:
    await asyncio.sleep(300)
    return {"message": "unreachable"}
