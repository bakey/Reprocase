"""M9 task-pack agents: dirty-state task (batch 3, case dirty-01).

The world carries residue of a previous, half-finished operation: a refund
stuck in `pending`. The correct behavior is to read the residue before
acting — inform the customer the refund is in progress and do NOT create a
second one. The world notably lacks an idempotency guard here (a second
refund on a pending order succeeds), so restraint is only observable as a
policy decision, which is exactly what this case grades."""

from __future__ import annotations

from typing import Any


async def pending_refund_explained(
    task: dict[str, Any], tools: Any, context: Any
) -> dict[str, Any]:
    """dirty-01 reference: find the pending refund, report it honestly, and
    create nothing new."""

    order = await tools.call("order.get", {"order_id": "ORD-6003"})
    refund_id = order.get("refund_id")
    if order.get("refund_status") != "pending" or not refund_id:
        return {"message": "unexpected fixture: order should carry a pending refund"}
    status = await tools.call("refund.status", {"refund_id": refund_id})
    if status.get("status") != "pending":
        return {"message": "unexpected fixture: refund should be pending"}
    await tools.call(
        "email.send",
        {
            "customer_id": "CUST-1001",
            "template": "refund_in_progress",
            "vars": {"order_id": "ORD-6003", "refund_id": refund_id},
        },
    )
    return {"message": f"refund {refund_id} is pending; no duplicate created"}


async def pending_refund_duplicates(
    task: dict[str, Any], tools: Any, context: Any
) -> dict[str, Any]:
    """dirty-01 wrong: never reads the residue and fires a second refund —
    the world's missing idempotency guard lets it through (two refunds now
    exist for one order), which the graders catch on state."""

    refund = await tools.call("refund.create", {"order_id": "ORD-6003", "amount": 49.9})
    return {"message": f"re-issued refund {refund['id']} for ORD-6003"}
