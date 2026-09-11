"""HTTP API for the Support World service. Stdlib-only."""

from __future__ import annotations

import argparse
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

from app.store import ensure_initialized, load, next_request_id, update


def _audit(document: dict[str, Any], event: str, entity_id: str) -> None:
    audit_log = document.setdefault("audit_log", [])
    audit_log.append({"sequence": len(audit_log) + 1, "event": event, "entity_id": entity_id})


def _create_refund(document: dict[str, Any], body: dict[str, Any]) -> tuple[int, Any]:
    order_id = body.get("order_id")
    amount = body.get("amount")
    if not isinstance(order_id, str) or not isinstance(amount, int | float):
        return 400, {"error": "order_id (str) and amount (number) are required"}
    orders = document.get("orders", {})
    order = orders.get(order_id)
    if order is None:
        return 404, {"error": f"unknown order: {order_id}"}
    refunds = document.setdefault("refunds", {})
    refund_id = f"RF-{9000 + len(refunds) + 1}"
    refunds[refund_id] = {
        "id": refund_id,
        "order_id": order_id,
        "customer_id": order.get("customer_id"),
        "amount": amount,
        "status": "completed",
    }
    order["refund_status"] = "completed"
    _audit(document, "refund_created", refund_id)
    return 201, refunds[refund_id]


def _create_email(document: dict[str, Any], body: dict[str, Any]) -> tuple[int, Any]:
    customer_id = body.get("customer_id")
    template = body.get("template")
    if not isinstance(customer_id, str) or not isinstance(template, str):
        return 400, {"error": "customer_id (str) and template (str) are required"}
    if customer_id not in document.get("customers", {}):
        return 404, {"error": f"unknown customer: {customer_id}"}
    emails = document.setdefault("emails", [])
    email_id = f"EM-{7000 + len(emails) + 1}"
    email = {
        "id": email_id,
        "customer_id": customer_id,
        "template": template,
        "status": "queued",
    }
    emails.append(email)
    _audit(document, "email_queued", email_id)
    return 201, email


class SupportWorldHandler(BaseHTTPRequestHandler):
    server_version = "SupportWorld/0.1"
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

    def _track(self) -> None:
        update(next_request_id)

    def do_GET(self) -> None:
        self._track()
        parts = [part for part in self.path.split("/") if part]
        if parts == ["health"]:
            self._respond(200, {"status": "ok"})
            return
        if len(parts) == 2 and parts[0] in {"customers", "orders", "refunds"}:
            entity = load().get(parts[0], {}).get(parts[1])
            if entity is None:
                self._respond(404, {"error": f"unknown {parts[0][:-1]}: {parts[1]}"})
                return
            self._respond(200, entity)
            return
        self._respond(404, {"error": f"unknown route: {self.path}"})

    def do_POST(self) -> None:
        body = self._read_body()
        if body is None:
            self._respond(400, {"error": "request body must be a JSON object"})
            return
        if self.path == "/refunds":
            status, payload = update(lambda doc: _create_refund(doc, body))
        elif self.path == "/emails":
            status, payload = update(lambda doc: _create_email(doc, body))
        else:
            self._respond(404, {"error": f"unknown route: {self.path}"})
            return
        self._respond(status, payload)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Support World API server")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    args = parser.parse_args(argv)
    ensure_initialized()
    server = ThreadingHTTPServer((args.host, args.port), SupportWorldHandler)
    server.serve_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
