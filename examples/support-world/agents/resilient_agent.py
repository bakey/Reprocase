"""Correct agent: survives a tool timeout on report generation with a safe fallback.

The report dependency stalls longer than the case's per-tool timeout. The
broker turns that into a structured ``TOOL_TIMEOUT`` error handed back to the
agent; handling it and notifying the customer is the correct behaviour, while
the slow operation itself is left to finish in the background without further
side effects.
"""

from __future__ import annotations

from typing import Any

ORDER_ID = "ORD-2002"


async def run(task: dict[str, Any], tools: Any, context: Any) -> dict[str, Any]:
    order = await tools.call("order.get", {"order_id": ORDER_ID})
    try:
        await tools.call("report.generate", {"order_id": order["id"]})
    except Exception as exc:  # structured ToolCallError from the broker
        if getattr(exc, "code", None) != "TOOL_TIMEOUT":
            raise
        await tools.call(
            "email.send",
            {"customer_id": order["customer_id"], "template": "report_delayed"},
        )
        return {"message": "报告生成超时; 已通知客户将延迟送达"}
    return {"message": "报告已生成"}
