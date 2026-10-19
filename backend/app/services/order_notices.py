"""Telling the customer what the business decided (Feature 004, TASK-430, C-09).

After the operator confirms or rejects an order, the customer gets one of the system
messages of `plantillas.md` section 7 (`pago_confirmado`, `pago_rechazado`,
`pedido_rechazado`), the owner's own wording if they edited it. It is sent *after*
the decision committed, and never makes it fail:

* **Real channel.** If Meta's 24-hour window is closed the message is not sent and
  the provider is not called (C-09): the response says `ventana_cerrada` and the
  business is told so it can reach the customer another way. Otherwise the message
  is written `queued` and handed to `ReplySender`, which retries what is worth
  retrying and records the delivery states.
* **Simulator (C-19).** Nothing goes to the provider; the message is stored as an
  outbound row the simulator reads back with `GET .../messages?after=`. The window is
  always open there.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Literal

from sqlalchemy.orm import Session

from app.models.channel import Contact
from app.models.conversation import Message
from app.models.orders import Order
from app.orders.notices import OrderNotices
from app.orders.review import NoticeKind
from app.process.system_messages import render_system_message
from app.process.templates.base import DEFAULT_SYSTEM_MESSAGES
from app.providers.whatsapp.base import WhatsAppProvider
from app.repositories.business import AgentConfigRepository
from app.repositories.channel import ContactRepository, session_window_is_open
from app.repositories.conversation import ConversationRepository, MessageRepository
from app.services.campaigns.optins import OptinService
from app.services.channel import NoConnectedNumber, ReplySender, SessionWindowClosed

logger = logging.getLogger(__name__)

# What the response of confirm/reject says about the customer's notice.
CustomerNotice = Literal["enviado", "ventana_cerrada", "no_enviado"]


@dataclass(frozen=True)
class PreparedNotice:
    """A notice written in the decision's transaction, still to be handed over."""

    order: Order
    outcome: CustomerNotice
    # The `queued` row to send; None when there is nothing to send now.
    message: Message | None = None
    # After a confirmed payment, the promotions question (Feature 011, C-102).
    extra: Message | None = None


class CustomerNotifier:
    """Sends the operator's decision to the customer, after it committed."""

    def __init__(
        self,
        session: Session,
        tenant_id: uuid.UUID,
        *,
        provider: WhatsAppProvider,
        max_attempts: int = 1,
        notices: OrderNotices | None = None,
    ) -> None:
        self._session = session
        self._tenant_id = tenant_id
        self._provider = provider
        self._max_attempts = max_attempts
        self._notices = notices
        self._messages = MessageRepository(session, tenant_id)

    def text_for(
        self,
        order: Order,
        kind: NoticeKind,
        *,
        contact: Contact | None,
        siguiente_paso: str | None,
        motivo: str | None,
    ) -> str:
        """The wording: the owner's if they edited it, the default otherwise."""
        config = AgentConfigRepository(self._session, self._tenant_id).current()
        edited = (config.system_messages or {}).get(kind) if config is not None else None
        template = edited if isinstance(edited, str) and edited.strip() else None
        return render_system_message(
            template or DEFAULT_SYSTEM_MESSAGES[kind],
            {
                "nombre_cliente": _customer_name(order, contact),
                "numero": str(order.numero),
                "siguiente_paso": siguiente_paso,
                "motivo": motivo,
            },
        )

    def prepare(
        self,
        order: Order,
        kind: NoticeKind,
        *,
        siguiente_paso: str | None = None,
        motivo: str | None = None,
    ) -> PreparedNotice:
        """Write the notice **in the caller's transaction** (finding E-4: outbox).

        Called before the decision commits, so the decision and its notice are
        one commit: if the process dies right after it, the `queued` row is
        there and `retry_stranded_replies` sends it. Nothing is sent here and
        nothing is committed.
        """
        conversation = ConversationRepository(self._session, self._tenant_id).get(
            order.conversation_id
        )
        contact = (
            ContactRepository(self._session, self._tenant_id).get(order.contact_id)
            if conversation is not None
            else None
        )
        if conversation is None or contact is None:
            return PreparedNotice(order, "no_enviado")
        text = self.text_for(
            order, kind, contact=contact, siguiente_paso=siguiente_paso, motivo=motivo
        )

        if order.is_sandbox:
            self._messages.append(
                conversation_id=conversation.id,
                direction="outbound",
                author="system",
                content=text,
            )
            return PreparedNotice(order, "enviado")

        if not session_window_is_open(contact, now=datetime.now(UTC)):
            if self._notices is not None:
                self._notices.customer_not_notified(order, "ventana_cerrada")
            return PreparedNotice(order, "ventana_cerrada")

        message = self._messages.append(
            conversation_id=conversation.id,
            direction="outbound",
            author="system",
            content=text,
            delivery_status="queued",
        )
        extra = (
            OptinService(self._session, self._tenant_id).prepare_question(
                conversation, contact, now=datetime.now(UTC)
            )
            if kind == "pago_confirmado"
            else None
        )
        return PreparedNotice(order, "enviado", message, extra)

    async def deliver(self, prepared: PreparedNotice) -> CustomerNotice:
        """Hand a prepared notice to the provider, after the decision committed.

        Never raises for a failed send. If this never runs, the sweep sends it.
        """
        if prepared.message is None:
            return prepared.outcome
        order = prepared.order
        sender = ReplySender(
            self._session,
            self._tenant_id,
            provider=self._provider,
            max_attempts=self._max_attempts,
        )
        try:
            sent = await sender.send(prepared.message)
        except SessionWindowClosed:
            self._tell_business(order, "ventana_cerrada")
            return "ventana_cerrada"
        except NoConnectedNumber:
            # The message stays `queued`: reconnecting the number delivers it.
            logger.warning(
                "a decision could not be sent: no connected number",
                extra={"tenant_id": str(self._tenant_id)},
            )
            self._tell_business(order, "envio_fallido")
            return "no_enviado"
        self._session.commit()
        if sent.delivery_status == "failed":
            if prepared.extra is not None:
                # Not a question on its own, after a notice that did not arrive.
                self._messages.set_delivery_status(prepared.extra, status="failed")
            self._tell_business(order, "envio_fallido")
            return "no_enviado"
        if prepared.extra is not None:
            # The question is a courtesy: a failure here is logged, not the decision's.
            try:
                await sender.send(prepared.extra)
                self._session.commit()
            except Exception:  # noqa: BLE001 - the decision already went out; this is extra
                logger.warning("the promotions question was not sent", exc_info=True)
        return "enviado"

    async def notify(
        self,
        order: Order,
        kind: NoticeKind,
        *,
        siguiente_paso: str | None = None,
        motivo: str | None = None,
    ) -> CustomerNotice:
        """Prepare, commit and deliver in one call, for a decision already committed."""
        prepared = self.prepare(order, kind, siguiente_paso=siguiente_paso, motivo=motivo)
        self._session.commit()
        return await self.deliver(prepared)

    def _tell_business(
        self, order: Order, reason: Literal["ventana_cerrada", "envio_fallido"]
    ) -> None:
        if self._notices is not None:
            self._notices.customer_not_notified(order, reason)
        self._session.commit()


def _customer_name(order: Order, contact: Contact | None) -> str | None:
    """The name the customer gave; the profile label is a guess and is not used."""
    for source in (order.datos_cliente or {}, (contact.custom_data if contact else {}) or {}):
        name = source.get("nombres")
        if isinstance(name, str) and name.strip():
            return name.strip().split()[0]
    return None
