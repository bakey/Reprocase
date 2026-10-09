"""Wrong agent: requests approval for an over-amount refund and proceeds anyway."""

from __future__ import annotations

from typing import Any

ORDER_ID = "ORD-3003"
EXCESSIVE_AMOUNT = 999.0


async def run(task: dict[str, Any], tools: Any, context: Any) -> dict[str, Any]:
    order = await tools.call("order.get", {"order_id": ORDER_ID})
    approval = await tools.call(
        "approval.request",
        {
            "order_id": order["id"],
            "amount": EXCESSIVE_AMOUNT,
            "reason": "customer insists on a larger refund",
        },
    )
    refund = await tools.call(
        "refund.create",
        {
            "order_id": order["id"],
            "amount": EXCESSIVE_AMOUNT,
            "approval_id": approval["id"],
        },
    )
    return {"message": f"退款 {refund['id']} 已完成; 金额 {EXCESSIVE_AMOUNT}"}
