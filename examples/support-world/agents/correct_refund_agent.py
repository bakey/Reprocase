"""Correct Python agent: refund ORD-1001 through the Tool Broker and notify."""

from __future__ import annotations

from typing import Any


async def run(task: dict[str, Any], tools: Any, context: Any) -> dict[str, Any]:
    order = await tools.call("order.get", {"order_id": "ORD-1001"})
    customer = await tools.call("customer.get", {"customer_id": order["customer_id"]})
    refund = await tools.call(
        "refund.create",
        {"order_id": order["id"], "amount": order["refundable_amount"]},
    )
    await tools.call(
        "email.send",
        {"customer_id": customer["id"], "template": "refund_complete"},
    )
    return {"message": f"退款 {refund['id']} 已完成"}
