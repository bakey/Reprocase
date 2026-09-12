"""Correct agent whose imports all happen lazily inside the function body.

Regression fixture for execution-time import isolation: relative imports and
importlib.import_module must resolve against this case's own root while the
agent executes, not only while it is loaded.
"""

from __future__ import annotations

from typing import Any


async def run(task: dict[str, Any], tools: Any, context: Any) -> dict[str, Any]:
    import importlib

    from . import lazy_helpers  # relative import inside the function body

    helpers = importlib.import_module("agents.lazy_helpers")  # absolute dynamic import
    assert helpers is lazy_helpers

    order = await tools.call("order.get", {"order_id": lazy_helpers.ORDER_ID})
    refund = await tools.call(
        "refund.create",
        {"order_id": order["id"], "amount": order["refundable_amount"]},
    )
    await tools.call(
        "email.send",
        {"customer_id": order["customer_id"], "template": helpers.EMAIL_TEMPLATE},
    )
    return {"message": lazy_helpers.build_message(refund["id"])}
