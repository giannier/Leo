"""The YCloud adapter: send a message, read a webhook, check a signature.

Replaces the `ManualYCloudProvider` of Feature 001, which validated pasted
credentials and could not send. Connecting a number is still manual - an admin
does the console work by hand and pastes the identifiers in - but from here on
the platform talks to YCloud's API for everything that happens afterwards.

Plain `httpx` rather than YCloud's SDK, for the same reason as the LLM
adapter: the payloads are ordinary JSON, and a dependency whose only job is to
build that JSON is not worth the supply-chain surface.

**The parser is deliberately forgiving.** `plan.md`'s risk table says it in
one line - "payloads de YCloud distintos a la doc" - and the answer there is
not to guess harder. Anything unrecognised becomes an `unsupported` message
rather than an exception, because the raw payload is already stored in
`webhook_events` and can be replayed once the parser learns the shape. A
strict parser turns a vendor's undocumented field into a customer's message
that never arrives.

Wire format, verified against YCloud's own documentation:

* send: ``POST https://api.ycloud.com/v2/whatsapp/messages`` with an
  ``X-API-Key`` header and ``{"from", "to", "type": "text", "text": {"body"}}``
* webhook: ``{"id", "type", "apiVersion", "createTime", <event object>}`` where
  the event object is ``whatsappInboundMessage`` for
  ``whatsapp.inbound_message.received`` and ``whatsappMessage`` for
  ``whatsapp.message.updated``
* signature: ``YCloud-Signature: t=<unix seconds>,s=<hex>`` over
  ``"<t>.<raw body>"``, HMAC-SHA256
"""

from __future__ import annotations

import hashlib
import hmac
import logging
from datetime import UTC, datetime
from typing import Any

import httpx

from app.providers.whatsapp.base import (
    InboundMessage,
    InvalidCredentials,
    NumberCredentials,
    ParsedEvent,
    QualityUpdate,
    SendResult,
    StatusUpdate,
    WhatsAppAuthError,
    WhatsAppError,
    WhatsAppRateLimited,
    WhatsAppTransientError,
)

logger = logging.getLogger(__name__)

API_BASE_URL = "https://api.ycloud.com/v2"
MESSAGES_PATH = "/whatsapp/messages"

# 20 seconds. A customer is watching a typing indicator, and past that the
# retry is a better answer than continuing to wait for this attempt.
DEFAULT_TIMEOUT = httpx.Timeout(20.0, connect=10.0)

# The identifiers YCloud issues per number. Named here rather than accepted
# blindly so a paste that is missing half of them fails at the admin's screen
# rather than at the first customer message.
REQUIRED_CREDENTIAL_KEYS = ("waba_id", "phone_number_id", "api_key")

MIN_CREDENTIAL_LENGTH = 6

EVENT_INBOUND = "whatsapp.inbound_message.received"
EVENT_MESSAGE_UPDATED = "whatsapp.message.updated"
EVENT_QUALITY_UPDATED = "whatsapp.business_account.phone_number.quality.updated"

# YCloud's message types, mapped onto ours. Everything absent from this table
# - video, interactive, reaction, an order, something introduced next month -
# becomes `unsupported`: stored, answered with the tenant's fallback, and
# counted as an interaction for the 24-hour window (C-001).
INBOUND_TYPES: dict[str, str] = {
    "text": "text",
    "image": "image",
    "document": "document",
    "audio": "audio",
    "voice": "audio",
    "sticker": "sticker",
    "location": "location",
    "contacts": "contact",
}

# YCloud reports `failed | sent | delivered | read`, which is our vocabulary
# already. Listed anyway so an unexpected value is dropped rather than written
# into a column with a CHECK constraint that would reject the whole
# transaction.
DELIVERY_STATUSES: dict[str, str] = {
    "sent": "sent",
    "delivered": "delivered",
    "read": "read",
    "failed": "failed",
}


class YCloudProvider:
    """WhatsApp through YCloud."""

    def __init__(
        self,
        *,
        base_url: str = API_BASE_URL,
        timeout: httpx.Timeout = DEFAULT_TIMEOUT,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._timeout = timeout
        # `None` is httpx's own default, so production behaviour is untouched.
        # The suite passes a `MockTransport`, which is the only way to assert
        # on the request this builds without sending a real WhatsApp message
        # to a real person.
        self._transport = transport

    @property
    def name(self) -> str:
        return "ycloud"

    # ---------------------------------------------------------- credentials

    def validate(self, credentials: NumberCredentials) -> None:
        if credentials.provider != self.name:
            raise InvalidCredentials(
                f"expected provider {self.name!r}, got {credentials.provider!r}"
            )

        missing = [key for key in REQUIRED_CREDENTIAL_KEYS if not credentials.values.get(key)]
        if missing:
            raise InvalidCredentials(f"missing credentials: {', '.join(missing)}")

        # A placeholder left in a form is the realistic failure here, not a
        # malformed token: somebody tabs through the fields and saves "TODO".
        short = [
            key
            for key in REQUIRED_CREDENTIAL_KEYS
            if len(credentials.values[key].strip()) < MIN_CREDENTIAL_LENGTH
        ]
        if short:
            raise InvalidCredentials(f"implausibly short credentials: {', '.join(short)}")

    # --------------------------------------------------------------- sending

    async def send_text(
        self,
        credentials: NumberCredentials,
        *,
        sender: str,
        to: str,
        body: str,
    ) -> SendResult:
        self.validate(credentials)
        payload = {
            "from": sender,
            "to": to,
            "type": "text",
            "text": {"body": body},
        }
        answer = await self._post(MESSAGES_PATH, payload, api_key=credentials.values["api_key"])

        message_id = answer.get("id")
        if not message_id:
            # Without it every later delivery webhook has nothing to attach
            # itself to, so an accepted send with no id is not a success.
            raise WhatsAppError("YCloud accepted the message but returned no id")
        return SendResult(
            provider_message_id=str(message_id),
            status=DELIVERY_STATUSES.get(str(answer.get("status") or ""), "sent"),
        )

    async def _post(self, path: str, payload: dict[str, Any], *, api_key: str) -> dict[str, Any]:
        url = f"{self._base_url}{path}"
        headers = {
            "X-API-Key": api_key,
            "Content-Type": "application/json",
            "Accept": "application/json",
        }
        try:
            async with httpx.AsyncClient(
                timeout=self._timeout, transport=self._transport
            ) as client:
                response = await client.post(url, headers=headers, json=payload)
        except httpx.TimeoutException as exc:
            raise WhatsAppTransientError(f"YCloud timed out after {self._timeout.read}s") from exc
        except httpx.HTTPError as exc:
            raise WhatsAppTransientError(f"could not reach YCloud: {exc}") from exc

        self._raise_for_status(response)

        try:
            body: dict[str, Any] = response.json()
        except ValueError as exc:
            raise WhatsAppTransientError("YCloud returned a non-JSON body") from exc
        return body

    def _raise_for_status(self, response: httpx.Response) -> None:
        """Turn an HTTP status into the retry decision the caller needs."""
        if response.status_code in (401, 403):
            # The body is deliberately not included: a gateway that rejects a
            # key sometimes echoes it back, and this message goes to a log.
            raise WhatsAppAuthError(f"YCloud rejected the credential (HTTP {response.status_code})")
        if response.status_code == 429:
            raise WhatsAppRateLimited("YCloud is throttling this number")
        if response.status_code >= 500:
            raise WhatsAppTransientError(f"YCloud returned HTTP {response.status_code}")
        if response.status_code >= 400:
            # A 4xx is our mistake - a malformed number, a message outside the
            # session window - and sending the identical request again will
            # produce the identical refusal.
            raise WhatsAppError(
                f"YCloud refused the message (HTTP {response.status_code}): {response.text[:200]}"
            )

    # ------------------------------------------------------------- webhooks

    def verify_signature(self, *, raw_body: bytes, header: str | None, secret: str) -> bool:
        """Whether this delivery was signed by YCloud.

        `YCloud-Signature: t=<unix seconds>,s=<hex>` over `"<t>.<raw body>"`.

        The **raw** body, before any JSON parsing: re-serialising the parsed
        object changes key order and whitespace, and the signature is over
        bytes. This is the single most common way signature verification is
        written so that it never rejects anything, because a mismatch then
        looks like a configuration problem and gets "fixed" by removing it.
        """
        if not header or not secret:
            return False
        parts = dict(piece.split("=", 1) for piece in header.split(",") if "=" in piece)
        timestamp = parts.get("t")
        signature = parts.get("s")
        if not timestamp or not signature:
            return False

        signed = f"{timestamp}.".encode() + raw_body
        expected = hmac.new(secret.encode(), signed, hashlib.sha256).hexdigest()
        # Constant time: a plain `==` leaks how much of a forged signature was
        # correct, one byte at a time.
        return hmac.compare_digest(expected, signature)

    def event_id(self, payload: dict[str, object]) -> str | None:
        """YCloud's own id for this delivery.

        The whole idempotency story rests on it, so an event without one is
        rejected at the door rather than stored under an invented id that a
        retry would never match.
        """
        raw = payload.get("id")
        return str(raw) if raw else None

    def parse_event(self, payload: dict[str, object]) -> ParsedEvent:
        event_type = str(payload.get("type") or "")
        if event_type == EVENT_INBOUND:
            return self._parse_inbound(_as_dict(payload.get("whatsappInboundMessage")))
        if event_type == EVENT_MESSAGE_UPDATED:
            return self._parse_status(_as_dict(payload.get("whatsappMessage")))
        if event_type == EVENT_QUALITY_UPDATED:
            return self._parse_quality(payload)
        # Templates, payments, whatever YCloud adds next. Not an error, and
        # explicitly not a retry: the event is complete, it is simply not
        # addressed to any behaviour this version has.
        logger.debug("ignoring YCloud event", extra={"event_type": event_type})
        return None

    def _parse_inbound(self, message: dict[str, Any]) -> InboundMessage | None:
        provider_message_id = str(message.get("id") or "")
        from_phone = str(message.get("from") or "")
        to_phone = str(message.get("to") or "")
        if not (provider_message_id and from_phone and to_phone):
            # Not recoverable by replaying: without these three there is no
            # message, no sender and no tenant to attribute it to.
            logger.warning(
                "inbound YCloud message is missing an identifier",
                extra={"has_id": bool(provider_message_id), "has_from": bool(from_phone)},
            )
            return None

        raw_type = str(message.get("type") or "")
        msg_type = INBOUND_TYPES.get(raw_type, "unsupported")
        if msg_type == "unsupported":
            # Worth a line: this is how a new WhatsApp message type announces
            # itself, and the tenant is meanwhile answering it with a fallback.
            logger.info("unsupported inbound message type", extra={"ycloud_type": raw_type})

        text = None
        if msg_type == "text":
            text = str(_as_dict(message.get("text")).get("body") or "") or None

        profile = _as_dict(message.get("customerProfile"))
        return InboundMessage(
            provider_message_id=provider_message_id,
            from_phone=from_phone,
            to_phone=to_phone,
            profile_name=str(profile.get("name") or "") or None,
            msg_type=msg_type,
            text=text,
            # `sendTime` is what the webhook examples carry and `timestamp`
            # is what the schema calls it. Both are accepted rather than
            # betting the 24-hour window on which one turns up.
            sent_at=_parse_time(message.get("sendTime") or message.get("timestamp")),
        )

    def _parse_status(self, message: dict[str, Any]) -> StatusUpdate | None:
        provider_message_id = str(message.get("id") or "")
        status = DELIVERY_STATUSES.get(str(message.get("status") or ""))
        if not provider_message_id or status is None:
            logger.warning(
                "unusable YCloud status update",
                extra={"status": message.get("status"), "has_id": bool(provider_message_id)},
            )
            return None

        error = _as_dict(message.get("error"))
        return StatusUpdate(
            provider_message_id=provider_message_id,
            status=status,
            occurred_at=_parse_time(message.get("updateTime") or message.get("createTime")),
            # Only the human-readable half. The vendor's numeric codes belong
            # in the raw event, which is stored whole.
            error=str(error.get("message") or "") or None if error else None,
        )

    def _parse_quality(self, payload: dict[str, object]) -> QualityUpdate | None:
        number = _as_dict(payload.get("whatsappPhoneNumber"))
        phone = str(number.get("phoneNumber") or "")
        rating = str(number.get("qualityRating") or "")
        if not phone or not rating:
            return None
        return QualityUpdate(phone_e164=phone, quality_rating=rating.upper())


def _as_dict(value: object) -> dict[str, Any]:
    """A nested object, or an empty one. Never an exception.

    Half the parser's tolerance lives here: a vendor sending `null` where an
    object was documented is a Tuesday, and it must not take a customer's
    message down with it.
    """
    return value if isinstance(value, dict) else {}


def _parse_time(value: object) -> datetime:
    """An RFC 3339 timestamp, falling back to now.

    Falling back rather than raising is the right trade for the one thing
    this feeds: `last_inbound_at`, which decides whether the business may
    reply at all. A missing timestamp would otherwise close the 24-hour window
    on a message that just arrived.
    """
    if isinstance(value, str) and value:
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            logger.warning("unparseable YCloud timestamp", extra={"value": value[:64]})
        else:
            return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)
    return datetime.now(UTC)
