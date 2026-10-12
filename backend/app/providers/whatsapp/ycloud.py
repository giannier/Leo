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
  ``X-API-Key`` header and ``{"from", "to", "type": "text", "text": {"body"}}``;
  buttons and lists are ``type: "interactive"`` (Meta's object), media is
  ``type: "image" | "document" | "video" | "audio"`` with an ``id`` or a
  ``link``. A customer with no phone is addressed with ``recipient`` (their
  BSUID) instead of ``to``
* upload: ``POST /v2/whatsapp/media/{phoneNumber}/upload``, multipart, one
  ``file``; the answer carries the media ``id`` and YCloud keeps the file 30 days
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
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import quote

import httpx

from app.providers.whatsapp.base import (
    Button,
    InboundMessage,
    InteractiveReply,
    InvalidCredentials,
    ListSection,
    MediaRef,
    NumberCredentials,
    ParsedEvent,
    QualityUpdate,
    Recipient,
    Referral,
    SendResult,
    StatusUpdate,
    UploadedMedia,
    WhatsAppAuthError,
    WhatsAppError,
    WhatsAppRateLimited,
    WhatsAppTransientError,
)

logger = logging.getLogger(__name__)

API_BASE_URL = "https://api.ycloud.com/v2"
MESSAGES_PATH = "/whatsapp/messages"
MEDIA_UPLOAD_PATH = "/whatsapp/media/{phone}/upload"

# Uploading a file is slower than posting a few hundred bytes of JSON.
UPLOAD_TIMEOUT = httpx.Timeout(60.0, connect=10.0)

# YCloud (through Meta) keeps an uploaded file for 30 days.
MEDIA_LIFETIME = timedelta(days=30)

# WhatsApp's own limits. The tools truncate before sending; these are the last
# line, so a request Meta would refuse never leaves the building.
MAX_BUTTONS = 3
MAX_LIST_ROWS = 10
MAX_BUTTON_TITLE = 20
MAX_ROW_TITLE = 24
MAX_ROW_DESCRIPTION = 72
MAX_BODY = 1024
MAX_HEADER_FOOTER = 60
MAX_LIST_BUTTON = 20

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
# - video, reaction, an order, something introduced next month (and an
# interactive message that is not a button or list reply) -
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
        to: Recipient,
        body: str,
    ) -> SendResult:
        return await self._send(credentials, sender, to, {"type": "text", "text": {"body": body}})

    async def send_media(
        self,
        credentials: NumberCredentials,
        *,
        sender: str,
        to: Recipient,
        media: MediaRef,
        caption: str | None = None,
    ) -> SendResult:
        body: dict[str, Any] = {"id": media.media_id} if media.media_id else {"link": media.link}
        # WhatsApp accepts a caption on everything but audio.
        if caption and media.kind != "audio":
            body["caption"] = caption
        if media.kind == "document" and media.filename:
            body["filename"] = media.filename
        return await self._send(credentials, sender, to, {"type": media.kind, media.kind: body})

    async def send_buttons(
        self,
        credentials: NumberCredentials,
        *,
        sender: str,
        to: Recipient,
        body: str,
        buttons: list[Button],
        header: str | None = None,
        footer: str | None = None,
    ) -> SendResult:
        if not 1 <= len(buttons) <= MAX_BUTTONS:
            raise WhatsAppError(f"a message carries 1 to {MAX_BUTTONS} buttons, got {len(buttons)}")
        _check_length("body", body, MAX_BODY)
        for button in buttons:
            _check_length("button title", button.title, MAX_BUTTON_TITLE)
        interactive: dict[str, Any] = {
            "type": "button",
            "body": {"text": body},
            "action": {
                "buttons": [
                    {"type": "reply", "reply": {"id": button.id, "title": button.title}}
                    for button in buttons
                ]
            },
        }
        if header:
            _check_length("header", header, MAX_HEADER_FOOTER)
            interactive["header"] = {"type": "text", "text": header}
        if footer:
            _check_length("footer", footer, MAX_HEADER_FOOTER)
            interactive["footer"] = {"text": footer}
        return await self._send(
            credentials, sender, to, {"type": "interactive", "interactive": interactive}
        )

    async def send_list(
        self,
        credentials: NumberCredentials,
        *,
        sender: str,
        to: Recipient,
        body: str,
        button_text: str,
        sections: list[ListSection],
    ) -> SendResult:
        total = sum(len(section.rows) for section in sections)
        if not 1 <= total <= MAX_LIST_ROWS:
            raise WhatsAppError(f"a list carries 1 to {MAX_LIST_ROWS} rows, got {total}")
        _check_length("body", body, MAX_BODY)
        _check_length("list button", button_text, MAX_LIST_BUTTON)
        wire_sections: list[dict[str, Any]] = []
        for section in sections:
            rows: list[dict[str, Any]] = []
            for row in section.rows:
                _check_length("row title", row.title, MAX_ROW_TITLE)
                wire_row: dict[str, Any] = {"id": row.id, "title": row.title}
                if row.description:
                    _check_length("row description", row.description, MAX_ROW_DESCRIPTION)
                    wire_row["description"] = row.description
                rows.append(wire_row)
            wire_section: dict[str, Any] = {"rows": rows}
            if section.title:
                wire_section["title"] = section.title
            wire_sections.append(wire_section)
        interactive = {
            "type": "list",
            "body": {"text": body},
            "action": {"button": button_text, "sections": wire_sections},
        }
        return await self._send(
            credentials, sender, to, {"type": "interactive", "interactive": interactive}
        )

    async def upload_media(
        self,
        credentials: NumberCredentials,
        *,
        sender: str,
        data: bytes,
        mime: str,
        filename: str,
    ) -> UploadedMedia:
        self.validate(credentials)
        # The sender goes in the path as E.164; the `+` must be encoded or it
        # reads as a space.
        path = MEDIA_UPLOAD_PATH.format(phone=quote(sender, safe=""))
        answer = await self._request(
            path,
            api_key=credentials.values["api_key"],
            files={"file": (filename, data, mime)},
            limit=UPLOAD_TIMEOUT,
        )
        media_id = answer.get("id")
        if not media_id:
            raise WhatsAppError("YCloud accepted the upload but returned no media id")
        return UploadedMedia(media_id=str(media_id), expires_at=datetime.now(UTC) + MEDIA_LIFETIME)

    async def _send(
        self,
        credentials: NumberCredentials,
        sender: str,
        to: Recipient,
        content: dict[str, Any],
    ) -> SendResult:
        self.validate(credentials)
        payload: dict[str, Any] = {"from": sender, **_address(to), **content}
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
        return await self._request(path, api_key=api_key, json=payload)

    async def _request(
        self,
        path: str,
        *,
        api_key: str,
        json: dict[str, Any] | None = None,
        files: dict[str, tuple[str, bytes, str]] | None = None,
        limit: httpx.Timeout | None = None,
    ) -> dict[str, Any]:
        url = f"{self._base_url}{path}"
        headers = {"X-API-Key": api_key, "Accept": "application/json"}
        if files is None:
            # With `files`, httpx sets the multipart boundary itself.
            headers["Content-Type"] = "application/json"
        limit = limit or self._timeout
        try:
            async with httpx.AsyncClient(timeout=limit, transport=self._transport) as client:
                response = await client.post(url, headers=headers, json=json, files=files)
        except httpx.TimeoutException as exc:
            raise WhatsAppTransientError(f"YCloud timed out after {limit.read}s") from exc
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
        # Since the BSUID rollout `from` "can be omitted": a customer who has
        # not shared their number is identified by `fromUserId` alone.
        from_phone = str(message.get("from") or "") or None
        from_user_id = str(message.get("fromUserId") or "") or None
        to_phone = str(message.get("to") or "")
        if not (provider_message_id and (from_phone or from_user_id) and to_phone):
            # Not recoverable by replaying: without these there is no message,
            # no sender and no tenant to attribute it to.
            logger.warning(
                "inbound YCloud message is missing an identifier",
                extra={
                    "has_id": bool(provider_message_id),
                    "has_from": bool(from_phone or from_user_id),
                },
            )
            return None

        raw_type = str(message.get("type") or "")
        interactive = _parse_interactive(message) if raw_type == "interactive" else None
        # A tap is stored as a text message carrying the title the customer
        # saw; the id travels beside it for the process engine.
        msg_type = "text" if interactive else INBOUND_TYPES.get(raw_type, "unsupported")
        if msg_type == "unsupported":
            # Worth a line: this is how a new WhatsApp message type announces
            # itself, and the tenant is meanwhile answering it with a fallback.
            logger.info("unsupported inbound message type", extra={"ycloud_type": raw_type})

        text = None
        if interactive is not None:
            text = interactive.title or None
        elif msg_type == "text":
            text = str(_as_dict(message.get("text")).get("body") or "") or None

        profile = _as_dict(message.get("customerProfile"))
        return InboundMessage(
            provider_message_id=provider_message_id,
            from_phone=from_phone,
            from_user_id=from_user_id,
            to_phone=to_phone,
            profile_name=str(profile.get("name") or "") or None,
            msg_type=msg_type,
            text=text,
            interactive=interactive,
            referral=_parse_referral(message.get("referral")),
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


def _address(to: Recipient) -> dict[str, str]:
    """`to` for a phone, `recipient` for a BSUID.

    YCloud uses `to` and ignores `recipient` when both are present, so the
    phone is sent whenever there is one and the BSUID only when there is not.
    """
    if to.phone:
        return {"to": to.phone}
    # `Recipient` guarantees one of the two is set.
    return {"recipient": to.bsuid or ""}


def _check_length(what: str, value: str, limit: int) -> None:
    if len(value) > limit:
        raise WhatsAppError(f"{what} is {len(value)} characters; WhatsApp allows {limit}")


def _parse_interactive(message: dict[str, Any]) -> InteractiveReply | None:
    """The tapped option of a `button_reply` or a `list_reply`, if readable."""
    interactive = _as_dict(message.get("interactive"))
    kind = str(interactive.get("type") or "")
    if kind not in ("button_reply", "list_reply"):
        return None
    reply = _as_dict(interactive.get(kind))
    reply_id = str(reply.get("id") or "")
    if not reply_id:
        return None
    return InteractiveReply(
        id=reply_id,
        title=str(reply.get("title") or ""),
        description=str(reply.get("description") or "") or None,
    )


def _parse_referral(value: object) -> Referral | None:
    """The Click-to-WhatsApp ad a message came from.

    The documented keys are snake_case (`source_url`, `image_url`); camelCase is
    accepted too, since the rest of the envelope is, and which one a given
    deployment sends is not something worth losing an ad attribution over.
    """
    raw = _as_dict(value)
    if not raw:
        return None

    def pick(snake: str, camel: str) -> str | None:
        return str(raw.get(snake) or raw.get(camel) or "") or None

    media_url = pick("image_url", "imageUrl") or pick("video_url", "videoUrl")
    return Referral(
        source_id=pick("source_id", "sourceId"),
        source_type=pick("source_type", "sourceType"),
        source_url=pick("source_url", "sourceUrl"),
        headline=pick("headline", "headline"),
        body=pick("body", "body"),
        media_type=pick("media_type", "mediaType"),
        media_url=media_url,
        ctwa_clid=pick("ctwa_clid", "ctwaClid"),
    )


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
