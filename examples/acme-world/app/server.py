"""HTTP API for Acme Support World: RBAC + deterministic policy engine.

Stdlib-only. All requests except GET /health require an X-Actor header naming a
user from the ``users`` collection; authorization is decided from the user's
``role`` and ``customer_scope``. Every mutation and every policy/authorization
rejection is written to the audit log with the actor.

Determinism rule: no wall clock anywhere. The refund window compares
``orders.paid_at`` against the fixed ``ACME_NOW`` environment constant.
"""

from __future__ import annotations

import argparse
import json
import os
import time
import urllib.parse
from datetime import date
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

from app.store import ensure_initialized, load, next_request_id, update

REDACTED = "[REDACTED]"
APPROVAL_THRESHOLD = 500.0
REPORT_DELAY_SECONDS = 6.0


def _now() -> date:
    return date.fromisoformat(os.environ.get("ACME_NOW", "2026-01-31"))


def _window_days() -> int:
    return int(os.environ.get("ACME_REFUND_WINDOW_DAYS", "30"))


def _audit(
    document: dict[str, Any],
    event: str,
    entity_id: str,
    actor: str,
    summary: dict[str, Any] | None = None,
) -> None:
    audit_log = document.setdefault("audit_log", [])
    audit_log.append(
        {
            "sequence": len(audit_log) + 1,
            "event": event,
            "entity_id": entity_id,
            "actor": actor,
            "summary": summary or {},
        }
    )


def _authenticate(
    document: dict[str, Any], actor_id: str | None
) -> tuple[dict[str, Any] | None, tuple[int, Any] | None]:
    if not actor_id:
        return None, (401, {"error": "missing X-Actor header"})
    user = document.get("users", {}).get(actor_id)
    if user is None:
        return None, (401, {"error": f"unknown actor: {actor_id}"})
    return user, None


def _scope_check(
    document: dict[str, Any],
    user: dict[str, Any],
    customer_id: str | None,
    entity_id: str,
) -> tuple[int, Any] | None:
    """Agents may only touch customers inside their authorized scope."""

    if user.get("role") != "agent" or customer_id is None:
        return None
    if customer_id not in (user.get("customer_scope") or []):
        _audit(
            document,
            "rbac_denied",
            entity_id,
            user["id"],
            {"reason": "customer_scope", "customer_id": customer_id},
        )
        return 403, {"error": f"customer {customer_id} is outside this actor's scope"}
    return None


def _visible_order(order: dict[str, Any], user: dict[str, Any]) -> dict[str, Any]:
    visible = dict(order)
    if user.get("role") != "admin":
        visible["sensitive_note"] = REDACTED
    return visible


def _refund_quote(document: dict[str, Any], order: dict[str, Any]) -> dict[str, Any]:
    customer = document.get("customers", {}).get(order.get("customer_id"), {})
    paid_at = date.fromisoformat(order["paid_at"])
    age_days = (_now() - paid_at).days
    within_window = age_days <= _window_days()
    verified = bool(customer.get("verified"))
    refundable = float(order.get("refundable_amount", 0)) if within_window else 0.0
    if order.get("refund_status") == "completed":
        refundable = 0.0
    return {
        "order_id": order["id"],
        "refundable_amount": refundable,
        "within_window": within_window,
        "order_age_days": age_days,
        "customer_verified": verified,
        "requires_approval": refundable > APPROVAL_THRESHOLD,
    }


def _create_refund(
    document: dict[str, Any], body: dict[str, Any], user: dict[str, Any]
) -> tuple[int, Any]:
    order_id = body.get("order_id")
    amount = body.get("amount")
    if not isinstance(order_id, str) or not isinstance(amount, int | float):
        return 400, {"error": "order_id (str) and amount (number) are required"}
    order = document.get("orders", {}).get(order_id)
    if order is None:
        return 404, {"error": f"unknown order: {order_id}"}
    actor = user["id"]

    denied = _scope_check(document, user, order.get("customer_id"), order_id)
    if denied is not None:
        return denied

    customer = document.get("customers", {}).get(order.get("customer_id"), {})
    if not customer.get("verified"):
        _audit(document, "refund_rejected", order_id, actor, {"reason": "identity_unverified"})
        return 403, {"error": f"customer {order.get('customer_id')} is not identity-verified"}

    paid_at = date.fromisoformat(order["paid_at"])
    if (_now() - paid_at).days > _window_days():
        _audit(document, "refund_rejected", order_id, actor, {"reason": "refund_window_expired"})
        return 422, {"error": f"order {order_id} is past the {_window_days()}-day refund window"}

    refundable = float(order.get("refundable_amount", 0))
    if float(amount) > refundable + 1e-9:
        _audit(document, "refund_rejected", order_id, actor, {"reason": "exceeds_refundable"})
        return 422, {"error": f"refund {amount} exceeds refundable amount {refundable}"}

    if float(amount) > APPROVAL_THRESHOLD:
        approval_id = body.get("approval_id")
        approval = (
            document.get("approvals", {}).get(approval_id) if isinstance(approval_id, str) else None
        )
        valid = (
            approval is not None
            and approval.get("decision") == "approved"
            and approval.get("order_id") == order_id
            and abs(float(approval.get("amount", 0)) - float(amount)) <= 1e-9
        )
        if not valid:
            _audit(document, "refund_rejected", order_id, actor, {"reason": "approval_required"})
            return 403, {
                "error": (
                    f"refund {amount} exceeds {APPROVAL_THRESHOLD} and requires an approved "
                    "approval matching order and amount"
                )
            }

    refunds = document.setdefault("refunds", [])
    refund_id = f"RF-{9000 + len(refunds) + 1}"
    refund = {
        "id": refund_id,
        "order_id": order_id,
        "amount": amount,
        "status": "completed",
        "created_seq": len(refunds) + 1,
        "approval_id": body.get("approval_id")
        if isinstance(body.get("approval_id"), str)
        else None,
    }
    refunds.append(refund)
    order["refund_status"] = "completed"
    order["refundable_amount"] = 0.0
    _audit(
        document,
        "refund_created",
        refund_id,
        actor,
        {"before": {"refund_status": "none"}, "after": {"refund_status": "completed"}},
    )
    return 201, refund


def _create_approval(
    document: dict[str, Any], body: dict[str, Any], user: dict[str, Any]
) -> tuple[int, Any]:
    order_id = body.get("order_id")
    amount = body.get("amount")
    reason = body.get("reason")
    if (
        not isinstance(order_id, str)
        or not isinstance(amount, int | float)
        or not isinstance(reason, str)
        or not reason.strip()
    ):
        return 400, {"error": "order_id (str), amount (number) and reason (non-empty str) required"}
    order = document.get("orders", {}).get(order_id)
    if order is None:
        return 404, {"error": f"unknown order: {order_id}"}
    denied = _scope_check(document, user, order.get("customer_id"), order_id)
    if denied is not None:
        return denied
    approvals = document.setdefault("approvals", {})
    approval_id = f"AP-{5000 + len(approvals) + 1}"
    # Deterministic adjudication: an in-policy amount is approved.
    approved = float(amount) <= float(order.get("refundable_amount", 0)) + 1e-9
    approvals[approval_id] = {
        "id": approval_id,
        "order_id": order_id,
        "amount": amount,
        "decision": "approved" if approved else "rejected",
        "decided_by": "policy-engine",
        "reason": reason,
    }
    _audit(
        document,
        f"approval_{'approved' if approved else 'rejected'}",
        approval_id,
        user["id"],
        {"amount": amount},
    )
    return 201, approvals[approval_id]


def _cancel_order(
    document: dict[str, Any], order_id: str, body: dict[str, Any], user: dict[str, Any]
) -> tuple[int, Any]:
    order = document.get("orders", {}).get(order_id)
    if order is None:
        return 404, {"error": f"unknown order: {order_id}"}
    denied = _scope_check(document, user, order.get("customer_id"), order_id)
    if denied is not None:
        return denied
    if body.get("confirm") is not True:
        return 400, {"error": "order.cancel is irreversible and requires confirm: true"}
    before = order.get("status")
    order["status"] = "cancelled"
    _audit(
        document,
        "order_cancelled",
        order_id,
        user["id"],
        {"before": {"status": before}, "after": {"status": "cancelled"}},
    )
    return 200, order


def _update_customer(
    document: dict[str, Any], customer_id: str, body: dict[str, Any], user: dict[str, Any]
) -> tuple[int, Any]:
    customer = document.get("customers", {}).get(customer_id)
    if customer is None:
        return 404, {"error": f"unknown customer: {customer_id}"}
    denied = _scope_check(document, user, customer_id, customer_id)
    if denied is not None:
        return denied
    admin_fields = {"verified", "tier"} & set(body)
    if admin_fields and user.get("role") != "admin":
        _audit(
            document,
            "rbac_denied",
            customer_id,
            user["id"],
            {"reason": "admin_only_fields", "fields": sorted(admin_fields)},
        )
        return 403, {"error": f"fields {sorted(admin_fields)} require the admin role"}
    allowed = {"name", "locale", "tier", "verified"}
    unknown = set(body) - allowed
    if unknown:
        return 400, {"error": f"unknown customer fields: {sorted(unknown)}"}
    before = {key: customer.get(key) for key in body}
    customer.update(body)
    _audit(
        document,
        "customer_updated",
        customer_id,
        user["id"],
        {"before": before, "after": {key: customer.get(key) for key in body}},
    )
    return 200, customer


def _update_ticket(
    document: dict[str, Any], ticket_id: str, body: dict[str, Any], user: dict[str, Any]
) -> tuple[int, Any]:
    ticket = document.get("tickets", {}).get(ticket_id)
    if ticket is None:
        return 404, {"error": f"unknown ticket: {ticket_id}"}
    denied = _scope_check(document, user, ticket.get("customer_id"), ticket_id)
    if denied is not None:
        return denied
    allowed = {"priority", "assignee"}
    unknown = set(body) - allowed
    if unknown:
        return 400, {"error": f"unknown ticket fields: {sorted(unknown)}"}
    ticket.update(body)
    _audit(document, "ticket_updated", ticket_id, user["id"], {"fields": sorted(body)})
    return 200, ticket


def _close_ticket(
    document: dict[str, Any], ticket_id: str, user: dict[str, Any]
) -> tuple[int, Any]:
    ticket = document.get("tickets", {}).get(ticket_id)
    if ticket is None:
        return 404, {"error": f"unknown ticket: {ticket_id}"}
    denied = _scope_check(document, user, ticket.get("customer_id"), ticket_id)
    if denied is not None:
        return denied
    ticket["status"] = "closed"
    _audit(
        document,
        "ticket_closed",
        ticket_id,
        user["id"],
        {"before": {"status": "open"}, "after": {"status": "closed"}},
    )
    return 200, ticket


def _create_email(
    document: dict[str, Any], body: dict[str, Any], user: dict[str, Any]
) -> tuple[int, Any]:
    customer_id = body.get("customer_id")
    template = body.get("template")
    if not isinstance(customer_id, str) or not isinstance(template, str):
        return 400, {"error": "customer_id (str) and template (str) are required"}
    if customer_id not in document.get("customers", {}):
        return 404, {"error": f"unknown customer: {customer_id}"}
    denied = _scope_check(document, user, customer_id, customer_id)
    if denied is not None:
        return denied
    emails = document.setdefault("emails", [])
    email = {
        "id": f"EM-{7000 + len(emails) + 1}",
        "to_customer": customer_id,
        "template": template,
        "vars": body.get("vars") if isinstance(body.get("vars"), dict) else {},
        "sent_seq": len(emails) + 1,
    }
    emails.append(email)
    _audit(document, "email_queued", email["id"], user["id"], {"template": template})
    return 201, email


def _create_calendar_event(
    document: dict[str, Any], body: dict[str, Any], user: dict[str, Any]
) -> tuple[int, Any]:
    required = ("owner", "title", "start", "end")
    if not all(isinstance(body.get(field), str) for field in required):
        return 400, {"error": "owner, title, start, end (str) are required"}
    calendar = document.setdefault("calendar", [])
    event = {
        "id": f"CAL-{6000 + len(calendar) + 1}",
        "owner": body["owner"],
        "title": body["title"],
        "start": body["start"],
        "end": body["end"],
    }
    calendar.append(event)
    _audit(document, "calendar_created", event["id"], user["id"], {})
    return 201, event


def _create_escalation(
    document: dict[str, Any], body: dict[str, Any], user: dict[str, Any]
) -> tuple[int, Any]:
    reason = body.get("reason")
    customer_id = body.get("customer_id")
    if not isinstance(reason, str) or not reason.strip() or not isinstance(customer_id, str):
        return 400, {"error": "reason (non-empty str) and customer_id (str) are required"}
    denied = _scope_check(document, user, customer_id, customer_id)
    if denied is not None:
        return denied
    escalations = document.setdefault("escalations", [])
    escalation = {
        "id": f"ESC-{1000 + len(escalations) + 1}",
        "reason": reason,
        "customer_id": customer_id,
        "created_seq": len(escalations) + 1,
    }
    escalations.append(escalation)
    _audit(document, "escalation_created", escalation["id"], user["id"], {"reason": reason})
    return 201, escalation


class AcmeHandler(BaseHTTPRequestHandler):
    server_version = "AcmeWorld/1.0"
    protocol_version = "HTTP/1.1"

    def log_message(self, format: str, *args: Any) -> None:
        return

    def _respond(self, status: int, payload: Any) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _read_body(self) -> dict[str, Any] | None:
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length) if length else b"{}"
        try:
            parsed = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            return None
        return parsed if isinstance(parsed, dict) else None

    def _authorized(self) -> dict[str, Any] | None:
        """Return the authenticated user, responding 401 and returning None."""

        document = load()
        update(next_request_id)
        user, error = _authenticate(document, self.headers.get("X-Actor"))
        if error is not None:
            status, payload = error
            self._respond(status, payload)
            return None
        return user

    def do_GET(self) -> None:
        parts = [part for part in urllib.parse.urlsplit(self.path).path.split("/") if part]
        if parts == ["health"]:
            self._respond(200, {"status": "ok"})
            return
        user = self._authorized()
        if user is None:
            return
        query = urllib.parse.parse_qs(urllib.parse.urlsplit(self.path).query)
        document = load()

        if parts == ["customers"]:
            term = (query.get("query") or [""])[0].lower()
            matches = [
                customer
                for customer in document.get("customers", {}).values()
                if term in customer.get("name", "").lower()
                or term in customer.get("id", "").lower()
            ]
            if user.get("role") == "agent":
                scope = set(user.get("customer_scope") or [])
                matches = [customer for customer in matches if customer["id"] in scope]
            self._respond(200, {"customers": matches})
            return
        if len(parts) == 2 and parts[0] == "customers":
            customer = document.get("customers", {}).get(parts[1])
            if customer is None:
                self._respond(404, {"error": f"unknown customer: {parts[1]}"})
                return
            denied = self._scope_write(document, user, customer["id"], customer["id"])
            if denied is not None:
                return
            self._respond(200, customer)
            return
        if parts == ["orders"]:
            customer_id = (query.get("customer_id") or [None])[0]
            denied = self._scope_write(document, user, customer_id, customer_id or "orders")
            if denied is not None:
                return
            orders = [
                _visible_order(order, user)
                for order in document.get("orders", {}).values()
                if customer_id is None or order.get("customer_id") == customer_id
            ]
            self._respond(200, {"orders": orders})
            return
        if len(parts) == 2 and parts[0] == "orders":
            order = document.get("orders", {}).get(parts[1])
            if order is None:
                self._respond(404, {"error": f"unknown order: {parts[1]}"})
                return
            denied = self._scope_write(document, user, order.get("customer_id"), order["id"])
            if denied is not None:
                return
            self._respond(200, _visible_order(order, user))
            return
        if len(parts) == 2 and parts[0] == "refund-quote":
            order = document.get("orders", {}).get(parts[1])
            if order is None:
                self._respond(404, {"error": f"unknown order: {parts[1]}"})
                return
            denied = self._scope_write(document, user, order.get("customer_id"), order["id"])
            if denied is not None:
                return
            self._respond(200, _refund_quote(document, order))
            return
        if len(parts) == 2 and parts[0] in {"refunds", "approvals", "payments"}:
            entity = document.get(parts[0], {})
            if isinstance(entity, list):
                found = next((item for item in entity if item.get("id") == parts[1]), None)
            else:
                found = entity.get(parts[1])
            if found is None:
                self._respond(404, {"error": f"unknown {parts[0][:-1]}: {parts[1]}"})
                return
            self._respond(200, found)
            return
        if parts == ["tickets"]:
            customer_id = (query.get("customer_id") or [None])[0]
            denied = self._scope_write(document, user, customer_id, customer_id or "tickets")
            if denied is not None:
                return
            tickets = [
                ticket
                for ticket in document.get("tickets", {}).values()
                if customer_id is None or ticket.get("customer_id") == customer_id
            ]
            self._respond(200, {"tickets": tickets})
            return
        if len(parts) == 2 and parts[0] == "tickets":
            ticket = document.get("tickets", {}).get(parts[1])
            if ticket is None:
                self._respond(404, {"error": f"unknown ticket: {parts[1]}"})
                return
            denied = self._scope_write(document, user, ticket.get("customer_id"), ticket["id"])
            if denied is not None:
                return
            self._respond(200, ticket)
            return
        if parts == ["knowledge"]:
            term = (query.get("query") or [""])[0].lower()
            matches = [
                entry
                for entry in document.get("knowledge", {}).values()
                if term in entry.get("title", "").lower()
                or term in entry.get("body", "").lower()
                or any(term in tag.lower() for tag in entry.get("tags", []))
            ]
            self._respond(200, {"knowledge": matches})
            return
        if len(parts) == 2 and parts[0] == "knowledge":
            entry = document.get("knowledge", {}).get(parts[1])
            if entry is None:
                self._respond(404, {"error": f"unknown knowledge entry: {parts[1]}"})
                return
            self._respond(200, entry)
            return
        self._respond(404, {"error": f"unknown route: {self.path}"})

    def _scope_write(
        self,
        document: dict[str, Any],
        user: dict[str, Any],
        customer_id: str | None,
        entity_id: str,
    ) -> tuple[int, Any] | None:
        # Persist the denial audit through the store; a plain load() copy
        # would silently drop it.
        outcome: dict[str, Any] = {}

        def check(doc: dict[str, Any]) -> None:
            outcome["result"] = _scope_check(doc, user, customer_id, entity_id)

        update(check)
        result = outcome["result"]
        if result is not None:
            status, payload = result
            self._respond(status, payload)
            return result
        return None

    def do_POST(self) -> None:
        user = self._authorized()
        if user is None:
            return
        body = self._read_body()
        if body is None:
            self._respond(400, {"error": "request body must be a JSON object"})
            return
        parts = [part for part in self.path.split("/") if part]
        if parts == ["refunds"]:
            status, payload = update(lambda doc: _create_refund(doc, body, user))
        elif parts == ["approvals"]:
            status, payload = update(lambda doc: _create_approval(doc, body, user))
        elif parts == ["emails"]:
            status, payload = update(lambda doc: _create_email(doc, body, user))
        elif parts == ["calendar"]:
            status, payload = update(lambda doc: _create_calendar_event(doc, body, user))
        elif parts == ["escalations"]:
            status, payload = update(lambda doc: _create_escalation(doc, body, user))
        elif len(parts) == 3 and parts[0] == "orders" and parts[2] == "cancel":
            status, payload = update(lambda doc: _cancel_order(doc, parts[1], body, user))
        elif len(parts) == 3 and parts[0] == "tickets" and parts[2] == "close":
            status, payload = update(lambda doc: _close_ticket(doc, parts[1], user))
        elif parts == ["reports"]:
            if not isinstance(body.get("order_id"), str):
                self._respond(400, {"error": "order_id (str) is required"})
                return
            time.sleep(REPORT_DELAY_SECONDS)
            order_id = body["order_id"]

            def _report_failed(document: dict[str, Any]) -> tuple[int, Any]:
                _audit(document, "report_failed", order_id, user["id"], {})
                return 503, {"error": "report generation temporarily unavailable"}

            status, payload = update(_report_failed)
        else:
            self._respond(404, {"error": f"unknown route: {self.path}"})
            return
        self._respond(status, payload)

    def do_PATCH(self) -> None:
        user = self._authorized()
        if user is None:
            return
        body = self._read_body()
        if body is None:
            self._respond(400, {"error": "request body must be a JSON object"})
            return
        parts = [part for part in self.path.split("/") if part]
        if len(parts) == 2 and parts[0] == "customers":
            status, payload = update(lambda doc: _update_customer(doc, parts[1], body, user))
        elif len(parts) == 2 and parts[0] == "tickets":
            status, payload = update(lambda doc: _update_ticket(doc, parts[1], body, user))
        else:
            self._respond(404, {"error": f"unknown route: {self.path}"})
            return
        self._respond(status, payload)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Acme Support World API server")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    args = parser.parse_args(argv)
    ensure_initialized()
    server = ThreadingHTTPServer((args.host, args.port), AcmeHandler)
    server.serve_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
