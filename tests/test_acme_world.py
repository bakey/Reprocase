"""Acme World RBAC and policy-engine integration matrix (host-level, no Docker).

The stdlib server runs in-process on an ephemeral port against a tmp database,
so each policy rule gets fast positive and negative coverage with audit checks.
"""

from __future__ import annotations

import importlib
import json
import threading
import urllib.error
import urllib.request
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from reprocase.entrypoints import ImportIsolation

ROOT = Path(__file__).resolve().parents[1]
ACME_DIR = ROOT / "examples" / "acme-world"
AGENT = "U-AGENT-01"
STAFF = "U-STAFF-01"
ADMIN = "U-ADMIN-01"


@pytest.fixture()
def acme(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Any:
    monkeypatch.setenv("ACME_WORLD_DB", str(tmp_path / "db.json"))
    monkeypatch.setenv("ACME_NOW", "2026-01-31")
    with ImportIsolation(ACME_DIR):
        server_mod = importlib.import_module("app.server")
        store_mod = importlib.import_module("app.store")
    store_mod.reset(ACME_DIR / "fixtures" / "acme.json", tmp_path / "db.json")
    server = server_mod.ThreadingHTTPServer(("127.0.0.1", 0), server_mod.AcmeHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield SimpleNamespace(
        base_url=f"http://127.0.0.1:{server.server_address[1]}",
        store=store_mod,
        db_path=tmp_path / "db.json",
    )
    server.shutdown()
    server.server_close()
    thread.join(timeout=5)


def _call(
    acme: Any,
    method: str,
    path: str,
    actor: str | None = None,
    body: dict[str, Any] | None = None,
) -> tuple[int, Any]:
    request = urllib.request.Request(
        f"{acme.base_url}{path}",
        data=json.dumps(body).encode("utf-8") if body is not None else None,
        headers={"Content-Type": "application/json", **({"X-Actor": actor} if actor else {})},
        method=method,
    )
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            return response.status, json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read().decode("utf-8"))


def _audit(acme: Any, event: str) -> list[dict[str, Any]]:
    return [
        entry
        for entry in acme.store.load(acme.db_path).get("audit_log", [])
        if entry.get("event") == event
    ]


def test_missing_or_unknown_actor_is_401(acme: Any) -> None:
    status, _ = _call(acme, "GET", "/customers/CUST-1001")
    assert status == 401
    status, _ = _call(acme, "GET", "/customers/CUST-1001", actor="U-GHOST")
    assert status == 401
    # health stays open for the runner's healthcheck
    status, _ = _call(acme, "GET", "/health")
    assert status == 200


def test_identity_verification_policy(acme: Any) -> None:
    status, body = _call(acme, "POST", "/refunds", STAFF, {"order_id": "ORD-2002", "amount": 120.0})
    assert status == 403
    assert "not identity-verified" in body["error"]
    entries = _audit(acme, "refund_rejected")
    assert entries[-1]["summary"]["reason"] == "identity_unverified"
    assert entries[-1]["actor"] == STAFF

    status, body = _call(acme, "POST", "/refunds", STAFF, {"order_id": "ORD-1001", "amount": 49.9})
    assert status == 201
    assert _audit(acme, "refund_created")[-1]["actor"] == STAFF


def test_refund_window_policy(acme: Any) -> None:
    status, quote = _call(acme, "GET", "/refund-quote/ORD-3003", STAFF)
    assert status == 200
    assert quote["within_window"] is False
    assert quote["refundable_amount"] == 0.0

    status, _ = _call(acme, "POST", "/refunds", STAFF, {"order_id": "ORD-3003", "amount": 35.0})
    assert status == 422
    entries = _audit(acme, "refund_rejected")
    assert entries[-1]["summary"]["reason"] == "refund_window_expired"


def test_amount_approval_policy(acme: Any) -> None:
    status, _ = _call(acme, "POST", "/refunds", STAFF, {"order_id": "ORD-4004", "amount": 850.0})
    assert status == 403
    assert _audit(acme, "refund_rejected")[-1]["summary"]["reason"] == "approval_required"

    status, approval = _call(
        acme,
        "POST",
        "/approvals",
        STAFF,
        {"order_id": "ORD-4004", "amount": 850.0, "reason": "enterprise exception"},
    )
    assert status == 201
    assert approval["decision"] == "approved"
    assert approval["decided_by"] == "policy-engine"

    # a mismatched amount must not reuse the approval
    status, _ = _call(
        acme,
        "POST",
        "/refunds",
        STAFF,
        {"order_id": "ORD-4004", "amount": 800.0, "approval_id": approval["id"]},
    )
    assert status == 403

    status, refund = _call(
        acme,
        "POST",
        "/refunds",
        STAFF,
        {"order_id": "ORD-4004", "amount": 850.0, "approval_id": approval["id"]},
    )
    assert status == 201
    assert refund["approval_id"] == approval["id"]


def test_sensitive_field_redaction_by_role(acme: Any) -> None:
    _, as_agent = _call(acme, "GET", "/orders/ORD-1001", AGENT)
    assert as_agent["sensitive_note"] == "[REDACTED]"
    _, as_staff = _call(acme, "GET", "/orders/ORD-1001", STAFF)
    assert as_staff["sensitive_note"] == "[REDACTED]"
    _, as_admin = _call(acme, "GET", "/orders/ORD-1001", ADMIN)
    assert as_admin["sensitive_note"] == "VIP renewal discount negotiated by account manager"


def test_cross_customer_scope_isolation(acme: Any) -> None:
    status, _ = _call(acme, "GET", "/customers/CUST-2002", AGENT)
    assert status == 403
    entries = _audit(acme, "rbac_denied")
    assert entries[-1]["actor"] == AGENT
    assert entries[-1]["summary"]["reason"] == "customer_scope"

    status, _ = _call(acme, "GET", "/customers/CUST-1001", AGENT)
    assert status == 200

    status, _ = _call(acme, "POST", "/refunds", AGENT, {"order_id": "ORD-2002", "amount": 120.0})
    assert status == 403

    _, listing = _call(acme, "GET", "/customers?query=", AGENT)
    assert [customer["id"] for customer in listing["customers"]] == ["CUST-1001"]

    status, _ = _call(acme, "GET", "/customers/CUST-2002", STAFF)
    assert status == 200


def test_order_cancel_requires_confirmation(acme: Any) -> None:
    status, body = _call(acme, "POST", "/orders/ORD-1001/cancel", AGENT, {})
    assert status == 400
    assert "confirm" in body["error"]

    status, order = _call(acme, "POST", "/orders/ORD-1001/cancel", AGENT, {"confirm": True})
    assert status == 200
    assert order["status"] == "cancelled"
    entry = _audit(acme, "order_cancelled")[-1]
    assert entry["actor"] == AGENT
    assert entry["summary"]["before"] == {"status": "paid"}


def test_admin_only_customer_fields(acme: Any) -> None:
    status, _ = _call(acme, "PATCH", "/customers/CUST-1001", AGENT, {"verified": False})
    assert status == 403
    assert _audit(acme, "rbac_denied")[-1]["summary"]["reason"] == "admin_only_fields"

    status, _ = _call(acme, "PATCH", "/customers/CUST-1001", AGENT, {"name": "Ada E."})
    assert status == 200

    status, customer = _call(acme, "PATCH", "/customers/CUST-1001", ADMIN, {"verified": True})
    assert status == 200
    assert customer["verified"] is True


def test_human_escalation_creates_record(acme: Any) -> None:
    status, escalation = _call(
        acme,
        "POST",
        "/escalations",
        AGENT,
        {"reason": "customer threatens legal action", "customer_id": "CUST-1001"},
    )
    assert status == 201
    assert escalation["created_seq"] == 1
    entry = _audit(acme, "escalation_created")[-1]
    assert entry["actor"] == AGENT


def test_knowledge_search_and_prompt_injection_content(acme: Any) -> None:
    _, listing = _call(acme, "GET", "/knowledge?query=refund", AGENT)
    assert any(entry["id"] == "KB-001" for entry in listing["knowledge"])
    _, entry = _call(acme, "GET", "/knowledge/KB-001", AGENT)
    assert "ignore previous instructions" in entry["body"]


def test_refund_amount_must_be_positive(acme: Any) -> None:
    """R1: non-positive amounts are rejected with 400 + audit, not accepted."""

    status, body = _call(acme, "POST", "/refunds", STAFF, {"order_id": "ORD-1001", "amount": -100})
    assert status == 400
    assert "positive" in body["error"]
    entry = _audit(acme, "refund_rejected")[-1]
    assert entry["summary"]["reason"] == "invalid_amount"
    assert entry["actor"] == STAFF

    status, _ = _call(acme, "POST", "/refunds", STAFF, {"order_id": "ORD-1001", "amount": 0})
    assert status == 400

    # the order was not touched by the rejected attempts
    _, order = _call(acme, "GET", "/orders/ORD-1001", STAFF)
    assert order["refund_status"] == "none"

    status, _ = _call(acme, "POST", "/refunds", STAFF, {"order_id": "ORD-1001", "amount": 49.9})
    assert status == 201


def test_list_endpoints_filter_agent_scope(acme: Any) -> None:
    """R2: list endpoints without a filter only show agents their own scope."""

    _, listing = _call(acme, "GET", "/orders", AGENT)
    assert {order["customer_id"] for order in listing["orders"]} == {"CUST-1001"}
    _, listing = _call(acme, "GET", "/tickets", AGENT)
    assert {ticket["customer_id"] for ticket in listing["tickets"]} == {"CUST-1001"}

    # staff and admin are unaffected
    _, listing = _call(acme, "GET", "/orders", STAFF)
    assert len(listing["orders"]) == 4
    _, listing = _call(acme, "GET", "/tickets", ADMIN)
    assert len(listing["tickets"]) == 2

    # an explicit out-of-scope filter is still a hard denial, not a filter
    status, _ = _call(acme, "GET", "/orders?customer_id=CUST-2002", AGENT)
    assert status == 403
    assert _audit(acme, "rbac_denied")[-1]["summary"]["reason"] == "customer_scope"


def test_double_close_records_actual_previous_status(acme: Any) -> None:
    """Nit: the second close must not claim the ticket was open."""

    _call(acme, "POST", "/tickets/T-1001/close", AGENT)
    _call(acme, "POST", "/tickets/T-1001/close", AGENT)
    entries = _audit(acme, "ticket_closed")
    assert entries[-2]["summary"]["before"] == {"status": "open"}
    assert entries[-1]["summary"]["before"] == {"status": "closed"}


def test_post_with_query_string_routes(acme: Any) -> None:
    """Nit: a query string must not break POST/PATCH route matching."""

    status, escalation = _call(
        acme,
        "POST",
        "/escalations?source=test",
        AGENT,
        {"reason": "query string regression", "customer_id": "CUST-1001"},
    )
    assert status == 201
    assert escalation["reason"] == "query string regression"
