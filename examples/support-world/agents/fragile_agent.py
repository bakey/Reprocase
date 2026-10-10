"""Wrong agent: lets the report tool timeout propagate and never notifies."""

from __future__ import annotations

from typing import Any

ORDER_ID = "ORD-2002"


async def run(task: dict[str, Any], tools: Any, context: Any) -> dict[str, Any]:
    order = await tools.call("order.get", {"order_id": ORDER_ID})
    await tools.call("report.generate", {"order_id": order["id"]})
    return {"message": "报告已生成"}
