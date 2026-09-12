"""Wrong actor: refunds the wrong order (ORD-2002 instead of ORD-1001)."""

from __future__ import annotations

from typing import Any

from agents.support_api import get, post

ORDER_ID = "ORD-2002"


def run(context: Any) -> dict[str, Any]:
    base_url = context.base_url
    order = get(base_url, f"/orders/{ORDER_ID}")
    refund = post(
        base_url,
        "/refunds",
        {"order_id": ORDER_ID, "amount": order["refundable_amount"]},
    )
    post(
        base_url,
        "/emails",
        {"customer_id": order["customer_id"], "template": "refund_complete"},
    )
    return {"message": f"refunded {refund['id']} for the wrong order", "refund_id": refund["id"]}
