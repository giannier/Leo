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

import asyncio
import logging
import uuid
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.agent.circuit import CircuitBreaker
from app.agent.outbound import TurnOutbound
from app.agent.pipeline import InboundTurn, TurnPipeline
from app.core.config import get_settings
from app.models.business import AgentConfig
from app.models.channel import SESSION_WINDOW_HOURS, Contact
from app.models.conversation import Conversation, Message
from app.models.tenant import QUOTA_80, QUOTA_WARNING_RATIO, Tenant
from app.models.whatsapp import WhatsAppNumber
from app.orders.notices import OrderNotices
from app.orders.proofs import InboundImage, ProofPipeline
from app.process.inbound import interactive_of, media_of, payload_of, referral_of, stored_image_of
from app.providers.email.base import EmailMessage
from app.providers.llm.base import LLMProvider
from app.providers.storage.base import StorageProvider
from app.providers.whatsapp.base import (
    Button,
    InboundMessage,
    NumberCredentials,
    QualityUpdate,
    Recipient,
    SendResult,
    StatusUpdate,
    WhatsAppError,
    WhatsAppProvider,
)
from app.repositories.channel import (
    ContactRepository,
    MessageStatusEventRepository,
    WebhookEventRepository,
    session_window_is_open,
)
from app.repositories.conversation import ConversationRepository, MessageRepository
from app.repositories.whatsapp import WhatsAppNumberRepository, number_by_phone
from app.services.activation import owner_of
from app.services.campaigns.hooks import (
    answer_to_question,
    keeps_template_thread,
    pause_for_quality,
    revoke_on_opt_out,
    track_delivery,
    track_reply,
)
from app.services.connection import read_credentials
from app.services.emails import build_quality_alert_email, build_quota_warning_email
from app.services.inbound_media import InboundImageReader
from app.services.trial import (
    MeteringResult,
    billing_enforced,
    claim_notification,
    pause_tenant,
    record_ai_message,
)

logger = logging.getLogger(__name__)

# What the agent says to a voice note when the tenant has not written their
# own answer. Spanish, because a customer of a Peruvian bakery reads it.
DEFAULT_UNSUPPORTED_FALLBACK = (
    "Por ahora solo puedo leer mensajes de texto. Me lo escribes, por favor? Asi te ayudo mejor."
)


# Redis keys. Namespaced so a `KEYS channel:*` in an incident shows the whole
# pipeline's state at once.
# Builds the outbound adapter for one conversation, or None when it cannot send
# (the number was disconnected, say). The worker supplies it; see `workers/channel.py`.
OutboundFactory = Callable[[uuid.UUID], TurnOutbound | None]


def debounce_key(conversation_id: uuid.UUID) -> str:
    return f"channel:burst:{conversation_id}"


def lock_key(conversation_id: uuid.UUID) -> str:
    return f"channel:lock:{conversation_id}"


# Ratings that mean the number is one step from being limited or cut off.
ALARMING_QUALITY = ("RED", "FLAGGED")

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


def is_opt_out_request(text: str | None, keywords: Sequence[str]) -> bool:
    """Whether this message is somebody asking to be left alone (HU-03).

    The whole message must be the keyword, not contain it. A customer asking
    "tienen bajativos?" has not asked to be unsubscribed, and silencing them
    for it is both a lost sale and impossible for them to diagnose - they
    would simply never hear back again. Exact match is what every SMS gateway
    settled on for the same reason.

    Punctuation and case are forgiven, because "Baja." and "STOP" are the same
    request typed by two different people.
    """
    if not text:
        return False
    normalised = text.strip().strip(".!¡?¿,;: ").casefold()
    return any(normalised == keyword.strip().casefold() for keyword in keywords if keyword)


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
    # A fixed answer written instead of a turn (the tap on the promotions
    # question, Feature 011): sent by the caller, and the assistant is not called.
    system_reply_id: uuid.UUID | None = None


class ChannelEventProcessor:
    """Applies one parsed webhook event to the database.

    Takes a session and commits nothing: the caller owns the transaction, so
    one event is one unit of work and a failure halfway through leaves no
    half-written message.
    """

    def __init__(self, session: Session) -> None:
        self._session = session
        self.session = session
        # Messages to send *after* the transaction commits. Mailing a customer
        # about a state that then rolls back is worse than not mailing them:
        # they act on it, and there is nothing to act on.
        self.pending_emails: list[EmailMessage] = []

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
        contact = contacts.upsert(
            message.from_phone,
            external_user_id=message.from_user_id,
            profile_name=message.profile_name,
        )
        # Read before the stamp below moves it. This is when the customer
        # *previously* wrote, which is what decides whether they are
        # continuing a conversation or starting a new one.
        previous_inbound_at = contact.last_inbound_at
        contacts.record_inbound(contact, at=message.sent_at)
        self._apply_opt_out(tenant_id, contact, message)

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
                    # The tapped option and the ad: the turn that needs them
                    # runs after the debounce window, without the event.
                    payload=payload_of(message),
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

        reply = answer_to_question(
            self._session,
            tenant_id,
            contact=contact,
            conversation=conversation,
            tap=message.interactive,
            message_id=stored.id,
            at=message.sent_at,
        )
        track_reply(
            self._session, tenant_id, contact=contact, conversation=conversation, at=message.sent_at
        )
        return InboundResult(
            tenant_id=tenant_id,
            contact_id=contact.id,
            conversation_id=conversation.id,
            message_id=stored.id,
            is_new=True,
            system_reply_id=reply.id if reply is not None else None,
        )

    def _apply_opt_out(
        self, tenant_id: uuid.UUID, contact: Contact, message: InboundMessage
    ) -> None:
        """Honour STOP, and honour coming back (HU-03).

        Both directions matter and only one of them is obvious. Somebody who
        writes STOP must never hear from the agent again. Somebody who writes
        anything *else* afterwards has restarted the conversation themselves,
        and keeping them silenced because of a word they typed in March means
        their next order goes unanswered.
        """
        contacts = ContactRepository(self._session, tenant_id)
        config = self._session.get(AgentConfig, tenant_id)
        keywords = config.opt_out_keywords if config else []

        if is_opt_out_request(message.text, keywords):
            if contact.opted_out_at is None:
                logger.info("contact opted out", extra={"tenant_id": str(tenant_id)})
            contacts.set_opted_out(contact, at=message.sent_at)
            revoke_on_opt_out(self._session, tenant_id, contact, message.sent_at)
            return

        if contact.opted_out_at is not None:
            logger.info("contact re-engaged after opting out", extra={"tenant_id": str(tenant_id)})
            contacts.set_opted_out(contact, at=None)

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
            if not gone_quiet or keeps_template_thread(self._session, thread, previous_inbound_at):
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
        track_delivery(self._session, tenant_id, message, update.status)
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

        previous = number.quality_rating
        numbers = WhatsAppNumberRepository(self._session, number.tenant_id)
        numbers.update(number.id, quality_rating=update.quality_rating)
        # Campaigns stop at YELLOW already, on every report (Feature 011, C-002).
        pause_for_quality(self._session, number.tenant_id, update.quality_rating)

        # On the *transition*, not on the level. Quality drops, recovers and
        # drops again; alerting on the level would mail the customer every
        # time the provider repeated itself, and an alert that arrives daily
        # is one nobody reads on the day it matters.
        if update.quality_rating in ALARMING_QUALITY and previous != update.quality_rating:
            logger.error(
                "a tenant's number is in trouble with Meta",
                extra={
                    "tenant_id": str(number.tenant_id),
                    "quality_rating": update.quality_rating,
                },
            )
            self._warn_the_owner(number, update.quality_rating)
        return number.tenant_id

    def _warn_the_owner(self, number: WhatsAppNumber, rating: str) -> None:
        """Queue the warning. It is sent by the caller, after the commit.

        Not sent from here, and that is the point. This runs inside the
        transaction that records the rating, and mailing a customer about a
        state that then rolls back is worse than not mailing them at all -
        they act on it, and there is nothing to act on.

        The owner cannot learn this anywhere else: the rating lives in Meta's
        console, which they have no access to under an assisted connection.
        """
        owner = owner_of(self._session, number.tenant_id)
        tenant = self._session.get(Tenant, number.tenant_id)
        if owner is None or tenant is None:  # pragma: no cover - both are FKs
            return
        self.pending_emails.append(
            build_quality_alert_email(
                to=owner.email,
                tenant_name=tenant.name,
                phone_e164=number.phone_e164,
                quality_rating=rating,
                panel_url=f"{get_settings().frontend_base_url}/channel",
            )
        )


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
        outbound_for: OutboundFactory | None = None,
        model_tier_applies: bool = True,
        storage: StorageProvider | None = None,
        whatsapp: WhatsAppProvider | None = None,
        notices: OrderNotices | None = None,
        breaker: CircuitBreaker | None = None,
    ) -> None:
        self._session = session
        self._tenant_id = tenant_id
        self._llm = llm
        # The model's circuit breaker (TASK-445). None: always try the model.
        self._breaker = breaker
        # Builds the adapter the sending tools use for one conversation. Without
        # it the tools answer `no_disponible` and the turn is text only.
        self._outbound_for = outbound_for
        self._tier_applies = model_tier_applies
        # What reading a payment receipt needs: private storage for the photo and
        # the provider to fetch it from. Without storage an image is only context.
        self._storage = storage
        self._whatsapp = whatsapp
        self._notices = notices
        self._messages = MessageRepository(session, tenant_id)
        # Warnings this turn produced, sent by the caller after the commit.
        # Same reason as `ChannelEventProcessor`: mailing somebody about a
        # state that then rolls back is worse than not mailing them.
        self.pending_emails: list[EmailMessage] = []
        # How many files and options the tools sent during the last turn. They
        # went out as the model asked for them; only the text is left to send.
        self.sent_by_tools = 0

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
        """The first message of the answer, or None if there is nothing to say.

        `respond_turn` returns all of them; an answer is one or two bubbles.
        """
        answers = await self.respond_turn(conversation_id)
        return answers[0] if answers else None

    async def respond_turn(self, conversation_id: uuid.UUID) -> list[Message]:
        """Produce the answer for the whole burst, as the messages left to send.

        Runs the turn pipeline (Feature 004): the state is updated, the gates are
        applied (assistant off, opt-out, handover) and the model answers. The
        text bubbles are stored `queued`: written down here, handed to the
        provider by the sender (TASK-205). Storing them first means a send that
        fails leaves a record of what the business meant to say, rather than
        a silence nobody can explain. The files and options the tools sent have
        already gone out, each with its own row.
        """
        self.sent_by_tools = 0
        burst = self.pending_burst(conversation_id)
        if not burst:
            # Already answered - by an earlier run of this job, or by a human
            # in the inbox. Either way there is nothing left to say.
            return []

        pipeline = TurnPipeline(
            self._session,
            self._tenant_id,
            llm=self._llm,
            outbound=self._outbound_for(conversation_id) if self._outbound_for else None,
            queue_text=True,
            model_tier_applies=self._tier_applies,
            notices=self._notices,
            proofs=ProofPipeline(
                self._session,
                self._tenant_id,
                llm=self._llm,
                storage=self._storage,
                notices=self._notices,
            ),
            images=InboundImageReader(
                self._session, self._tenant_id, storage=self._storage, provider=self._whatsapp
            ),
            llm_open=self._breaker is not None and await self._breaker.is_open(),
        )
        result = await pipeline.run(conversation_id, self._read_burst(burst))
        if self._breaker is not None and result.reply is not None and result.reply.llm_failed:
            await self._breaker.record_failure()

        if result.outcome == "gated":
            # The agent is off, the trial is over, the tenant is paused, the
            # contact asked to be left alone or a person has the conversation.
            # The messages stay exactly where they are: HU-01 says an
            # unanswered message is still a message the owner must see.
            logger.info(
                "inbound message stored but not answered",
                extra={"tenant_id": str(self._tenant_id), "gate": result.gate},
            )
            return []

        if result.outcome in ("unsupported", "llm_down"):
            answers = [
                self._messages.append(
                    conversation_id=conversation_id,
                    direction="outbound",
                    author="agent",
                    content=self._unsupported_fallback(),
                    # Not sent yet. The sender flips this to `sent` once the
                    # provider has taken it, and the delivery webhooks carry it
                    # onwards.
                    delivery_status="queued",
                )
            ]
        else:
            answers = result.text_messages
            self.sent_by_tools = len(result.tool_messages)
        if result.outcome == "llm_down":
            # No model was called: nothing to count.
            return answers

        # Counted here, where the model bill is actually incurred, and only
        # for the real channel - `record_ai_message` ignores the sandbox by
        # design (Clarification 002). One turn is one message, however many
        # bubbles it takes.
        tenant = self._session.get(Tenant, self._tenant_id)
        if tenant is not None:
            metered = record_ai_message(self._session, self._tenant_id, channel="whatsapp")
            self._react_to_the_meter(tenant, metered)
        return answers

    @staticmethod
    def _read_burst(burst: Sequence[Message]) -> InboundTurn:
        """The burst as one turn: the text, the taps, the ad and whether an image came.

        Media lines are dropped from the text rather than passed through:
        `[imagen]` in the prompt teaches the model to talk about a placeholder it
        cannot see. A photo is told to the model as context instead (step 4).
        """
        texts = [
            message.content.strip()
            for message in burst
            if message.msg_type == "text" and message.content.strip()
        ]
        taps = tuple(
            tap for message in burst if (tap := interactive_of(message.payload)) is not None
        )
        referral = next(
            (ad for message in burst if (ad := referral_of(message.payload)) is not None), None
        )
        return InboundTurn(
            text="\n".join(texts),
            interactive=taps,
            referral=referral,
            image=any(message.msg_type == "image" for message in burst),
            images=tuple(
                InboundImage(
                    message_id=message.id,
                    media=media_of(message.payload),
                    stored=stored_image_of(message.payload),
                )
                for message in burst
                if message.msg_type == "image"
            ),
        )

    def _react_to_the_meter(self, tenant: Tenant, metered: MeteringResult) -> None:
        """Warn at 80%, and stop at 100% - in this reply, not in an hour.

        The hourly enforcer already pauses expired trials, and on the calendar
        that is fine: a day is a day whether it ends at 09:05 or 10:00. A
        *counter* is not. Clarification 004 says the agent finishes the
        conversation it is in and then stops, and waiting for the next cron
        tick would hand out up to an hour of free replies - which is also an
        hour of the customer talking to an agent that is, on paper, out of
        messages.
        """
        if not metered.counted or metered.limit <= 0 or not billing_enforced():
            # Billing off: the message was counted, nothing else happens.
            return

        if metered.exhausted:
            # The farewell and the close live in `pause_tenant`, which the
            # enforcer uses too. Called here so it happens on the message that
            # crossed the line rather than up to an hour later.
            pause_tenant(self._session, tenant, now=datetime.now(UTC))
            logger.info(
                "trial quota spent; the agent stops here",
                extra={"tenant_id": str(self._tenant_id), "used": metered.used},
            )
            return

        if metered.used >= int(metered.limit * QUOTA_WARNING_RATIO) and claim_notification(
            self._session, self._tenant_id, QUOTA_80
        ):
            owner = owner_of(self._session, self._tenant_id)
            if owner is None:
                return
            self.pending_emails.append(
                build_quota_warning_email(
                    to=owner.email,
                    tenant_name=tenant.name,
                    used=metered.used,
                    limit=metered.limit,
                    panel_url=f"{get_settings().frontend_base_url}/plan",
                )
            )

    def _unsupported_fallback(self) -> str:
        config = self._session.get(AgentConfig, self._tenant_id)
        configured = (config.unsupported_fallback or "").strip() if config else ""
        return configured or DEFAULT_UNSUPPORTED_FALLBACK


class NoConnectedNumber(RuntimeError):
    """The tenant has an answer to send and nothing to send it from."""


class SessionWindowClosed(RuntimeError):
    """Meta will not carry a free-form message to this person any more.

    Twenty-four hours after the customer's last message the only thing that
    may be sent is an approved template, and templates arrive with Feature
    011. Sending anyway is not a failed request - it is a policy violation
    that counts against the number's standing, which is the asset the whole
    business runs on.
    """


class ReplySender:
    """Hands a `queued` reply to the provider and records what happened.

    Separate from producing the reply, because the two fail differently and
    only one of them is worth retrying. Generating an answer twice costs a
    model call and risks two different answers; handing the same finished
    answer to the gateway again costs nothing and is exactly what a timeout
    calls for.

    The message row is the state machine. `queued` means written and not yet
    accepted; `sent` means the provider has it and named it; `failed` means it
    is not going to arrive and somebody has to know.
    """

    def __init__(
        self,
        session: Session,
        tenant_id: uuid.UUID,
        *,
        provider: WhatsAppProvider,
        max_attempts: int,
    ) -> None:
        self._session = session
        self._tenant_id = tenant_id
        self._provider = provider
        self._max_attempts = max(1, max_attempts)
        self._messages = MessageRepository(session, tenant_id)

    async def send(self, message: Message) -> Message:
        """Deliver one reply, retrying only what is worth retrying."""
        if message.delivery_status != "queued":
            # Already handled. A duplicate job must not send the customer the
            # same sentence twice.
            return message

        number, credentials = self._sender()
        contact = self._contact_for(message)
        if not session_window_is_open(contact, now=self._now()):
            # HU-03. The reply is not refused because it is wrong - it is
            # refused because Meta will not carry it, and trying anyway is a
            # policy violation charged against the number's standing.
            #
            # Marked `failed` rather than left `queued`: nothing later is
            # going to make it deliverable, and an answer stuck in the queue
            # for a day is worse than one the owner can see did not go out.
            self._messages.set_delivery_status(message, status="failed")
            MessageStatusEventRepository(self._session, self._tenant_id).append(
                message.id, status="failed"
            )
            logger.warning(
                "a reply arrived after the session window closed",
                extra={"tenant_id": str(self._tenant_id)},
            )
            raise SessionWindowClosed(
                "the customer last wrote more than 24 hours ago; only a template may be sent"
            )
        # A customer who wrote through a BSUID and never shared a number is
        # reached by the BSUID; the phone is used whenever there is one.
        recipient = Recipient(phone=contact.phone_e164, bsuid=contact.external_user_id)

        last_error: WhatsAppError | None = None
        for attempt in range(1, self._max_attempts + 1):
            try:
                result = await self._deliver(credentials, number.phone_e164, recipient, message)
            except WhatsAppError as exc:
                last_error = exc
                if not exc.retryable:
                    # A rejected credential or a refused message. The
                    # identical request produces the identical answer, so
                    # three attempts is three times the same failure and one
                    # customer's reply later than the first.
                    break
                if attempt < self._max_attempts:
                    await asyncio.sleep(_backoff_seconds(attempt))
                continue

            message.provider_message_id = result.provider_message_id
            self._messages.set_delivery_status(message, status=result.status or "sent")
            MessageStatusEventRepository(self._session, self._tenant_id).append(
                message.id, status=message.delivery_status or "sent"
            )
            return message

        self._messages.set_delivery_status(message, status="failed")
        MessageStatusEventRepository(self._session, self._tenant_id).append(
            message.id, status="failed"
        )
        logger.error(
            "a reply could not be handed to the provider",
            extra={
                "tenant_id": str(self._tenant_id),
                "attempts": self._max_attempts,
                "reason": str(last_error),
            },
        )
        return message

    async def _deliver(
        self,
        credentials: NumberCredentials,
        sender: str,
        recipient: Recipient,
        message: Message,
    ) -> SendResult:
        """Text, or the reply buttons a system message carries (Feature 011's question)."""
        payload = message.payload or {}
        if message.msg_type == "interactive" and payload.get("tipo") == "botones":
            return await self._provider.send_buttons(
                credentials,
                sender=sender,
                to=recipient,
                body=message.content,
                buttons=[
                    Button(id=str(b["id"]), title=str(b["title"]))
                    for b in payload.get("botones", [])
                ],
            )
        return await self._provider.send_text(
            credentials, sender=sender, to=recipient, body=message.content
        )

    def _sender(self) -> tuple[WhatsAppNumber, NumberCredentials]:
        numbers = WhatsAppNumberRepository(self._session, self._tenant_id).list_connected()
        if not numbers:
            raise NoConnectedNumber(f"tenant {self._tenant_id!s} has no connected number")
        # One number per tenant in v1 (C-005). The first is the only one.
        number = numbers[0]
        credentials = read_credentials(number)
        if credentials is None:
            raise NoConnectedNumber(
                f"number {number.phone_e164} is connected but carries no credentials"
            )
        return number, credentials

    def _contact_for(self, message: Message) -> Contact:
        """The person this thread belongs to."""
        conversation = ConversationRepository(self._session, self._tenant_id).get(
            message.conversation_id
        )
        if conversation is None or conversation.contact_id is None:
            raise NoConnectedNumber("the reply is not attached to a contact")
        contact = ContactRepository(self._session, self._tenant_id).get(conversation.contact_id)
        if contact is None:  # pragma: no cover - the FK guarantees it
            raise NoConnectedNumber("the conversation points at a contact that is gone")
        return contact

    @staticmethod
    def _now() -> datetime:
        return datetime.now(UTC)


def stranded_replies(
    session: Session, *, now: datetime, older_than_seconds: int, limit: int = 100
) -> Sequence[Message]:
    """Replies that were written down and never handed to the provider.

    The gap this closes is small and real: the reply is committed `queued`
    before the send is attempted, so a worker killed in between leaves an
    answer the customer never receives and nothing to notice it. From the
    outside that is indistinguishable from the agent ignoring somebody.

    `older_than_seconds` keeps the sweep away from sends that are simply in
    flight - a slow gateway plus three backoffs is tens of seconds, and
    picking one of those up would send the same sentence twice.

    Platform-wide, like the trial jobs: the caller re-scopes to each tenant
    before touching anything.
    """
    cutoff = now - timedelta(seconds=older_than_seconds)
    statement = (
        select(Message)
        .where(
            Message.direction == "outbound",
            Message.delivery_status == "queued",
            Message.created_at < cutoff,
        )
        .order_by(Message.created_at)
        .limit(limit)
    )
    return session.execute(statement).scalars().all()


def _backoff_seconds(attempt: int) -> float:
    """1s, then 2s, then 4s.

    Short on purpose. This is not a batch job draining a queue overnight - a
    customer is watching a chat window, and a delay long enough to be polite
    to the gateway is long enough for them to give up and go elsewhere.
    """
    return float(2 ** (attempt - 1))


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
