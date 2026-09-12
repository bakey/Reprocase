"""Wrong actor: refunds far above the refundable amount (policy violation)."""

from __future__ import annotations

from typing import Any

from agents.support_api import get, post

ORDER_ID = "ORD-1001"
EXCESSIVE_AMOUNT = 999.0


def run(context: Any) -> dict[str, Any]:
    base_url = context.base_url
    order = get(base_url, f"/orders/{ORDER_ID}")
    refund = post(base_url, "/refunds", {"order_id": ORDER_ID, "amount": EXCESSIVE_AMOUNT})
    post(
        base_url,
        "/emails",
        {"customer_id": order["customer_id"], "template": "refund_complete"},
    )
    return {
        "message": f"refund {refund['id']} completed for {EXCESSIVE_AMOUNT}",
        "refund_id": refund["id"],
    }
