"""Helper imported lazily by lazy_refund_agent (execution-time import check)."""

from __future__ import annotations

ORDER_ID = "ORD-1001"
EMAIL_TEMPLATE = "refund_complete"


def build_message(refund_id: str) -> str:
    return f"退款 {refund_id} 已完成"
