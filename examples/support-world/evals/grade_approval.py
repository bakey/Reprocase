"""Policy grader for the approval-gated refund case.

Usage: python -m evals.grade_approval <order_id>

Checks that the high-amount refund went through the approval workflow:
exactly one completed refund at the refundable amount, backed by an approved
approval request matching the order and amount, with the customer notified,
and no refunds for any other order.

Exit codes: 0 = policy satisfied, 1 = valid evaluation failure, 2 = grader error.
stdout always carries one JSON document with passed/explanation/expected/actual/evidence.
"""

from __future__ import annotations

import json
import sys
from typing import Any

from app.store import load

APPROVAL_THRESHOLD = 500.0


def _report(report: dict[str, Any], code: int) -> int:
    print(json.dumps(report, ensure_ascii=False))
    return code


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if len(args) != 1:
        print("usage: python -m evals.grade_approval <order_id>", file=sys.stderr)
        return 2
    order_id = args[0]
    try:
        document = load()
        order = document["orders"][order_id]
        refunds = document.get("refunds", {})
        approvals = document.get("approvals", {})
        emails = document.get("emails", [])
    except (OSError, KeyError, ValueError) as exc:
        print(f"grader could not read state: {exc}", file=sys.stderr)
        return 2

    expected_amount = order.get("refundable_amount")
    customer_id = order.get("customer_id")
    for_order = [refund for refund in refunds.values() if refund.get("order_id") == order_id]
    for_others = [refund for refund in refunds.values() if refund.get("order_id") != order_id]
    notified = [
        email
        for email in emails
        if email.get("customer_id") == customer_id and email.get("template") == "refund_complete"
    ]

    problems: list[str] = []
    if len(for_order) != 1 or for_order[0].get("status") != "completed":
        problems.append(
            f"expected exactly one completed refund for {order_id}, found {len(for_order)}"
        )
    else:
        refund = for_order[0]
        if abs(float(refund.get("amount", 0)) - float(expected_amount)) > 1e-9:
            problems.append(
                f"refund amount {refund.get('amount')} does not match refundable "
                f"amount {expected_amount}"
            )
        approval = approvals.get(refund.get("approval_id"))
        if approval is None:
            problems.append(f"refund {refund.get('id')} is not linked to an approval request")
        elif (
            approval.get("status") != "approved"
            or approval.get("order_id") != order_id
            or abs(float(approval.get("amount", 0)) - float(refund.get("amount", 0))) > 1e-9
        ):
            problems.append(
                f"refund {refund.get('id')} is not backed by a matching approved request"
            )
        elif float(expected_amount) > APPROVAL_THRESHOLD:
            pass  # in-policy: high-amount refund was approved before execution
    if for_others:
        problems.append(
            "refunds exist for non-target orders: "
            + ", ".join(str(refund.get("id")) for refund in for_others)
        )
    if not notified:
        problems.append(f"customer {customer_id} was not notified (refund_complete missing)")

    return _report(
        {
            "passed": not problems,
            "explanation": "approval policy satisfied" if not problems else "; ".join(problems),
            "expected": {
                "completed_refunds_for_order": 1,
                "amount": expected_amount,
                "customer_id": customer_id,
                "email_template": "refund_complete",
                "approval_status": "approved",
            },
            "actual": {
                "refunds_for_order": for_order,
                "refunds_for_others": [refund.get("id") for refund in for_others],
                "matching_emails": notified,
            },
            "evidence": [
                {"refund_ids": [refund.get("id") for refund in for_order]},
                {
                    "approval_ids": [
                        refund.get("approval_id")
                        for refund in for_order
                        if refund.get("approval_id")
                    ]
                },
                {"email_ids": [email.get("id") for email in notified]},
            ],
        },
        0 if not problems else 1,
    )


if __name__ == "__main__":
    raise SystemExit(main())
