"""Handing a reply to the provider, and what the states mean (TASK-205).

Sending is separated from producing the reply because the two fail differently
and only one of them is worth retrying: generating an answer twice costs a
model call and risks two different answers, while handing the *same* finished
answer to the gateway again costs nothing and is exactly what a timeout calls
for.

The message row is the state machine. `queued` is written and not yet
accepted, `sent` is the provider has it, `failed` is it is not going to arrive
and somebody has to know. What is pinned here is that no failure ever leaves a
reply in a state that lies about it.
"""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.crypto import encrypt_secret
from app.models.channel import Contact, MessageStatusEvent
from app.models.conversation import Conversation, Message
from app.models.tenant import Tenant
from app.models.whatsapp import WhatsAppNumber
from app.providers.whatsapp.base import (
    NumberCredentials,
    SendResult,
    WhatsAppAuthError,
    WhatsAppError,
    WhatsAppRateLimited,
    WhatsAppTransientError,
)
from app.services.channel import NoConnectedNumber, ReplySender, stranded_replies

CUSTOMER = "+51987654321"


class ScriptedProvider:
    """A `WhatsAppProvider` whose sends are decided by the test."""

    def __init__(self, *outcomes: object) -> None:
        self._outcomes = list(outcomes)
        self.calls: list[dict[str, Any]] = []

    @property
    def name(self) -> str:
        return "ycloud"

    def validate(self, credentials: NumberCredentials) -> None:
        return None

    async def send_text(
        self,
        credentials: NumberCredentials,
        *,
        sender: str,
        to: str,
        body: str,
    ) -> SendResult:
        self.calls.append({"sender": sender, "to": to, "body": body})
        outcome = self._outcomes.pop(0) if self._outcomes else None
        if isinstance(outcome, Exception):
            raise outcome
        if isinstance(outcome, SendResult):
            return outcome
        return SendResult(provider_message_id=f"wamid.{uuid.uuid4().hex}", status="sent")


@pytest.fixture(autouse=True)
def no_waiting(monkeypatch: pytest.MonkeyPatch) -> None:
    """Backoff sleeps are real seconds. The suite asserts on the *number* of
    attempts, never on how long they took, so waiting proves nothing."""

    async def instantly(_seconds: float) -> None:
        return None

    monkeypatch.setattr("app.services.channel.asyncio.sleep", instantly)


def _tenant_ready_to_send(db: Session, *, with_credentials: bool = True) -> tuple[Tenant, str]:
    tenant = Tenant(name=f"Panaderia {uuid.uuid4().hex[:8]}")
    db.add(tenant)
    db.flush()
    phone = f"+5191{uuid.uuid4().int % 10**7:07d}"
    number = WhatsAppNumber(
        tenant_id=tenant.id,
        phone_e164=phone,
        display_name="Panaderia Quispe",
        provider="ycloud",
        status="connected",
    )
    if with_credentials:
        number.credentials_enc = encrypt_secret(
            json.dumps(
                {
                    "waba_id": "102290129340398",
                    "phone_number_id": "112233445566778",
                    "api_key": "yc_live_abcdef123456",
                }
            )
        )
    db.add(number)
    db.flush()
    return tenant, phone


def _queued_reply(db: Session, tenant: Tenant, *, content: str = "Si, tenemos.") -> Message:
    contact = Contact(tenant_id=tenant.id, phone_e164=CUSTOMER, last_inbound_at=datetime.now(UTC))
    db.add(contact)
    db.flush()
    conversation = Conversation(
        tenant_id=tenant.id, channel="whatsapp", contact_id=contact.id, status="open"
    )
    db.add(conversation)
    db.flush()
    message = Message(
        tenant_id=tenant.id,
        conversation_id=conversation.id,
        direction="outbound",
        author="agent",
        content=content,
        msg_type="text",
        delivery_status="queued",
    )
    db.add(message)
    db.flush()
    return message


def _sender(db: Session, tenant: Tenant, provider: ScriptedProvider, *, attempts: int = 3):
    return ReplySender(db, tenant.id, provider=provider, max_attempts=attempts)


class TestASuccessfulSend:
    async def test_it_goes_to_the_customer_from_the_tenants_number(self, db: Session) -> None:
        tenant, phone = _tenant_ready_to_send(db)
        message = _queued_reply(db, tenant)
        provider = ScriptedProvider()

        await _sender(db, tenant, provider).send(message)

        assert provider.calls == [{"sender": phone, "to": CUSTOMER, "body": "Si, tenemos."}]

    async def test_the_providers_id_is_stored(self, db: Session) -> None:
        """Every later delivery webhook names the message by this id. Without
        it the status updates arrive with nothing to attach them to."""
        tenant, _ = _tenant_ready_to_send(db)
        message = _queued_reply(db, tenant)
        provider = ScriptedProvider(SendResult(provider_message_id="wamid.abc123", status="sent"))

        await _sender(db, tenant, provider).send(message)

        assert message.provider_message_id == "wamid.abc123"
        assert message.delivery_status == "sent"

    async def test_the_send_is_written_into_the_history(self, db: Session) -> None:
        tenant, _ = _tenant_ready_to_send(db)
        message = _queued_reply(db, tenant)

        await _sender(db, tenant, ScriptedProvider()).send(message)

        events = (
            db.execute(
                select(MessageStatusEvent).where(MessageStatusEvent.message_id == message.id)
            )
            .scalars()
            .all()
        )
        assert [event.status for event in events] == ["sent"]

    async def test_a_message_that_is_not_queued_is_left_alone(self, db: Session) -> None:
        """A duplicate job must not send the customer the same sentence
        twice."""
        tenant, _ = _tenant_ready_to_send(db)
        message = _queued_reply(db, tenant)
        message.delivery_status = "sent"
        provider = ScriptedProvider()

        await _sender(db, tenant, provider).send(message)

        assert provider.calls == []


class TestRetrying:
    async def test_a_timeout_is_tried_again(self, db: Session) -> None:
        tenant, _ = _tenant_ready_to_send(db)
        message = _queued_reply(db, tenant)
        provider = ScriptedProvider(
            WhatsAppTransientError("timed out"),
            SendResult(provider_message_id="wamid.second", status="sent"),
        )

        await _sender(db, tenant, provider).send(message)

        assert len(provider.calls) == 2
        assert message.delivery_status == "sent"

    async def test_throttling_is_tried_again(self, db: Session) -> None:
        tenant, _ = _tenant_ready_to_send(db)
        message = _queued_reply(db, tenant)
        provider = ScriptedProvider(WhatsAppRateLimited("slow down"), SendResult("wamid.x", "sent"))

        await _sender(db, tenant, provider).send(message)

        assert len(provider.calls) == 2

    async def test_it_gives_up_after_the_configured_number_of_attempts(self, db: Session) -> None:
        tenant, _ = _tenant_ready_to_send(db)
        message = _queued_reply(db, tenant)
        provider = ScriptedProvider(*[WhatsAppTransientError("down")] * 5)

        await _sender(db, tenant, provider, attempts=3).send(message)

        assert len(provider.calls) == 3
        assert message.delivery_status == "failed"

    async def test_a_rejected_credential_is_not_tried_again(self, db: Session) -> None:
        """Three attempts against a wrong key is three times the same answer,
        one customer's reply later than the first."""
        tenant, _ = _tenant_ready_to_send(db)
        message = _queued_reply(db, tenant)
        provider = ScriptedProvider(*[WhatsAppAuthError("bad key")] * 3)

        await _sender(db, tenant, provider, attempts=3).send(message)

        assert len(provider.calls) == 1
        assert message.delivery_status == "failed"

    async def test_a_refused_message_is_not_tried_again(self, db: Session) -> None:
        tenant, _ = _tenant_ready_to_send(db)
        message = _queued_reply(db, tenant)
        provider = ScriptedProvider(*[WhatsAppError("outside the session window")] * 3)

        await _sender(db, tenant, provider, attempts=3).send(message)

        assert len(provider.calls) == 1


class TestAFailedSend:
    async def test_the_reply_is_marked_failed_not_left_pending(self, db: Session) -> None:
        """`queued` forever is the worst outcome: it looks like work in
        progress, so nobody looks at it."""
        tenant, _ = _tenant_ready_to_send(db)
        message = _queued_reply(db, tenant)

        await _sender(db, tenant, ScriptedProvider(WhatsAppAuthError("no"))).send(message)

        assert message.delivery_status == "failed"

    async def test_the_failure_is_in_the_history(self, db: Session) -> None:
        tenant, _ = _tenant_ready_to_send(db)
        message = _queued_reply(db, tenant)

        await _sender(db, tenant, ScriptedProvider(WhatsAppAuthError("no"))).send(message)

        events = (
            db.execute(
                select(MessageStatusEvent).where(MessageStatusEvent.message_id == message.id)
            )
            .scalars()
            .all()
        )
        assert [event.status for event in events] == ["failed"]

    async def test_what_the_business_meant_to_say_survives(self, db: Session) -> None:
        """The reply text is still there. A failed send that also lost the
        answer would leave the owner unable to see what went out - or did
        not."""
        tenant, _ = _tenant_ready_to_send(db)
        message = _queued_reply(db, tenant, content="Tenemos pan integral a 6 soles.")

        await _sender(db, tenant, ScriptedProvider(WhatsAppAuthError("no"))).send(message)

        db.refresh(message)
        assert message.content == "Tenemos pan integral a 6 soles."


class TestWhenThereIsNowhereToSendFrom:
    async def test_a_tenant_with_no_connected_number_is_an_error(self, db: Session) -> None:
        """Not a failed send - the reply is fine and the number is not. The
        caller leaves it `queued`, because reconnecting the number is what
        fixes it."""
        tenant = Tenant(name=f"Sin numero {uuid.uuid4().hex[:8]}")
        db.add(tenant)
        db.flush()
        message = _queued_reply(db, tenant)

        with pytest.raises(NoConnectedNumber):
            await _sender(db, tenant, ScriptedProvider()).send(message)

        assert message.delivery_status == "queued"

    async def test_a_number_without_credentials_is_an_error(self, db: Session) -> None:
        tenant, _ = _tenant_ready_to_send(db, with_credentials=False)
        message = _queued_reply(db, tenant)

        with pytest.raises(NoConnectedNumber):
            await _sender(db, tenant, ScriptedProvider()).send(message)


class TestTheSweepForRepliesNobodySent:
    """The worker was killed between committing the answer and sending it.

    A narrow window, and the only one where nothing else would ever notice:
    the job that should have sent it is gone, so there is no retry to fire and
    no error to see. From the customer's side it is indistinguishable from the
    business ignoring them.
    """

    def test_a_reply_left_queued_long_enough_is_found(self, db: Session) -> None:
        tenant, _ = _tenant_ready_to_send(db)
        message = _queued_reply(db, tenant)
        message.created_at = datetime.now(UTC) - timedelta(minutes=10)
        db.flush()

        stranded = stranded_replies(db, now=datetime.now(UTC), older_than_seconds=120)

        assert message.id in {row.id for row in stranded}

    def test_a_send_that_is_merely_in_flight_is_left_alone(self, db: Session) -> None:
        """A slow gateway plus three backoffs is tens of seconds. Picking one
        of those up would send the customer the same sentence twice."""
        tenant, _ = _tenant_ready_to_send(db)
        message = _queued_reply(db, tenant)

        stranded = stranded_replies(db, now=datetime.now(UTC), older_than_seconds=120)

        assert message.id not in {row.id for row in stranded}

    def test_an_already_sent_reply_is_never_swept(self, db: Session) -> None:
        tenant, _ = _tenant_ready_to_send(db)
        message = _queued_reply(db, tenant)
        message.delivery_status = "sent"
        message.created_at = datetime.now(UTC) - timedelta(minutes=10)
        db.flush()

        stranded = stranded_replies(db, now=datetime.now(UTC), older_than_seconds=120)

        assert message.id not in {row.id for row in stranded}

    def test_a_failed_reply_is_not_retried_by_the_sweep(self, db: Session) -> None:
        """`failed` is a decision that was already made and reported. The
        sweep is for replies nobody ever decided anything about."""
        tenant, _ = _tenant_ready_to_send(db)
        message = _queued_reply(db, tenant)
        message.delivery_status = "failed"
        message.created_at = datetime.now(UTC) - timedelta(minutes=10)
        db.flush()

        stranded = stranded_replies(db, now=datetime.now(UTC), older_than_seconds=120)

        assert message.id not in {row.id for row in stranded}

    async def test_sweeping_one_actually_sends_it(self, db: Session) -> None:
        tenant, phone = _tenant_ready_to_send(db)
        message = _queued_reply(db, tenant, content="Perdon la demora.")
        message.created_at = datetime.now(UTC) - timedelta(minutes=10)
        db.flush()
        provider = ScriptedProvider()

        for stranded in stranded_replies(db, now=datetime.now(UTC), older_than_seconds=120):
            if stranded.id == message.id:
                await _sender(db, tenant, provider).send(stranded)

        assert provider.calls == [{"sender": phone, "to": CUSTOMER, "body": "Perdon la demora."}]
        assert message.delivery_status == "sent"


class TestTheStatesTellTheTruth:
    async def test_read_beats_delivered_whichever_arrives_first(self, db: Session) -> None:
        """`delivered` and `read` webhooks arrive out of order routinely, and
        showing a message as merely delivered after the customer read it is a
        small lie the owner will act on."""
        from app.repositories.conversation import MessageRepository

        tenant, _ = _tenant_ready_to_send(db)
        message = _queued_reply(db, tenant)
        messages = MessageRepository(db, tenant.id)

        messages.set_delivery_status(message, status="read")
        messages.set_delivery_status(message, status="delivered")

        assert message.delivery_status == "read"

    async def test_failed_is_terminal(self, db: Session) -> None:
        """A deliberate choice about which mistake to prefer: a message
        wrongly left as failed shows the tenant a problem to look at, while a
        failure wrongly overwritten by a late success is a lost sale nobody is
        ever told about."""
        from app.repositories.conversation import MessageRepository

        tenant, _ = _tenant_ready_to_send(db)
        message = _queued_reply(db, tenant)
        messages = MessageRepository(db, tenant.id)

        messages.set_delivery_status(message, status="failed")
        messages.set_delivery_status(message, status="delivered")

        assert message.delivery_status == "failed"


class TestAContactWithoutAPhoneNumber:
    """A customer who writes with a BSUID only (Feature 004, TASK-408). The
    provider still addresses by phone until TASK-418, so the reply must end
    visibly `failed` instead of reaching the provider with no recipient."""

    async def test_the_reply_is_marked_failed_and_nothing_is_sent(self, db: Session) -> None:
        tenant, _ = _tenant_ready_to_send(db)
        contact = Contact(
            tenant_id=tenant.id, external_user_id="BSUID-1", last_inbound_at=datetime.now(UTC)
        )
        db.add(contact)
        db.flush()
        conversation = Conversation(
            tenant_id=tenant.id, channel="whatsapp", contact_id=contact.id, status="open"
        )
        db.add(conversation)
        db.flush()
        message = Message(
            tenant_id=tenant.id,
            conversation_id=conversation.id,
            direction="outbound",
            author="agent",
            content="Hola",
            msg_type="text",
            delivery_status="queued",
        )
        db.add(message)
        db.flush()
        provider = ScriptedProvider()

        result = await _sender(db, tenant, provider).send(message)

        assert result.delivery_status == "failed"
        assert provider.calls == []
        events = db.execute(
            select(MessageStatusEvent.status).where(MessageStatusEvent.message_id == message.id)
        ).scalars()
        assert list(events) == ["failed"]
