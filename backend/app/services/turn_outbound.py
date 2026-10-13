"""The two `TurnOutbound` adapters: WhatsApp and the simulator (Feature 004, C-11, C-19).

The sending tools (`enviar_recurso`, `mostrar_opciones`) hand a finished message
to a `TurnOutbound` and learn whether it went. Who is behind it is decided by the
turn pipeline:

* `LiveOutbound` resolves the file, sends it through the provider and writes the
  `messages` row of what went out. It sends *at the moment the tool calls it*, as
  Capacita v21 did, so the order the customer sees is the order the model chose
  ("here is the flyer... which one do you prefer?"). The text of the reply comes
  last, through the usual `queued` row and `ReplySender`.
* `SandboxOutbound` writes the same row and sends nothing (C-19): the simulator
  reads the rows back, so what the owner sees is exactly what a customer would.

Every outbound message is a row in `messages` (C-11): `content` is the readable
text and `payload` the structure (buttons, list rows, the file), which is what the
simulator draws and what the inbox will show. A message that could not be sent
leaves no row: nothing was said, and the tool tells the model so.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from collections.abc import Awaitable, Callable, Sequence
from datetime import UTC, datetime
from typing import Any

from sqlalchemy.orm import Session

from app.agent.outbound import OutboundFailed
from app.models.channel import Contact
from app.models.conversation import Message
from app.models.media import MediaAsset
from app.models.whatsapp import WhatsAppNumber
from app.providers.storage.base import StorageProvider
from app.providers.whatsapp.base import (
    Button,
    ListSection,
    NumberCredentials,
    Recipient,
    SendResult,
    WhatsAppError,
    WhatsAppProvider,
)
from app.repositories.channel import (
    ContactRepository,
    MessageStatusEventRepository,
    session_window_is_open,
)
from app.repositories.conversation import ConversationRepository, MessageRepository
from app.repositories.whatsapp import WhatsAppNumberRepository
from app.services.connection import read_credentials
from app.services.media_delivery import MediaDelivery, MediaUnavailable

logger = logging.getLogger(__name__)

# The message type of each kind of library file.
MESSAGE_TYPE_OF_ASSET = {
    "imagen": "image",
    "documento": "document",
    "video": "video",
    "audio": "audio",
}

# The label the simulator and the inbox draw for each kind of outbound.
KIND_MEDIA = "media"
KIND_BUTTONS = "botones"
KIND_LIST = "lista"


def media_payload(asset: MediaAsset) -> dict[str, Any]:
    return {
        "tipo": KIND_MEDIA,
        "clave": asset.clave,
        "titulo": asset.titulo,
        "media": asset.tipo,
        "caption": asset.caption,
        "asset_id": str(asset.id),
        "mime_type": asset.mime_type,
    }


def media_text(asset: MediaAsset) -> str:
    """The readable line stored for a file: its caption, or what it is."""
    return asset.caption or f"[{asset.tipo}: {asset.titulo}]"


def buttons_payload(body: str, buttons: Sequence[Button]) -> dict[str, Any]:
    return {
        "tipo": KIND_BUTTONS,
        "body": body,
        "botones": [{"id": button.id, "title": button.title} for button in buttons],
    }


def list_payload(body: str, button_text: str, sections: Sequence[ListSection]) -> dict[str, Any]:
    return {
        "tipo": KIND_LIST,
        "body": body,
        "boton": button_text,
        "filas": [
            {"id": row.id, "title": row.title, "description": row.description}
            for section in sections
            for row in section.rows
        ],
    }


class _RowWriter:
    """Writes the `messages` row of an outbound that went out."""

    def __init__(self, session: Session, tenant_id: uuid.UUID, conversation_id: uuid.UUID) -> None:
        self._session = session
        self._tenant_id = tenant_id
        self._conversation_id = conversation_id
        self._messages = MessageRepository(session, tenant_id)
        # What this adapter has written, in order: the pipeline counts them for
        # the trace and the simulator's response.
        self.written: list[Message] = []

    def _write(
        self,
        *,
        msg_type: str,
        content: str,
        payload: dict[str, Any] | None,
        delivery_status: str | None,
        provider_message_id: str | None = None,
    ) -> Message:
        row = self._messages.append(
            conversation_id=self._conversation_id,
            direction="outbound",
            author="agent",
            content=content,
            msg_type=msg_type,
            provider_message_id=provider_message_id,
            delivery_status=delivery_status,
            payload=payload,
        )
        self.written.append(row)
        return row


class SandboxOutbound(_RowWriter):
    """Records what the agent sends in the simulator and sends nothing (C-19)."""

    async def send_text(self, body: str) -> None:
        self._write(msg_type="text", content=body, payload=None, delivery_status=None)

    async def send_media(self, asset: MediaAsset) -> None:
        self._write(
            msg_type=MESSAGE_TYPE_OF_ASSET[asset.tipo],
            content=media_text(asset),
            payload=media_payload(asset),
            delivery_status=None,
        )

    async def send_buttons(self, body: str, buttons: Sequence[Button]) -> None:
        self._write(
            msg_type="interactive",
            content=body,
            payload=buttons_payload(body, buttons),
            delivery_status=None,
        )

    async def send_list(self, body: str, button_text: str, sections: Sequence[ListSection]) -> None:
        self._write(
            msg_type="interactive",
            content=body,
            payload=list_payload(body, button_text, sections),
            delivery_status=None,
        )


def _backoff_seconds(attempt: int) -> float:
    """1 s, then 2 s: a customer is watching the chat, so retries stay short."""
    return float(2 ** (attempt - 1))


class LiveOutbound(_RowWriter):
    """Sends through the provider and records what went out."""

    def __init__(
        self,
        session: Session,
        tenant_id: uuid.UUID,
        conversation_id: uuid.UUID,
        *,
        provider: WhatsAppProvider,
        credentials: NumberCredentials,
        number: WhatsAppNumber,
        contact: Contact,
        media: MediaDelivery,
        max_attempts: int = 1,
        now: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        super().__init__(session, tenant_id, conversation_id)
        self._provider = provider
        self._credentials = credentials
        self._number = number
        self._contact = contact
        self._media = media
        self._max_attempts = max(1, max_attempts)
        self._clock = now

    @classmethod
    def build(
        cls,
        session: Session,
        tenant_id: uuid.UUID,
        conversation_id: uuid.UUID,
        *,
        contact: Contact,
        number: WhatsAppNumber,
        credentials: NumberCredentials,
        provider: WhatsAppProvider,
        storage: StorageProvider,
        max_attempts: int = 1,
    ) -> LiveOutbound:
        return cls(
            session,
            tenant_id,
            conversation_id,
            provider=provider,
            credentials=credentials,
            number=number,
            contact=contact,
            media=MediaDelivery(
                session,
                tenant_id,
                provider=provider,
                storage=storage,
                credentials=credentials,
                sender=number.phone_e164,
            ),
            max_attempts=max_attempts,
        )

    # ------------------------------------------------------------------ sends

    async def send_text(self, body: str) -> None:
        self._require_open_window()
        result = await self._deliver(
            lambda: self._provider.send_text(
                self._credentials, sender=self._number.phone_e164, to=self._recipient(), body=body
            )
        )
        self._recorded(result, msg_type="text", content=body, payload=None)

    async def send_media(self, asset: MediaAsset) -> None:
        self._require_open_window()
        try:
            reference = await self._media.reference_for(asset)
        except MediaUnavailable as exc:
            logger.warning(
                "a library file could not be sent",
                extra={"tenant_id": str(self._tenant_id), "clave": asset.clave},
            )
            raise OutboundFailed(str(exc)) from exc
        result = await self._deliver(
            lambda: self._provider.send_media(
                self._credentials,
                sender=self._number.phone_e164,
                to=self._recipient(),
                media=reference,
                caption=asset.caption,
            )
        )
        self._recorded(
            result,
            msg_type=MESSAGE_TYPE_OF_ASSET[asset.tipo],
            content=media_text(asset),
            payload=media_payload(asset),
        )

    async def send_buttons(self, body: str, buttons: Sequence[Button]) -> None:
        self._require_open_window()
        result = await self._deliver(
            lambda: self._provider.send_buttons(
                self._credentials,
                sender=self._number.phone_e164,
                to=self._recipient(),
                body=body,
                buttons=list(buttons),
            )
        )
        self._recorded(
            result,
            msg_type="interactive",
            content=body,
            payload=buttons_payload(body, buttons),
        )

    async def send_list(self, body: str, button_text: str, sections: Sequence[ListSection]) -> None:
        self._require_open_window()
        result = await self._deliver(
            lambda: self._provider.send_list(
                self._credentials,
                sender=self._number.phone_e164,
                to=self._recipient(),
                body=body,
                button_text=button_text,
                sections=list(sections),
            )
        )
        self._recorded(
            result,
            msg_type="interactive",
            content=body,
            payload=list_payload(body, button_text, sections),
        )

    # ---------------------------------------------------------------- helpers

    def _recipient(self) -> Recipient:
        return Recipient(phone=self._contact.phone_e164, bsuid=self._contact.external_user_id)

    def _require_open_window(self) -> None:
        """Meta carries a free-form message only inside the 24-hour window; the
        same rule that stops the text of a reply (`ReplySender`)."""
        if not session_window_is_open(self._contact, now=self._clock()):
            raise OutboundFailed("the 24-hour session window is closed")

    async def _deliver(self, send: Callable[[], Awaitable[SendResult]]) -> SendResult:
        """One send, retrying only what is worth retrying."""
        last: WhatsAppError | None = None
        for attempt in range(1, self._max_attempts + 1):
            try:
                return await send()
            except WhatsAppError as exc:
                last = exc
                if not exc.retryable:
                    break
                if attempt < self._max_attempts:
                    await asyncio.sleep(_backoff_seconds(attempt))
        logger.error(
            "an outbound message could not be handed to the provider",
            extra={"tenant_id": str(self._tenant_id), "reason": str(last)},
        )
        raise OutboundFailed(str(last)) from last

    def _recorded(
        self, result: SendResult, *, msg_type: str, content: str, payload: dict[str, Any] | None
    ) -> None:
        status = result.status or "sent"
        row = self._write(
            msg_type=msg_type,
            content=content,
            payload=payload,
            delivery_status=status,
            provider_message_id=result.provider_message_id,
        )
        MessageStatusEventRepository(self._session, self._tenant_id).append(row.id, status=status)


def live_outbound_factory(
    session: Session,
    tenant_id: uuid.UUID,
    *,
    provider: WhatsAppProvider,
    storage: StorageProvider | None,
    max_attempts: int = 1,
) -> Callable[[uuid.UUID], LiveOutbound | None]:
    """The factory `ConversationResponder` calls once per turn.

    It answers None when this conversation cannot send - no connected number,
    no credentials, no contact, no object store - and the tools then report
    `no_disponible` instead of failing the turn. The reply itself still gets
    written, so reconnecting the number is what delivers it.
    """

    def build(conversation_id: uuid.UUID) -> LiveOutbound | None:
        if storage is None:
            return None
        conversation = ConversationRepository(session, tenant_id).get(conversation_id)
        if conversation is None or conversation.contact_id is None:
            return None
        contact = ContactRepository(session, tenant_id).get(conversation.contact_id)
        numbers = WhatsAppNumberRepository(session, tenant_id).list_connected()
        if contact is None or not numbers:
            return None
        # One number per tenant in v1 (C-005).
        number = numbers[0]
        credentials = read_credentials(number)
        if credentials is None:
            return None
        return LiveOutbound.build(
            session,
            tenant_id,
            conversation_id,
            contact=contact,
            number=number,
            credentials=credentials,
            provider=provider,
            storage=storage,
            max_attempts=max_attempts,
        )

    return build
