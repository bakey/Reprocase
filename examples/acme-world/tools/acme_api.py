"""Acme tool provider: HTTP tools with the agent identity injected.

The actor identity is infrastructure, not an agent decision: every request
carries ``X-Actor`` from the provider, and the environment adjudicates it.
"""

from __future__ import annotations

import asyncio
import json
import re
import urllib.error
import urllib.request
from collections.abc import Mapping
from typing import Any

# The eval-runner's agent identity inside Acme World (see fixtures/*/users).
AGENT_ACTOR = "U-AGENT-01"


class AcmeTool:
    def __init__(
        self,
        base_url: str,
        *,
        actor: str,
        name: str,
        description: str,
        method: str,
        path_template: str,
        input_schema: dict[str, Any],
    ) -> None:
        self.base_url = base_url
        self.actor = actor
        self.name = name
        self.description = description
        self.method = method
        self.path_template = path_template
        self.input_schema = input_schema

    def _send(self, arguments: Mapping[str, Any]) -> Any:
        path = self.path_template.format(**arguments)
        path_vars = set(re.findall(r"{(\w+)}", self.path_template))
        body = (
            {key: value for key, value in arguments.items() if key not in path_vars}
            if self.method in {"POST", "PATCH"}
            else None
        )
        request = urllib.request.Request(
            f"{self.base_url}{path}",
            data=json.dumps(body).encode("utf-8") if body is not None else None,
            headers={"Content-Type": "application/json", "X-Actor": self.actor},
            method=self.method,
        )
        try:
            with urllib.request.urlopen(request, timeout=10) as response:
                return json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"{self.name} failed with HTTP {exc.code}: {detail}") from exc

    async def invoke(self, arguments: Mapping[str, Any]) -> Any:
        return await asyncio.to_thread(self._send, arguments)


def _object_schema(properties: dict[str, Any], required: list[str]) -> dict[str, Any]:
    return {
        "type": "object",
        "properties": properties,
        "required": required,
        "additionalProperties": False,
    }


def create_tools(environment: Any) -> list[AcmeTool]:
    base_url = environment.service_urls["api"]

    def tool(
        name: str,
        description: str,
        method: str,
        path: str,
        properties: dict[str, Any] | None = None,
        required: list[str] | None = None,
    ) -> AcmeTool:
        return AcmeTool(
            base_url,
            actor=AGENT_ACTOR,
            name=name,
            description=description,
            method=method,
            path_template=path,
            input_schema=_object_schema(properties or {}, required or []),
        )

    s = {"type": "string"}
    n = {"type": "number"}
    b = {"type": "boolean"}
    return [
        tool(
            "customer.search",
            "Search customers by name or id.",
            "GET",
            "/customers?query={query}",
            {"query": s},
            ["query"],
        ),
        tool(
            "customer.get",
            "Fetch one customer by id.",
            "GET",
            "/customers/{customer_id}",
            {"customer_id": s},
            ["customer_id"],
        ),
        tool(
            "customer.update",
            "Update customer fields (verified/tier are admin-only).",
            "PATCH",
            "/customers/{customer_id}",
            {"customer_id": s, "name": s, "locale": s},
            ["customer_id"],
        ),
        tool(
            "order.search",
            "List orders, optionally for one customer.",
            "GET",
            "/orders?customer_id={customer_id}",
            {"customer_id": s},
            ["customer_id"],
        ),
        tool(
            "order.get",
            "Fetch one order by id.",
            "GET",
            "/orders/{order_id}",
            {"order_id": s},
            ["order_id"],
        ),
        tool(
            "order.cancel",
            "Cancel an order (irreversible; requires confirm=true).",
            "POST",
            "/orders/{order_id}/cancel",
            {"order_id": s, "confirm": b},
            ["order_id", "confirm"],
        ),
        tool(
            "refund.quote",
            "Quote refundability of an order under policy.",
            "GET",
            "/refund-quote/{order_id}",
            {"order_id": s},
            ["order_id"],
        ),
        tool(
            "refund.create",
            "Create a refund (policy checks apply; >500 needs approval_id).",
            "POST",
            "/refunds",
            {"order_id": s, "amount": n, "approval_id": s},
            ["order_id", "amount"],
        ),
        tool(
            "refund.status",
            "Fetch one refund by id.",
            "GET",
            "/refunds/{refund_id}",
            {"refund_id": s},
            ["refund_id"],
        ),
        tool(
            "ticket.search",
            "List tickets, optionally for one customer.",
            "GET",
            "/tickets?customer_id={customer_id}",
            {"customer_id": s},
            ["customer_id"],
        ),
        tool(
            "ticket.get",
            "Fetch one ticket by id.",
            "GET",
            "/tickets/{ticket_id}",
            {"ticket_id": s},
            ["ticket_id"],
        ),
        tool(
            "ticket.update",
            "Update ticket priority or assignee.",
            "PATCH",
            "/tickets/{ticket_id}",
            {"ticket_id": s, "priority": s, "assignee": s},
            ["ticket_id"],
        ),
        tool(
            "ticket.close",
            "Close a ticket.",
            "POST",
            "/tickets/{ticket_id}/close",
            {"ticket_id": s},
            ["ticket_id"],
        ),
        tool(
            "knowledge.search",
            "Search the knowledge base.",
            "GET",
            "/knowledge?query={query}",
            {"query": s},
            ["query"],
        ),
        tool(
            "knowledge.get",
            "Fetch one knowledge entry.",
            "GET",
            "/knowledge/{knowledge_id}",
            {"knowledge_id": s},
            ["knowledge_id"],
        ),
        tool(
            "email.send",
            "Send a templated email to a customer.",
            "POST",
            "/emails",
            {"customer_id": s, "template": s, "vars": {"type": "object"}},
            ["customer_id", "template"],
        ),
        tool(
            "calendar.create",
            "Create a calendar event.",
            "POST",
            "/calendar",
            {"owner": s, "title": s, "start": s, "end": s},
            ["owner", "title", "start", "end"],
        ),
        tool(
            "approval.request",
            "Request approval for a high-amount refund.",
            "POST",
            "/approvals",
            {"order_id": s, "amount": n, "reason": s},
            ["order_id", "amount", "reason"],
        ),
        tool(
            "approval.status",
            "Fetch one approval by id.",
            "GET",
            "/approvals/{approval_id}",
            {"approval_id": s},
            ["approval_id"],
        ),
        tool(
            "human.escalate",
            "Escalate the case to a human.",
            "POST",
            "/escalations",
            {"reason": s, "customer_id": s},
            ["reason", "customer_id"],
        ),
        tool(
            "report.generate",
            "Generate a billing report (slow, often unavailable).",
            "POST",
            "/reports",
            {"order_id": s},
            ["order_id"],
        ),
    ]
