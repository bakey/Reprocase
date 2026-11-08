"""Correct smoke agent: quote, refund, and notify through the Tool Broker."""

from __future__ import annotations

from typing import Any


async def run(task: dict[str, Any], tools: Any, context: Any) -> dict[str, Any]:
    quote = await tools.call("refund.quote", {"order_id": "ORD-1001"})
    if not quote["customer_verified"] or not quote["within_window"]:
        return {"message": "policy blocks the refund", "quote": quote}
    refund = await tools.call(
        "refund.create",
        {"order_id": "ORD-1001", "amount": quote["refundable_amount"]},
    )
    await tools.call(
        "email.send",
        {"customer_id": "CUST-1001", "template": "refund_complete"},
    )
    return {"message": f"退款 {refund['id']} 已完成", "refund_id": refund["id"]}
