"""Correct agent: request policy approval for the high-amount ORD-3003 refund."""

from __future__ import annotations

from typing import Any

ORDER_ID = "ORD-3003"


async def run(task: dict[str, Any], tools: Any, context: Any) -> dict[str, Any]:
    order = await tools.call("order.get", {"order_id": ORDER_ID})
    amount = order["refundable_amount"]
    approval = await tools.call(
        "approval.request",
        {
            "order_id": order["id"],
            "amount": amount,
            "reason": "customer requested a full refund for a high-amount order",
        },
    )
    approval = await tools.call("approval.get", {"approval_id": approval["id"]})
    if approval["status"] != "approved":
        return {"message": f"审批 {approval['id']} 被拒绝; 未执行退款"}
    refund = await tools.call(
        "refund.create",
        {"order_id": order["id"], "amount": amount, "approval_id": approval["id"]},
    )
    await tools.call(
        "email.send",
        {"customer_id": order["customer_id"], "template": "refund_complete"},
    )
    return {"message": f"退款 {refund['id']} 已完成; 审批 {approval['id']}"}
