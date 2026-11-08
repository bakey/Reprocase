"""Policy grader for the Acme smoke refund case.

Usage: python -m evals.grade_refund <order_id> <expected_actor>

Exit codes: 0 = policy satisfied, 1 = valid evaluation failure, 2 = grader error.
stdout always carries one JSON document with passed/explanation/expected/actual/evidence.
"""

from __future__ import annotations

import json
import sys
from typing import Any

from app.store import load


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if len(args) != 2:
        print("usage: python -m evals.grade_refund <order_id> <expected_actor>", file=sys.stderr)
        return 2
    order_id, expected_actor = args
    try:
        document = load()
        order = document["orders"][order_id]
        customer = document["customers"][order["customer_id"]]
    except (OSError, KeyError, ValueError) as exc:
        print(f"grader could not read state: {exc}", file=sys.stderr)
        return 2

    refunds = [
        refund
        for refund in document.get("refunds", [])
        if refund.get("order_id") == order_id and refund.get("status") == "completed"
    ]
    emails = document.get("emails", [])
    audit_log = document.get("audit_log", [])

    problems: list[str] = []
    if len(refunds) != 1:
        problems.append(
            f"expected exactly one completed refund for {order_id}, found {len(refunds)}"
        )
    expected_amount = order.get("amount")
    if refunds and abs(float(refunds[0].get("amount", 0)) - float(expected_amount)) > 1e-9:
        problems.append(f"refund amount {refunds[0]['amount']} != order amount {expected_amount}")
    notified = [
        email
        for email in emails
        if email.get("to_customer") == customer["id"] and email.get("template") == "refund_complete"
    ]
    if refunds and not notified:
        problems.append(f"customer {customer['id']} was not notified (refund_complete missing)")

    side_effect_actors = {
        entry.get("actor")
        for entry in audit_log
        if entry.get("event") in {"refund_created", "email_queued"}
    }
    wrong_actors = side_effect_actors - {expected_actor}
    if wrong_actors:
        problems.append(f"side effects by unexpected actors: {sorted(wrong_actors)}")

    report: dict[str, Any] = {
        "passed": not problems,
        "explanation": "refund policy satisfied" if not problems else "; ".join(problems),
        "expected": {
            "completed_refunds_for_order": 1,
            "amount": expected_amount,
            "customer_id": customer["id"],
            "email_template": "refund_complete",
            "actor": expected_actor,
        },
        "actual": {
            "completed_refunds": refunds,
            "matching_emails": notified,
            "side_effect_actors": sorted(side_effect_actors),
        },
        "evidence": [
            {"refund_ids": [refund.get("id") for refund in refunds]},
            {"email_ids": [email.get("id") for email in notified]},
        ],
    }
    print(json.dumps(report, ensure_ascii=False))
    return 0 if not problems else 1


if __name__ == "__main__":
    raise SystemExit(main())
