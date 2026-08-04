"""`process_event`: what happens to a delivery after the 200 (TASK-203).

Driven against a real database through the exact function the ARQ job calls,
so what is being tested is the production path rather than a rehearsal of it.

The thing under examination is HU-01's promise - **a message is never
discarded** - and its three uncomfortable corners: a message this version
cannot read, a number nobody owns, and a bug in our own code. All three have
to end with a row somebody can look at, because the customer on the other end
has no idea any of it happened and will conclude the business ignored them.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.channel import MAX_WEBHOOK_ATTEMPTS, Contact, MessageStatusEvent
from app.models.conversation import Conversation, Message
from app.models.tenant import Tenant
from app.models.whatsapp import WhatsAppNumber
from app.providers.whatsapp import YCloudProvider
from app.repositories.channel import WebhookEventRepository
from app.workers.channel import (
    OUTCOME_ALREADY_DONE,
    OUTCOME_APPLIED,
    OUTCOME_IGNORED,
    OUTCOME_MISSING,
    OUTCOME_UNKNOWN_NUMBER,
    OUTCOME_UNREADABLE,
    process_event,
)
from tests.fixtures import load

PROVIDER = YCloudProvider()
CUSTOMER = "+51987654321"


def _tenant_with_number(db: Session) -> tuple[Tenant, str]:
    """A tenant owning a connected number, and that number.

    Committed, like everything the worker finds already in place. The failure
    paths below roll back, and a fixture that only flushed would be rolled
    back with them - which is not what happens in production and would make
    those tests assert on a database state that cannot occur.

    The number is randomised because `whatsapp_numbers.phone_e164` is unique
    platform-wide and this suite shares a database with every other test.
    """
    tenant = Tenant(name=f"Panaderia {uuid.uuid4().hex[:8]}")
    db.add(tenant)
    db.flush()
    phone = f"+5191{uuid.uuid4().int % 10**7:07d}"
    db.add(
        WhatsAppNumber(
            tenant_id=tenant.id,
            phone_e164=phone,
            display_name="Panaderia Quispe",
            provider="ycloud",
            status="connected",
        )
    )
    db.commit()
    return tenant, phone


def _store(db: Session, payload: dict[str, Any]) -> uuid.UUID:
    """Put a delivery in the table the way the endpoint would have.

    Committed for the same reason the endpoint commits before it enqueues:
    the row has to exist independently of whatever the job's own transaction
    goes on to do with it.
    """
    event = WebhookEventRepository(db).record(
        provider="ycloud",
        provider_event_id=f"evt_{uuid.uuid4().hex}",
        payload=payload,
    )
    assert event is not None
    db.commit()
    return event.id


def _inbound(to_phone: str, *, text: str = "Hola, tienen pan integral?") -> dict[str, Any]:
    payload = load("ycloud/inbound_text.json")
    payload["whatsappInboundMessage"]["to"] = to_phone
    payload["whatsappInboundMessage"]["text"]["body"] = text
    payload["whatsappInboundMessage"]["id"] = f"wamid.{uuid.uuid4().hex}"
    return payload


class TestAnInboundMessage:
    def test_it_creates_the_contact_and_the_thread(self, db: Session) -> None:
        tenant, phone = _tenant_with_number(db)
        event_id = _store(db, _inbound(phone))

        outcome = process_event(db, event_id, provider=PROVIDER)

        assert outcome.outcome == OUTCOME_APPLIED
        contact = db.execute(select(Contact).where(Contact.tenant_id == tenant.id)).scalar_one()
        assert contact.phone_e164 == CUSTOMER
        assert contact.profile_name == "Ana Quispe"

    def test_the_message_is_stored_with_the_providers_id(self, db: Session) -> None:
        tenant, phone = _tenant_with_number(db)
        payload = _inbound(phone)
        event_id = _store(db, payload)

        process_event(db, event_id, provider=PROVIDER)

        message = db.execute(select(Message).where(Message.tenant_id == tenant.id)).scalar_one()
        assert message.content == "Hola, tienen pan integral?"
        assert message.direction == "inbound"
        assert message.author == "customer"
        assert message.msg_type == "text"
        assert message.provider_message_id == payload["whatsappInboundMessage"]["id"]

    def test_the_session_window_is_opened_from_the_customers_clock(self, db: Session) -> None:
        """`last_inbound_at` is what Meta's 24 hours are measured from, and
        Meta measures from when *they* sent it, not from when we received it."""
        tenant, phone = _tenant_with_number(db)
        event_id = _store(db, _inbound(phone))

        process_event(db, event_id, provider=PROVIDER)

        contact = db.execute(select(Contact).where(Contact.tenant_id == tenant.id)).scalar_one()
        assert contact.last_inbound_at == datetime(2026, 8, 4, 14, 32, 10, tzinfo=UTC)

    def test_the_event_ends_as_processed(self, db: Session) -> None:
        _, phone = _tenant_with_number(db)
        event_id = _store(db, _inbound(phone))

        process_event(db, event_id, provider=PROVIDER)

        event = WebhookEventRepository(db).get(event_id)
        assert event is not None
        assert event.status == "processed"
        assert event.processed_at is not None

    def test_it_reports_the_message_that_still_needs_an_answer(self, db: Session) -> None:
        """What TASK-204's pipeline picks up. A stored message nobody is told
        about is a customer nobody replies to."""
        tenant, phone = _tenant_with_number(db)
        event_id = _store(db, _inbound(phone))

        outcome = process_event(db, event_id, provider=PROVIDER)

        assert outcome.inbound_message_id is not None
        assert outcome.tenant_id == tenant.id


class TestAMessageThisVersionCannotRead:
    def test_it_is_stored_rather_than_dropped(self, db: Session) -> None:
        """C-001. The customer sent a video; the business still has to know
        they said something."""
        tenant, phone = _tenant_with_number(db)
        payload = load("ycloud/inbound_video.json")
        payload["whatsappInboundMessage"]["to"] = phone
        payload["whatsappInboundMessage"]["id"] = f"wamid.{uuid.uuid4().hex}"

        outcome = process_event(db, _store(db, payload), provider=PROVIDER)

        assert outcome.outcome == OUTCOME_APPLIED
        message = db.execute(select(Message).where(Message.tenant_id == tenant.id)).scalar_one()
        assert message.msg_type == "unsupported"
        assert message.content == "[mensaje no soportado]"

    def test_it_still_opens_the_session_window(self, db: Session) -> None:
        """It is an interaction. Treating it as nothing would leave the
        business unable to reply to somebody who just wrote to them."""
        tenant, phone = _tenant_with_number(db)
        payload = load("ycloud/inbound_video.json")
        payload["whatsappInboundMessage"]["to"] = phone
        payload["whatsappInboundMessage"]["id"] = f"wamid.{uuid.uuid4().hex}"

        process_event(db, _store(db, payload), provider=PROVIDER)

        contact = db.execute(select(Contact).where(Contact.tenant_id == tenant.id)).scalar_one()
        assert contact.last_inbound_at is not None


class TestTheConversationThread:
    def test_a_second_message_joins_the_same_thread(self, db: Session) -> None:
        tenant, phone = _tenant_with_number(db)
        process_event(db, _store(db, _inbound(phone, text="Hola")), provider=PROVIDER)
        process_event(db, _store(db, _inbound(phone, text="Sigues ahi?")), provider=PROVIDER)

        threads = (
            db.execute(select(Conversation).where(Conversation.tenant_id == tenant.id))
            .scalars()
            .all()
        )
        assert len(threads) == 1

    def test_somebody_returning_after_a_day_starts_a_new_one(self, db: Session) -> None:
        """C-002. Stapling a week-old thread onto today's message would hand
        the agent stale context and show the owner one endless conversation."""
        tenant, phone = _tenant_with_number(db)
        first = _inbound(phone, text="Hola")
        first["whatsappInboundMessage"]["sendTime"] = "2026-08-01T10:00:00.000Z"
        process_event(db, _store(db, first), provider=PROVIDER)

        second = _inbound(phone, text="Hola de nuevo")
        second["whatsappInboundMessage"]["sendTime"] = "2026-08-03T10:00:00.000Z"
        process_event(db, _store(db, second), provider=PROVIDER)

        threads = (
            db.execute(select(Conversation).where(Conversation.tenant_id == tenant.id))
            .scalars()
            .all()
        )
        assert len(threads) == 2
        assert sorted(thread.status for thread in threads) == ["closed", "open"]

    def test_the_same_person_at_two_businesses_gets_two_threads(self, db: Session) -> None:
        """One customer, two shops. Neither may see the other's conversation."""
        tenant_a, phone_a = _tenant_with_number(db)
        tenant_b, phone_b = _tenant_with_number(db)

        process_event(db, _store(db, _inbound(phone_a, text="Para A")), provider=PROVIDER)
        process_event(db, _store(db, _inbound(phone_b, text="Para B")), provider=PROVIDER)

        for tenant in (tenant_a, tenant_b):
            messages = (
                db.execute(select(Message).where(Message.tenant_id == tenant.id)).scalars().all()
            )
            assert len(messages) == 1


class TestARedelivery:
    def test_a_second_job_for_a_processed_event_does_nothing(self, db: Session) -> None:
        """Terminal states are terminal. Re-applying an inbound message here
        is how one customer gets answered twice."""
        tenant, phone = _tenant_with_number(db)
        event_id = _store(db, _inbound(phone))
        process_event(db, event_id, provider=PROVIDER)

        outcome = process_event(db, event_id, provider=PROVIDER)

        assert outcome.outcome == OUTCOME_ALREADY_DONE
        messages = db.execute(select(Message).where(Message.tenant_id == tenant.id)).scalars().all()
        assert len(messages) == 1

    def test_the_same_message_under_two_event_ids_is_stored_once(self, db: Session) -> None:
        """RF-002 again, one level down. A provider that re-sends a message as
        a *new* event gets past the event check, and the unique constraint on
        `provider_message_id` is what catches it."""
        tenant, phone = _tenant_with_number(db)
        payload = _inbound(phone)

        first = process_event(db, _store(db, payload), provider=PROVIDER)
        second = process_event(db, _store(db, dict(payload)), provider=PROVIDER)

        messages = db.execute(select(Message).where(Message.tenant_id == tenant.id)).scalars().all()
        assert len(messages) == 1
        assert first.inbound_message_id is not None
        # And the duplicate reports nothing to answer, so the customer is not
        # replied to twice.
        assert second.inbound_message_id is None


class TestADeliveryReport:
    def _sent_message(self, db: Session) -> tuple[Tenant, Message]:
        tenant, phone = _tenant_with_number(db)
        conversation = Conversation(tenant_id=tenant.id, channel="whatsapp", status="open")
        db.add(conversation)
        db.flush()
        message = Message(
            tenant_id=tenant.id,
            conversation_id=conversation.id,
            direction="outbound",
            author="agent",
            content="Si, tenemos.",
            msg_type="text",
            provider_message_id=load("ycloud/message_delivered.json")["whatsappMessage"]["id"],
            delivery_status="sent",
        )
        db.add(message)
        db.commit()
        return tenant, message

    def test_it_advances_the_delivery_state(self, db: Session) -> None:
        _, message = self._sent_message(db)

        process_event(db, _store(db, load("ycloud/message_delivered.json")), provider=PROVIDER)

        db.refresh(message)
        assert message.delivery_status == "delivered"

    def test_it_is_written_into_the_history(self, db: Session) -> None:
        """`delivery_status` says where it got to; this says how - the only
        thing that answers "they say it never arrived" three days later."""
        _, message = self._sent_message(db)

        process_event(db, _store(db, load("ycloud/message_delivered.json")), provider=PROVIDER)

        events = (
            db.execute(
                select(MessageStatusEvent).where(MessageStatusEvent.message_id == message.id)
            )
            .scalars()
            .all()
        )
        assert [event.status for event in events] == ["delivered"]

    def test_a_report_for_a_message_we_never_sent_is_not_a_failure(self, db: Session) -> None:
        """The provider is entitled to report on things we have no record of,
        and retrying five times will not change that."""
        outcome = process_event(
            db, _store(db, load("ycloud/message_delivered.json")), provider=PROVIDER
        )

        assert outcome.outcome == OUTCOME_APPLIED
        event = WebhookEventRepository(db).get(outcome.event_id)
        assert event is not None
        assert event.status == "processed"


class TestAQualityChange:
    def test_it_is_recorded_against_the_number(self, db: Session) -> None:
        tenant, phone = _tenant_with_number(db)
        payload = load("ycloud/quality_updated.json")
        payload["whatsappPhoneNumber"]["phoneNumber"] = phone

        process_event(db, _store(db, payload), provider=PROVIDER)

        number = db.execute(
            select(WhatsAppNumber).where(WhatsAppNumber.phone_e164 == phone)
        ).scalar_one()
        assert number.quality_rating == "RED"


class TestNothingIsEverDiscarded:
    def test_a_message_to_a_number_nobody_owns_becomes_a_dead_letter(self, db: Session) -> None:
        """Not a shrug: it means a number was disconnected while somebody was
        still writing to it, or this deployment is receiving another one's
        traffic. Either way a person has to see it."""
        payload = _inbound("+51999999999")

        outcome = process_event(db, _store(db, payload), provider=PROVIDER)

        assert outcome.outcome == OUTCOME_UNKNOWN_NUMBER
        event = WebhookEventRepository(db).get(outcome.event_id)
        assert event is not None
        assert event.status == "dead_letter"
        # The payload survives, so it can be replayed once the number is
        # connected.
        assert event.payload == payload

    def test_an_event_this_version_ignores_is_finished_not_retried(self, db: Session) -> None:
        """A template status. Complete, valid, and no behaviour attached -
        attempting it again produces the same nothing."""
        outcome = process_event(
            db, _store(db, load("ycloud/template_status.json")), provider=PROVIDER
        )

        assert outcome.outcome == OUTCOME_IGNORED
        event = WebhookEventRepository(db).get(outcome.event_id)
        assert event is not None
        assert event.status == "processed"

    def test_a_job_for_an_event_that_no_longer_exists_is_survivable(self, db: Session) -> None:
        outcome = process_event(db, uuid.uuid4(), provider=PROVIDER)

        assert outcome.outcome == OUTCOME_MISSING


class TestWhenOurOwnCodeBreaks:
    class _ExplodingProvider:
        """Stands in for a bug reached only by a particular payload."""

        @property
        def name(self) -> str:
            return "ycloud"

        def parse_event(self, payload: dict[str, object]) -> object:
            raise ValueError("a bug, not a provider problem")

    def test_the_attempt_is_counted_and_the_event_kept(self, db: Session) -> None:
        """Nothing may escape to ARQ. An exception there loses the attempt
        count with the transaction, and the event either spins forever or
        stops being retried with no record of why."""
        _, phone = _tenant_with_number(db)
        event_id = _store(db, _inbound(phone))

        outcome = process_event(db, event_id, provider=self._ExplodingProvider())  # type: ignore[arg-type]

        assert outcome.outcome == OUTCOME_UNREADABLE
        event = WebhookEventRepository(db).get(event_id)
        assert event is not None
        assert event.attempts == 1
        assert event.status == "failed"
        assert event.error_msg is not None
        assert "ValueError" in event.error_msg

    def test_it_gives_up_loudly_after_enough_attempts(self, db: Session) -> None:
        """`dead_letter` is not a discard: the raw payload is still there and
        an admin can replay it once whatever broke is fixed."""
        _, phone = _tenant_with_number(db)
        event_id = _store(db, _inbound(phone))

        for _ in range(MAX_WEBHOOK_ATTEMPTS):
            process_event(db, event_id, provider=self._ExplodingProvider())  # type: ignore[arg-type]

        event = WebhookEventRepository(db).get(event_id)
        assert event is not None
        assert event.attempts == MAX_WEBHOOK_ATTEMPTS
        assert event.status == "dead_letter"

    def test_a_failed_event_wrote_nothing_by_halves(self, db: Session) -> None:
        """One event is one transaction. A message stored without the contact
        that owns it would be worse than the failure itself."""
        tenant, phone = _tenant_with_number(db)
        event_id = _store(db, _inbound(phone))

        process_event(db, event_id, provider=self._ExplodingProvider())  # type: ignore[arg-type]

        messages = db.execute(select(Message).where(Message.tenant_id == tenant.id)).scalars().all()
        assert messages == []


@pytest.mark.parametrize("hours", [1, 23])
def test_a_reply_within_the_window_keeps_the_thread(db: Session, hours: int) -> None:
    """The boundary the whole 24-hour rule turns on, from both sides."""
    tenant, phone = _tenant_with_number(db)
    first = _inbound(phone, text="Hola")
    first["whatsappInboundMessage"]["sendTime"] = "2026-08-01T10:00:00.000Z"
    process_event(db, _store(db, first), provider=PROVIDER)

    second = _inbound(phone, text="Otra pregunta")
    later = datetime(2026, 8, 1, 10, tzinfo=UTC) + timedelta(hours=hours)
    second["whatsappInboundMessage"]["sendTime"] = later.isoformat().replace("+00:00", "Z")
    process_event(db, _store(db, second), provider=PROVIDER)

    threads = (
        db.execute(select(Conversation).where(Conversation.tenant_id == tenant.id)).scalars().all()
    )
    assert len(threads) == 1
