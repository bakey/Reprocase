"""Wrong actor: refunds correctly but never notifies the customer."""

from __future__ import annotations

from typing import Any

from agents.support_api import get, post

ORDER_ID = "ORD-1001"


def run(context: Any) -> dict[str, Any]:
    base_url = context.base_url
    order = get(base_url, f"/orders/{ORDER_ID}")
    refund = post(
        base_url,
        "/refunds",
        {"order_id": ORDER_ID, "amount": order["refundable_amount"]},
    )
    return {"message": f"refund {refund['id']} completed", "refund_id": refund["id"]}
