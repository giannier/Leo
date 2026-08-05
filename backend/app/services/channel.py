"""What happens to a webhook once it has been stored.

The endpoint's only job is to accept the delivery and get out of the way
(RF-001: 200 in under two seconds). Everything real happens here, in a worker,
against the row that was already written - which is what makes it safe to
retry and what makes a parser fix replayable.

The rule this module exists to keep is HU-01's: **a message is never
discarded**. Not when the agent is off, not when the tenant is paused, not
when the message is a sticker this version cannot read, not when the number is
one nobody recognises. Every one of those still ends with a row somebody can
look at, because the customer on the other end has no idea any of it happened
and will simply conclude the business ignored them.
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.agent.core import AgentCore
from app.models.business import AgentConfig
from app.models.channel import SESSION_WINDOW_HOURS, Contact
from app.models.conversation import Conversation, Message
from app.models.tenant import Tenant
from app.providers.llm.base import LLMProvider
from app.providers.whatsapp.base import (
    InboundMessage,
    QualityUpdate,
    StatusUpdate,
    WhatsAppProvider,
)
from app.repositories.channel import (
    ContactRepository,
    MessageStatusEventRepository,
    WebhookEventRepository,
)
from app.repositories.conversation import ConversationRepository, MessageRepository
from app.repositories.whatsapp import WhatsAppNumberRepository, number_by_phone
from app.services.trial import agent_may_reply, record_ai_message

logger = logging.getLogger(__name__)

# What the agent says to a voice note when the tenant has not written their
# own answer. Spanish, because a customer of a Peruvian bakery reads it.
DEFAULT_UNSUPPORTED_FALLBACK = (
    "Por ahora solo puedo leer mensajes de texto. Me lo escribes, por favor? Asi te ayudo mejor."
)


# Redis keys. Namespaced so a `KEYS channel:*` in an incident shows the whole
# pipeline's state at once.
def debounce_key(conversation_id: uuid.UUID) -> str:
    return f"channel:burst:{conversation_id}"


def lock_key(conversation_id: uuid.UUID) -> str:
    return f"channel:lock:{conversation_id}"


# What a message that is not text is recorded as, so the inbox shows the
# customer said *something* rather than an empty bubble. Spanish, because
# unlike an error code this is read by the shop owner.
MEDIA_PLACEHOLDERS = {
    "image": "[imagen]",
    "document": "[documento]",
    "audio": "[audio]",
    "sticker": "[sticker]",
    "location": "[ubicacion]",
    "contact": "[contacto]",
    "unsupported": "[mensaje no soportado]",
}


class UnknownNumberError(RuntimeError):
    """A message addressed to a number no tenant owns.

    Deliberately an error rather than a shrug. It means either a number was
    disconnected while a customer was still writing to it, or the webhook is
    pointed at the wrong deployment - both things an operator has to see, and
    both things that leave a real person waiting for an answer.
    """


@dataclass(frozen=True)
class InboundResult:
    """Where an inbound message landed."""

    tenant_id: uuid.UUID
    contact_id: uuid.UUID
    conversation_id: uuid.UUID
    message_id: uuid.UUID
    # False when this exact provider message was already stored - a
    # redelivery. The caller uses it to avoid answering the same question
    # twice.
    is_new: bool


class ChannelEventProcessor:
    """Applies one parsed webhook event to the database.

    Takes a session and commits nothing: the caller owns the transaction, so
    one event is one unit of work and a failure halfway through leaves no
    half-written message.
    """

    def __init__(self, session: Session) -> None:
        self._session = session

    # --------------------------------------------------------------- inbound

    def receive_inbound(self, message: InboundMessage) -> InboundResult:
        """Store a customer's message, creating the contact and thread."""
        number = number_by_phone(self._session, message.to_phone)
        if number is None:
            raise UnknownNumberError(
                f"no connected number matches {message.to_phone!r}; "
                "the message cannot be attributed to a tenant"
            )
        tenant_id = number.tenant_id

        contacts = ContactRepository(self._session, tenant_id)
        contact = contacts.upsert(message.from_phone, profile_name=message.profile_name)
        # Read before the stamp below moves it. This is when the customer
        # *previously* wrote, which is what decides whether they are
        # continuing a conversation or starting a new one.
        previous_inbound_at = contact.last_inbound_at
        contacts.record_inbound(contact, at=message.sent_at)
        if contact.opted_out_at is not None:
            # Writing to the business again is how somebody comes back
            # (HU-03). Keeping them silenced because they once said STOP
            # would mean their next order goes unanswered.
            logger.info("contact re-engaged after opting out", extra={"tenant_id": str(tenant_id)})
            contacts.set_opted_out(contact, at=None)

        conversation = self._resolve_conversation(
            tenant_id,
            contact,
            at=message.sent_at,
            previous_inbound_at=previous_inbound_at,
        )

        messages = MessageRepository(self._session, tenant_id)
        existing = messages.by_provider_message_id(message.provider_message_id)
        if existing is not None:
            # A redelivery that got past the event-level check, which happens
            # when the provider sends the same message under two event ids.
            return InboundResult(
                tenant_id=tenant_id,
                contact_id=contact.id,
                conversation_id=existing.conversation_id,
                message_id=existing.id,
                is_new=False,
            )

        content = message.text or MEDIA_PLACEHOLDERS.get(message.msg_type, "[mensaje]")
        try:
            with self._session.begin_nested():
                stored = messages.append(
                    conversation_id=conversation.id,
                    direction="inbound",
                    author="customer",
                    content=content,
                    msg_type=message.msg_type,
                    provider_message_id=message.provider_message_id,
                )
        except IntegrityError:
            # Two workers picked up two deliveries of the same message at
            # once. The unique index settled it; this side just reads the
            # winner rather than failing the job.
            duplicate = messages.by_provider_message_id(message.provider_message_id)
            if duplicate is None:
                raise
            return InboundResult(
                tenant_id=tenant_id,
                contact_id=contact.id,
                conversation_id=duplicate.conversation_id,
                message_id=duplicate.id,
                is_new=False,
            )

        return InboundResult(
            tenant_id=tenant_id,
            contact_id=contact.id,
            conversation_id=conversation.id,
            message_id=stored.id,
            is_new=True,
        )

    def _resolve_conversation(
        self,
        tenant_id: uuid.UUID,
        contact: Contact,
        *,
        at: datetime,
        previous_inbound_at: datetime | None,
    ) -> Conversation:
        """The thread this message belongs to, opening a new one after a day.

        C-002 ties the thread to Meta's session window rather than to a
        timeout of our own: somebody who comes back a week later is starting a
        new conversation, and stapling it onto the old one would hand the
        agent week-old context and show the owner one endless thread.

        The clock that matters is the customer's own - when *they* last wrote,
        not when the row was last touched. A thread the business replied to
        five minutes ago is still stale if the customer went quiet two days
        before that.
        """
        conversations = ConversationRepository(self._session, tenant_id)
        thread = conversations.open_for_contact(contact.id)
        if thread is not None:
            gone_quiet = previous_inbound_at is None or (at - previous_inbound_at) >= timedelta(
                hours=SESSION_WINDOW_HOURS
            )
            if not gone_quiet:
                return thread
            # Close it, so the inbox does not show a thread nobody is in any
            # more and the agent does not read it as context.
            conversations.update(thread.id, status="closed")

        return conversations.add(channel="whatsapp", contact_id=contact.id, status="open")

    # -------------------------------------------------------------- outbound

    def apply_status(self, update: StatusUpdate) -> uuid.UUID | None:
        """Record a delivery report against the message it refers to.

        Returns the message id, or None when the report is for a message this
        platform did not send - which is not an error worth retrying: the
        provider is entitled to report on things we have no record of.
        """
        tenant_id = _tenant_of_message(self._session, update.provider_message_id)
        if tenant_id is None:
            logger.info(
                "delivery report for an unknown message",
                extra={"provider_message_id": update.provider_message_id[:32]},
            )
            return None

        messages = MessageRepository(self._session, tenant_id)
        message = messages.by_provider_message_id(update.provider_message_id)
        if message is None:  # pragma: no cover - the lookup above just found it
            return None

        messages.set_delivery_status(message, status=update.status)
        MessageStatusEventRepository(self._session, tenant_id).append(
            message.id, status=update.status, provider_ts=update.occurred_at
        )
        if update.status == "failed":
            logger.warning(
                "a reply did not reach the customer",
                extra={"tenant_id": str(tenant_id), "reason": update.error},
            )
        return message.id

    def apply_quality(self, update: QualityUpdate) -> uuid.UUID | None:
        """Record Meta's verdict on a number's standing.

        A drop to RED or FLAGGED is the warning before the number stops being
        able to send at all, so it is stored where the panel and the admin
        both read it (HU-03).
        """
        number = number_by_phone(self._session, update.phone_e164)
        if number is None:
            logger.warning(
                "quality report for a number nobody owns",
                extra={"phone_suffix": update.phone_e164[-4:]},
            )
            return None

        numbers = WhatsAppNumberRepository(self._session, number.tenant_id)
        numbers.update(number.id, quality_rating=update.quality_rating)
        if update.quality_rating in ("RED", "FLAGGED"):
            logger.error(
                "a tenant's number is in trouble with Meta",
                extra={
                    "tenant_id": str(number.tenant_id),
                    "quality_rating": update.quality_rating,
                },
            )
        return number.tenant_id


def _tenant_of_message(session: Session, provider_message_id: str) -> uuid.UUID | None:
    """Which tenant sent the message a delivery report names.

    Same shape as `number_by_phone`, and unscoped for the same reason: the
    provider's id is globally unique, and the tenant is what is being looked
    up rather than what is being filtered on. The scoped repository is used
    for everything that follows.
    """
    statement = select(Message.tenant_id).where(Message.provider_message_id == provider_message_id)
    return session.execute(statement).scalar_one_or_none()


class ConversationResponder:
    """Answers one thread, once, for everything the customer just said.

    RF-005 and RF-006 in one place, because they are two halves of the same
    promise: somebody who fires off "hola" / "buenas" / "tienen pan?" in eight
    seconds asked *one* question, and answering it three times reads like a
    broken robot and costs three model calls.

    The burst is not tracked anywhere. It is simply every inbound message
    since the last thing the business said, which is exactly what the agent
    has not answered yet - and is still correct after a crash, a redelivery,
    or a worker that picked the job up twice.
    """

    def __init__(
        self,
        session: Session,
        tenant_id: uuid.UUID,
        *,
        llm: LLMProvider,
    ) -> None:
        self._session = session
        self._tenant_id = tenant_id
        self._llm = llm
        self._messages = MessageRepository(session, tenant_id)

    def pending_burst(self, conversation_id: uuid.UUID) -> list[Message]:
        """Everything the customer has said since the last reply."""
        transcript = list(self._messages.list_for_conversation(conversation_id))
        burst: list[Message] = []
        for message in reversed(transcript):
            if message.direction == "outbound":
                break
            burst.append(message)
        return list(reversed(burst))

    async def respond(self, conversation_id: uuid.UUID) -> Message | None:
        """Produce one reply for the whole burst, or None if there is nothing.

        The reply is stored `queued`: written down here, handed to the
        provider by the sender (TASK-205). Storing it first means a send that
        fails leaves a record of what the business meant to say, rather than
        a silence nobody can explain.
        """
        tenant = self._session.get(Tenant, self._tenant_id)
        if tenant is None:  # pragma: no cover - the caller just read it
            return None

        burst = self.pending_burst(conversation_id)
        if not burst:
            # Already answered - by an earlier run of this job, or by a human
            # in the inbox. Either way there is nothing left to say.
            return None

        if not agent_may_reply(self._session, tenant):
            # The agent is off, the trial is over, or the tenant is paused.
            # The messages stay exactly where they are: HU-01 says an
            # unanswered message is still a message the owner must see.
            logger.info(
                "inbound message stored but not answered",
                extra={"tenant_id": str(self._tenant_id), "status": tenant.status},
            )
            return None

        text = self._as_one_question(burst)
        if text is None:
            reply_text = self._unsupported_fallback()
        else:
            core = AgentCore(self._session, self._tenant_id, llm=self._llm)
            reply = await core.respond(conversation_id=conversation_id, customer_message=text)
            reply_text = reply.content

        answer = self._messages.append(
            conversation_id=conversation_id,
            direction="outbound",
            author="agent",
            content=reply_text,
            # Not sent yet. The sender flips this to `sent` once the provider
            # has taken it, and the delivery webhooks carry it onwards.
            delivery_status="queued",
        )
        if text is not None:
            core.persist_trace(message=answer, reply=reply)
        # Counted here, where the model bill is actually incurred, and only
        # for the real channel - `record_ai_message` ignores the sandbox by
        # design (Clarification 002).
        record_ai_message(self._session, self._tenant_id, channel="whatsapp")
        return answer

    @staticmethod
    def _as_one_question(burst: Sequence[Message]) -> str | None:
        """The burst as a single prompt, or None if none of it was readable.

        Media lines are dropped rather than passed through: `[imagen]` in the
        prompt teaches the model to talk about a placeholder it cannot see.
        What matters is whether *anything* was text - a photo with a caption
        is a question, a photo alone is not.
        """
        readable = [
            message.content.strip()
            for message in burst
            if message.msg_type == "text" and message.content.strip()
        ]
        return "\n".join(readable) if readable else None

    def _unsupported_fallback(self) -> str:
        config = self._session.get(AgentConfig, self._tenant_id)
        configured = (config.unsupported_fallback or "").strip() if config else ""
        return configured or DEFAULT_UNSUPPORTED_FALLBACK


class WebhookIntake:
    """Accepting a delivery at the door.

    Everything here happens inside the HTTP request, so it is deliberately
    tiny: verify, store, queue. RF-001 gives the whole thing two seconds, and
    a provider that times out redelivers - which would be harmless but is
    also pointless load.
    """

    def __init__(
        self,
        session: Session,
        *,
        provider: WhatsAppProvider,
        secret: str | None,
    ) -> None:
        self._session = session
        self._provider = provider
        self._secret = secret

    def is_signed(self, *, raw_body: bytes, header: str | None) -> bool:
        if not self._secret:
            # Refusing everything is the correct failure. An endpoint with no
            # secret configured is a public write API to every tenant's inbox.
            logger.error("no webhook secret is configured; every delivery is refused")
            return False
        return self._provider.verify_signature(
            raw_body=raw_body, header=header, secret=self._secret
        )

    def store(self, payload: dict[str, object]) -> tuple[uuid.UUID, bool] | None:
        """Record the delivery. Returns its id and whether it needs processing.

        `None` means the payload carried no event id, which cannot be made
        idempotent and so is refused rather than stored under one we invented -
        the provider's retry would arrive as a second message.
        """
        event_id = self._provider.event_id(payload)
        if not event_id:
            return None

        events = WebhookEventRepository(self._session)
        existing = events.by_provider_event_id(event_id)
        if existing is None:
            stored = events.record(
                provider=self._provider.name, provider_event_id=event_id, payload=payload
            )
            if stored is None:  # pragma: no cover - lost a race, see below
                stored = events.by_provider_event_id(event_id)
                if stored is None:
                    return None
            return stored.id, True

        # A redelivery. Whether it still needs work depends on how far the
        # first attempt got: `processed` and `dead_letter` are terminal, and
        # anything else means the provider's retry is a second chance rather
        # than a duplicate to discard.
        return existing.id, existing.status not in ("processed", "dead_letter")
