"""Resend adapter for transactional email.

Approved by Giannier; recorded as an Open Question in plan.md until then.
Plain HTTP rather than the `resend` SDK - the API is a single POST, and the
SDK would add a dependency for JSON serialisation we already do.
"""

from __future__ import annotations

from typing import Any

import httpx

from app.providers.email.base import (
    EmailAuthError,
    EmailError,
    EmailMessage,
)

RESEND_URL = "https://api.resend.com/emails"
DEFAULT_TIMEOUT = httpx.Timeout(30.0, connect=10.0)


class ResendEmailProvider:
    """Sends through Resend's HTTP API."""

    def __init__(
        self,
        *,
        api_key: str,
        sender: str,
        timeout: httpx.Timeout = DEFAULT_TIMEOUT,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._api_key = api_key
        self._sender = sender
        self._timeout = timeout
        # `None` is httpx's own default, so nothing changes in production.
        # The suite passes a `MockTransport`, which is the only way to check
        # the payload this builds without sending real mail to a real address.
        self._transport = transport

    async def send(self, message: EmailMessage) -> str:
        payload: dict[str, Any] = {
            "from": self._sender,
            "to": list(message.to),
            "subject": message.subject,
            "html": message.html,
        }
        if message.text:
            payload["text"] = message.text
        if message.reply_to:
            payload["reply_to"] = message.reply_to

        try:
            async with httpx.AsyncClient(
                timeout=self._timeout, transport=self._transport
            ) as client:
                response = await client.post(
                    RESEND_URL,
                    headers={
                        "Authorization": f"Bearer {self._api_key}",
                        "Content-Type": "application/json",
                    },
                    json=payload,
                )
        except httpx.HTTPError as exc:
            raise EmailError(f"could not reach Resend: {exc}") from exc

        if response.status_code in (401, 403):
            raise EmailAuthError(f"Resend rejected the API key (HTTP {response.status_code})")
        if response.status_code >= 400:
            raise EmailError(f"Resend returned HTTP {response.status_code}: {response.text[:200]}")

        try:
            body = response.json()
        except ValueError as exc:
            raise EmailError("Resend returned a non-JSON body") from exc

        message_id = body.get("id")
        if not message_id:
            raise EmailError(f"Resend accepted the request but returned no id: {body!r}")
        return str(message_id)
