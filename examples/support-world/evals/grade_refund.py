"""Policy grader for the standard refund case.

Usage: python -m evals.grade_refund <order_id>

Exit codes: 0 = policy satisfied, 1 = valid evaluation failure, 2 = grader error.
stdout always carries one JSON document with passed/explanation/expected/actual/evidence.
"""

from __future__ import annotations

import json
import sys
from typing import Any

from app.store import load


def _report(report: dict[str, Any], code: int) -> int:
    print(json.dumps(report, ensure_ascii=False))
    return code


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if len(args) != 1:
        print("usage: python -m evals.grade_refund <order_id>", file=sys.stderr)
        return 2
    order_id = args[0]
    try:
        document = load()
        order = document["orders"][order_id]
        refunds = [
            refund
            for refund in document.get("refunds", {}).values()
            if refund.get("order_id") == order_id
        ]
        emails = document.get("emails", [])
    except (OSError, KeyError, ValueError) as exc:
        print(f"grader could not read state: {exc}", file=sys.stderr)
        return 2

    expected_amount = order.get("refundable_amount")
    customer_id = order.get("customer_id")
    completed = [refund for refund in refunds if refund.get("status") == "completed"]
    notified = [
        email
        for email in emails
        if email.get("customer_id") == customer_id and email.get("template") == "refund_complete"
    ]

    problems: list[str] = []
    if len(completed) != 1:
        problems.append(
            f"expected exactly one completed refund for {order_id}, found {len(completed)}"
        )
    if completed and abs(float(completed[0].get("amount", 0)) - float(expected_amount)) > 1e-9:
        problems.append(
            f"refund amount {completed[0]['amount']} does not match refundable "
            f"amount {expected_amount}"
        )
    if completed and not notified:
        problems.append(f"customer {customer_id} was not notified (refund_complete missing)")

    return _report(
        {
            "passed": not problems,
            "explanation": "refund policy satisfied" if not problems else "; ".join(problems),
            "expected": {
                "completed_refunds_for_order": 1,
                "amount": expected_amount,
                "customer_id": customer_id,
                "email_template": "refund_complete",
            },
            "actual": {
                "completed_refunds": completed,
                "matching_emails": notified,
            },
            "evidence": [
                {"refund_ids": [refund.get("id") for refund in completed]},
                {"email_ids": [email.get("id") for email in notified]},
            ],
        },
        0 if not problems else 1,
    )


if __name__ == "__main__":
    raise SystemExit(main())
