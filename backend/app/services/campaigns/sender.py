"""Sending one approved template to one contact (Feature 011): campaigns and follow-ups.

The template is written into the contact's conversation as an outbound `template` message
with the text the customer reads, so the inbox shows it and the assistant sees it when the
customer answers (C-109). The thread it goes to:

* the open conversation, if the customer wrote in the last 24 hours (they are talking);
* otherwise a new one, marked with the campaign; the old one is closed. When the customer
  answers, that thread continues (`ChannelEventProcessor._resolve_conversation`).

Templates are the one thing allowed outside the 24-hour window, so nothing here checks it.
"""

from __future__ import annotations

import logging
import re
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.business import BusinessProfile
from app.models.campaigns import MessageTemplate
from app.models.channel import Contact
from app.models.conversation import Conversation, Message
from app.models.crm import Lead
from app.models.media import MediaAsset
from app.models.orders import Order, OrderItem
from app.models.tenant import Tenant
from app.models.whatsapp import WhatsAppNumber
from app.providers.storage.base import StorageProvider
from app.providers.whatsapp.base import (
    NumberCredentials,
    Recipient,
    WhatsAppError,
    WhatsAppProvider,
)
from app.repositories.channel import MessageStatusEventRepository, session_window_is_open
from app.repositories.conversation import ConversationRepository, MessageRepository
from app.repositories.whatsapp import WhatsAppNumberRepository
from app.services.campaigns import CampaignError
from app.services.connection import read_credentials

logger = logging.getLogger(__name__)

PLACEHOLDER = re.compile(r"\{\{\s*([A-Za-z_]+)\s*\}\}")
# A first name that is a word, not a WhatsApp label full of emojis.
_WORD = re.compile(r"^[A-Za-zÁÉÍÓÚÜÑáéíóúüñ]{2,30}$")
# A header image link lives this long; Meta fetches it right away.
LINK_SECONDS = 24 * 3600

# Why a recipient was left out (C-004), in the words the report shows.
SKIP_REASONS = {
    "baja": "Se dio de baja",
    "sin_permiso": "No aceptó recibir promociones",
    "sin_nombre": "No sabemos su nombre",
    "sin_producto": "No sabemos qué producto le interesa",
    "sin_telefono": "Sin número de WhatsApp",
    "envio_fallido": "WhatsApp no aceptó el mensaje",
    "cancelada": "Se canceló la campaña",
    # Meta's cap: about two marketing templates per person a day, across businesses (131049).
    "tope_meta": "WhatsApp limitó los mensajes de marketing a esta persona hoy",
}
# The error Meta returns when that cap is hit.
META_MARKETING_CAP = "131049"


def connected_number(
    session: Session, tenant_id: uuid.UUID
) -> tuple[WhatsAppNumber, NumberCredentials]:
    numbers = WhatsAppNumberRepository(session, tenant_id).list_connected()
    credentials = read_credentials(numbers[0]) if numbers else None
    if not numbers or credentials is None:
        raise CampaignError(
            "sin_numero", "Conecta tu número de WhatsApp para usar plantillas.", status=409
        )
    return numbers[0], credentials


def business_name(session: Session, tenant_id: uuid.UUID) -> str:
    profile = session.get(BusinessProfile, tenant_id)
    if profile is not None and profile.business_name:
        return profile.business_name.strip()
    tenant = session.get(Tenant, tenant_id)
    return tenant.name if tenant is not None else "el negocio"


def first_name(contact: Contact) -> str | None:
    data = contact.custom_data if isinstance(contact.custom_data, dict) else {}
    given = data.get("nombres")
    if isinstance(given, str) and given.strip():
        return given.strip().split()[0].capitalize()
    label = (contact.profile_name or "").strip().split()
    if label and _WORD.match(label[0]):
        return label[0].capitalize()
    return None


def product_for(session: Session, tenant_id: uuid.UUID, contact_id: uuid.UUID) -> str | None:
    """What the contact was interested in (their lead) or last ordered."""
    interest = session.execute(
        select(Lead.product_interest)
        .where(
            Lead.tenant_id == tenant_id,
            Lead.contact_id == contact_id,
            Lead.product_interest.is_not(None),
        )
        .order_by(Lead.created_at.desc())
        .limit(1)
    ).scalar_one_or_none()
    if interest:
        return str(interest).split(",")[0].strip()
    item = session.execute(
        select(OrderItem.nombre)
        .join(Order, Order.id == OrderItem.order_id)
        .where(
            Order.tenant_id == tenant_id,
            Order.contact_id == contact_id,
            Order.is_sandbox.is_(False),
        )
        .order_by(Order.created_at.desc())
        .limit(1)
    ).scalar_one_or_none()
    return str(item) if item else None


@dataclass(frozen=True)
class Filled:
    """The values for one contact, or why they cannot get the template."""

    values: list[str]
    text: str
    skip: str | None = None


def fill(
    session: Session, tenant_id: uuid.UUID, template: MessageTemplate, contact: Contact
) -> Filled:
    known: dict[str, str | None] = {}
    for name in template.variables:
        if name == "nombre":
            known[name] = first_name(contact)
        elif name == "negocio":
            known[name] = business_name(session, tenant_id)
        elif name == "producto":
            known[name] = product_for(session, tenant_id, contact.id)
    for name in template.variables:
        if not known.get(name):
            return Filled([], "", skip=f"sin_{name}")
    values = [str(known[name]) for name in template.variables]
    text = PLACEHOLDER.sub(lambda m: str(known.get(m.group(1), m.group(0))), template.body)
    return Filled(values, text)


def header_link(
    session: Session,
    tenant_id: uuid.UUID,
    template: MessageTemplate,
    storage: StorageProvider | None,
) -> str | None:
    if template.header_media_asset_id is None or storage is None:
        return None
    asset = session.get(MediaAsset, template.header_media_asset_id)
    if asset is None or asset.tenant_id != tenant_id:
        return None
    return storage.presigned_url(asset.storage_key, expires_in=LINK_SECONDS)


class TemplateSender:
    """Writes and sends one template message; the caller commits."""

    def __init__(
        self,
        session: Session,
        tenant_id: uuid.UUID,
        *,
        provider: WhatsAppProvider,
        storage: StorageProvider | None = None,
        now: datetime | None = None,
    ) -> None:
        self._session = session
        self._tenant_id = tenant_id
        self._provider = provider
        self._storage = storage
        self._now = now or datetime.now(UTC)
        self._messages = MessageRepository(session, tenant_id)
        self._conversations = ConversationRepository(session, tenant_id)
        self._number: tuple[WhatsAppNumber, NumberCredentials] | None = None
        # Why the last send failed, for the campaign's report.
        self.last_error: str | None = None

    def thread_for(self, contact: Contact, campaign_id: uuid.UUID | None) -> Conversation:
        open_thread = self._conversations.open_for_contact(contact.id)
        if open_thread is not None and session_window_is_open(contact, now=self._now):
            return open_thread
        if open_thread is not None:
            self._conversations.update(open_thread.id, status="closed")
        return self._conversations.add(
            channel="whatsapp",
            contact_id=contact.id,
            status="open",
            origin_campaign_id=campaign_id,
        )

    async def send(
        self,
        contact: Contact,
        template: MessageTemplate,
        filled: Filled,
        *,
        kind: str,
        campaign_id: uuid.UUID | None = None,
        conversation: Conversation | None = None,
    ) -> Message:
        """Send; the message ends `sent` or `failed`, never raises for the provider."""
        if self._number is None:
            self._number = connected_number(self._session, self._tenant_id)
        number, credentials = self._number
        thread = conversation or self.thread_for(contact, campaign_id)
        link = header_link(self._session, self._tenant_id, template, self._storage)
        payload: dict[str, Any] = {
            "tipo": "plantilla",
            "motivo": kind,
            "plantilla": template.name,
            "plantilla_id": str(template.id),
            "idioma": template.language,
            "valores": filled.values,
            "imagen": bool(link),
        }
        if campaign_id is not None:
            payload["campana_id"] = str(campaign_id)
        message = self._messages.append(
            conversation_id=thread.id,
            direction="outbound",
            author="system",
            content=filled.text,
            msg_type="template",
            delivery_status="queued",
            payload=payload,
        )
        try:
            result = await self._provider.send_template(
                credentials,
                sender=number.phone_e164,
                to=Recipient(phone=contact.phone_e164, bsuid=contact.external_user_id),
                name=template.name,
                language=template.language,
                values=filled.values,
                header_image_link=link,
            )
        except WhatsAppError as exc:
            self.last_error = str(exc)
            logger.warning(
                "a template could not be sent",
                extra={"tenant_id": str(self._tenant_id), "reason": str(exc)[:200]},
            )
            self._messages.set_delivery_status(message, status="failed")
            MessageStatusEventRepository(self._session, self._tenant_id).append(
                message.id, status="failed"
            )
            return message
        message.provider_message_id = result.provider_message_id
        self._messages.set_delivery_status(message, status=result.status or "sent")
        MessageStatusEventRepository(self._session, self._tenant_id).append(
            message.id, status=message.delivery_status or "sent"
        )
        return message
