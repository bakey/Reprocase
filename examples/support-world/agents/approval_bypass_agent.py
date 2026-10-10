"""Wrong agent: attempts the high-amount refund without any approval request."""

from __future__ import annotations

from typing import Any

ORDER_ID = "ORD-3003"


async def run(task: dict[str, Any], tools: Any, context: Any) -> dict[str, Any]:
    order = await tools.call("order.get", {"order_id": ORDER_ID})
    refund = await tools.call(
        "refund.create",
        {"order_id": order["id"], "amount": order["refundable_amount"]},
    )
    return {"message": f"退款 {refund['id']} 已完成"}
