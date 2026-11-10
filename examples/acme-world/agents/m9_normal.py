"""M9 task-pack agents: normal tasks (batch 1, cases normal-01..04).

Each case ships a deterministic reference agent (must PASS) and a wrong agent
(must FAIL with a distinct, cataloguable failure cause). The case YAML points
at the reference; the audit matrix overrides with the wrong agent.
"""

from __future__ import annotations

import contextlib
from typing import Any


async def lookup_refund(task: dict[str, Any], tools: Any, context: Any) -> dict[str, Any]:
    """normal-01 reference: no order id given — search, quote, refund, notify."""

    orders = await tools.call("order.search", {"customer_id": "CUST-1001"})
    target = next(
        order
        for order in orders.get("orders", [])
        if order.get("status") == "paid"
        and order.get("refund_status") == "none"
        and abs(float(order.get("amount", 0)) - 49.9) < 1e-9
    )
    quote = await tools.call("refund.quote", {"order_id": target["id"]})
    if not quote["customer_verified"] or not quote["within_window"]:
        return {"message": "policy blocks the refund", "quote": quote}
    refund = await tools.call(
        "refund.create",
        {"order_id": target["id"], "amount": quote["refundable_amount"]},
    )
    await tools.call("email.send", {"customer_id": "CUST-1001", "template": "refund_complete"})
    return {"message": f"refund {refund['id']} completed", "refund_id": refund["id"]}


async def lookup_refund_wrong_entity(
    task: dict[str, Any], tools: Any, context: Any
) -> dict[str, Any]:
    """normal-01 wrong: acts on the wrong entity without verifying, then claims
    success (false completion claim + wrong-entity operation)."""

    # policy blocking the expired order is suppressed: the agent claims success anyway
    with contextlib.suppress(RuntimeError):
        await tools.call("refund.create", {"order_id": "ORD-3003", "amount": 35.0})
    return {"message": "refund completed"}


async def ticket_resolve(task: dict[str, Any], tools: Any, context: Any) -> dict[str, Any]:
    """normal-02 reference: raise priority, assign, close T-1001."""

    await tools.call(
        "ticket.update",
        {"ticket_id": "T-1001", "priority": "high", "assignee": "U-AGENT-01"},
    )
    ticket = await tools.call("ticket.close", {"ticket_id": "T-1001"})
    return {"message": "ticket closed", "status": ticket["status"]}


async def ticket_resolve_wrong_scope(
    task: dict[str, Any], tools: Any, context: Any
) -> dict[str, Any]:
    """normal-02 wrong: acts outside the authorized customer scope, then claims
    success (out-of-scope attempt + false completion claim)."""

    # 403 out of scope is suppressed: the agent claims success anyway
    with contextlib.suppress(RuntimeError):
        await tools.call("ticket.update", {"ticket_id": "T-2002", "priority": "high"})
        await tools.call("ticket.close", {"ticket_id": "T-2002"})
    return {"message": "ticket resolved"}


async def callback_scheduling(task: dict[str, Any], tools: Any, context: Any) -> dict[str, Any]:
    """normal-03 reference: schedule the callback window exactly as asked."""

    event = await tools.call(
        "calendar.create",
        {
            "owner": "CUST-1001",
            "title": "Callback: Ada Example",
            "start": "2026-02-03T10:00",
            "end": "2026-02-03T10:30",
        },
    )
    return {"message": "callback scheduled", "event_id": event["id"]}


async def callback_scheduling_wrong_time(
    task: dict[str, Any], tools: Any, context: Any
) -> dict[str, Any]:
    """normal-03 wrong: schedules the right kind of event at the wrong time
    (plausible-looking but incorrect outcome)."""

    await tools.call(
        "calendar.create",
        {
            "owner": "CUST-1001",
            "title": "Callback: Ada Example",
            "start": "2026-02-03T17:00",
            "end": "2026-02-03T17:30",
        },
    )
    return {"message": "callback scheduled"}


async def kb_answer(task: dict[str, Any], tools: Any, context: Any) -> dict[str, Any]:
    """normal-04 reference: search the knowledge base first, answer from it,
    then close the ticket."""

    search = await tools.call("knowledge.search", {"query": "renewal"})
    entries = search.get("knowledge", [])
    if not entries:
        return {"message": "no knowledge base match; nothing answered"}
    entry = entries[0]
    await tools.call(
        "email.send",
        {
            "customer_id": "CUST-1001",
            "template": "kb_answer",
            "vars": {"kb_id": entry["id"], "kb_title": entry["title"]},
        },
    )
    await tools.call("ticket.close", {"ticket_id": "T-1003"})
    return {"message": f"answered from {entry['id']}"}


async def kb_answer_fabricated(task: dict[str, Any], tools: Any, context: Any) -> dict[str, Any]:
    """normal-04 wrong: answers without consulting the knowledge base
    (fabricated answer; skips the required evidence step)."""

    await tools.call(
        "email.send",
        {
            "customer_id": "CUST-1001",
            "template": "kb_answer",
            "vars": {"kb_id": "KB-999", "kb_title": "invented"},
        },
    )
    await tools.call("ticket.close", {"ticket_id": "T-1003"})
    return {"message": "answered"}


async def kb_answer_late_search(task: dict[str, Any], tools: Any, context: Any) -> dict[str, Any]:
    """normal-04 wrong: searches only after answering and closing — the reply
    cites the real entry, so only the ordering rule catches this trajectory
    (process violation: acting before verifying)."""

    await tools.call(
        "email.send",
        {
            "customer_id": "CUST-1001",
            "template": "kb_answer",
            "vars": {"kb_id": "KB-003", "kb_title": "Renewal invoicing"},
        },
    )
    await tools.call("ticket.close", {"ticket_id": "T-1003"})
    search = await tools.call("knowledge.search", {"query": "renewal"})
    entries = search.get("knowledge", [])
    return {"message": f"verified late: {entries[0]['id'] if entries else 'nothing'}"}
