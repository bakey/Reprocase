"""Support World tool provider: HTTP tools backed by the running api service."""

from __future__ import annotations

import asyncio
import json
import urllib.error
import urllib.request
from collections.abc import Mapping
from typing import Any


class HttpTool:
    def __init__(
        self,
        base_url: str,
        *,
        name: str,
        description: str,
        method: str,
        path_template: str,
        input_schema: dict[str, Any],
    ) -> None:
        self.base_url = base_url
        self.name = name
        self.description = description
        self.method = method
        self.path_template = path_template
        self.input_schema = input_schema

    def _send(self, arguments: Mapping[str, Any]) -> Any:
        path = self.path_template.format(**arguments)
        body = dict(arguments) if self.method == "POST" else None
        request = urllib.request.Request(
            f"{self.base_url}{path}",
            data=json.dumps(body).encode("utf-8") if body is not None else None,
            headers={"Content-Type": "application/json"},
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


def create_tools(environment: Any) -> list[HttpTool]:
    base_url = environment.service_urls["api"]
    return [
        HttpTool(
            base_url,
            name="customer.get",
            description="Fetch one customer by id.",
            method="GET",
            path_template="/customers/{customer_id}",
            input_schema={
                "type": "object",
                "properties": {"customer_id": {"type": "string"}},
                "required": ["customer_id"],
                "additionalProperties": False,
            },
        ),
        HttpTool(
            base_url,
            name="order.get",
            description="Fetch one order by id.",
            method="GET",
            path_template="/orders/{order_id}",
            input_schema={
                "type": "object",
                "properties": {"order_id": {"type": "string"}},
                "required": ["order_id"],
                "additionalProperties": False,
            },
        ),
        HttpTool(
            base_url,
            name="refund.create",
            description="Create a refund for an order.",
            method="POST",
            path_template="/refunds",
            input_schema={
                "type": "object",
                "properties": {
                    "order_id": {"type": "string"},
                    "amount": {"type": "number"},
                },
                "required": ["order_id", "amount"],
                "additionalProperties": False,
            },
        ),
        HttpTool(
            base_url,
            name="email.send",
            description="Send a templated email to a customer.",
            method="POST",
            path_template="/emails",
            input_schema={
                "type": "object",
                "properties": {
                    "customer_id": {"type": "string"},
                    "template": {"type": "string"},
                },
                "required": ["customer_id", "template"],
                "additionalProperties": False,
            },
        ),
    ]
