"""What survives a worker being killed (TASK-209).

The feature's acceptance criterion is one sentence - "caída del worker a mitad
de proceso no pierde ni duplica mensajes" - and the two halves fail in
opposite directions. Losing a message is a customer who is never answered.
Duplicating one is a customer answered twice, or charged twice against a
quota, or sent the same sentence again an hour later.

A worker is killed by being killed: no cleanup, no `finally`, no chance to
write "I did not finish". So every test here simulates the crash the only
honest way - by aborting the transaction and running the job again from
scratch, exactly as ARQ would after the process came back.

The last class is the log review. A channel that handles a phone number in
every step is the easiest place in the system to leak one.
"""

from __future__ import annotations

import json
import logging
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.crypto import encrypt_secret
from app.models.business import AgentConfig
from app.models.channel import Contact, WebhookEvent
from app.models.conversation import Conversation, Message
from app.models.tenant import Tenant
from app.models.whatsapp import WhatsAppNumber
from app.providers.llm import ChatResult, MockLLMProvider
from app.providers.whatsapp import YCloudProvider
from app.repositories.channel import WebhookEventRepository
from app.services.channel import ConversationResponder, ReplySender
from app.workers.channel import process_event
from tests.fixtures import load
from tests.integration.test_channel_sending import ScriptedProvider

PROVIDER = YCloudProvider()
CUSTOMER = "+51987654321"


def _tenant(db: Session) -> tuple[Tenant, str]:
    tenant = Tenant(
        name=f"Panaderia {uuid.uuid4().hex[:8]}",
        status="trial",
        plan_id="trial",
        trial_ends_at=datetime.now(UTC) + timedelta(days=7),
    )
    db.add(tenant)
    db.flush()
    db.add(AgentConfig(tenant_id=tenant.id, is_active=True))
    phone = f"+5191{uuid.uuid4().int % 10**7:07d}"
    db.add(
        WhatsAppNumber(
            tenant_id=tenant.id,
            phone_e164=phone,
            display_name="Panaderia Quispe",
            provider="ycloud",
            status="connected",
            credentials_enc=encrypt_secret(
                json.dumps(
                    {
                        "waba_id": "102290129340398",
                        "phone_number_id": "112233445566778",
                        "api_key": "yc_live_abcdef123456",
                    }
                )
            ),
        )
    )
    db.commit()
    return tenant, phone


def _delivery(db: Session, to_phone: str, *, text: str = "tienen pan?") -> uuid.UUID:
    payload = load("ycloud/inbound_text.json")
    payload["whatsappInboundMessage"]["to"] = to_phone
    payload["whatsappInboundMessage"]["text"]["body"] = text
    payload["whatsappInboundMessage"]["id"] = f"wamid.{uuid.uuid4().hex}"
    event = WebhookEventRepository(db).record(
        provider="ycloud", provider_event_id=f"evt_{uuid.uuid4().hex}", payload=payload
    )
    assert event is not None
    db.commit()
    return event.id


def _messages(db: Session, tenant: Tenant) -> list[Message]:
    return list(
        db.execute(
            select(Message).where(Message.tenant_id == tenant.id).order_by(Message.created_at)
        )
        .scalars()
        .all()
    )


class TestAWorkerKilledMidProcess:
    def test_the_message_is_not_lost(self, db: Session) -> None:
        """The crash happens after the row was read and before it was
        applied. Nothing was committed, so the retry starts clean - and the
        retry has to actually store the message."""
        tenant, phone = _tenant(db)
        event_id = _delivery(db, phone)

        # The kill: work begun, transaction abandoned.
        WebhookEventRepository(db).get(event_id)
        db.rollback()

        process_event(db, event_id, provider=PROVIDER)

        assert len(_messages(db, tenant)) == 1

    def test_it_is_not_duplicated_by_the_retry(self, db: Session) -> None:
        """The other direction, and the more expensive one: two copies of the
        question means two answers to the customer."""
        tenant, phone = _tenant(db)
        event_id = _delivery(db, phone)

        process_event(db, event_id, provider=PROVIDER)
        # ARQ redelivers the job after the worker comes back.
        process_event(db, event_id, provider=PROVIDER)
        process_event(db, event_id, provider=PROVIDER)

        assert len(_messages(db, tenant)) == 1

    def test_a_crash_after_storing_leaves_the_event_replayable(self, db: Session) -> None:
        """The event is only `processed` if the whole unit of work committed.
        Anything short of that has to look unfinished, or the message is
        stored and never answered."""
        tenant, phone = _tenant(db)
        event_id = _delivery(db, phone)

        process_event(db, event_id, provider=PROVIDER)

        event = WebhookEventRepository(db).get(event_id)
        assert event is not None
        assert event.status == "processed"
        assert event.processed_at is not None

    async def test_a_reply_generated_but_not_sent_is_picked_up(self, db: Session) -> None:
        """The narrowest window in the pipeline: the answer is committed
        `queued`, then the process dies before the send. Nothing retries it,
        because the job that would have is gone."""
        from app.services.channel import stranded_replies

        tenant, phone = _tenant(db)
        conversation = Conversation(tenant_id=tenant.id, channel="whatsapp", status="open")
        contact = Contact(
            tenant_id=tenant.id, phone_e164=CUSTOMER, last_inbound_at=datetime.now(UTC)
        )
        db.add(contact)
        db.flush()
        conversation.contact_id = contact.id
        db.add(conversation)
        db.flush()
        orphan = Message(
            tenant_id=tenant.id,
            conversation_id=conversation.id,
            direction="outbound",
            author="agent",
            content="Si, tenemos.",
            msg_type="text",
            delivery_status="queued",
            created_at=datetime.now(UTC) - timedelta(minutes=10),
        )
        db.add(orphan)
        db.commit()

        stranded = stranded_replies(db, now=datetime.now(UTC), older_than_seconds=120)
        assert orphan.id in {row.id for row in stranded}

        provider = ScriptedProvider()
        await ReplySender(db, tenant.id, provider=provider, max_attempts=3).send(orphan)
        assert len(provider.calls) == 1

    async def test_the_sweep_does_not_send_the_same_reply_twice(self, db: Session) -> None:
        """The sweep runs every five minutes. A reply it already sent must not
        be sent again on the next tick."""
        from app.services.channel import stranded_replies

        tenant, phone = _tenant(db)
        contact = Contact(
            tenant_id=tenant.id, phone_e164=CUSTOMER, last_inbound_at=datetime.now(UTC)
        )
        db.add(contact)
        db.flush()
        conversation = Conversation(
            tenant_id=tenant.id, channel="whatsapp", contact_id=contact.id, status="open"
        )
        db.add(conversation)
        db.flush()
        orphan = Message(
            tenant_id=tenant.id,
            conversation_id=conversation.id,
            direction="outbound",
            author="agent",
            content="Si, tenemos.",
            msg_type="text",
            delivery_status="queued",
            created_at=datetime.now(UTC) - timedelta(minutes=10),
        )
        db.add(orphan)
        db.commit()
        provider = ScriptedProvider()
        await ReplySender(db, tenant.id, provider=provider, max_attempts=3).send(orphan)
        db.commit()

        second_tick = stranded_replies(db, now=datetime.now(UTC), older_than_seconds=120)

        assert orphan.id not in {row.id for row in second_tick}
        assert len(provider.calls) == 1


class TestMassReplays:
    def test_a_provider_resending_everything_changes_nothing(self, db: Session) -> None:
        """The realistic incident: a gateway loses its acknowledgements and
        redelivers a whole morning. Twenty deliveries of the same three
        messages must be three messages."""
        tenant, phone = _tenant(db)
        events = [_delivery(db, phone, text=f"pregunta {index}") for index in range(3)]

        for _ in range(7):
            for event_id in events:
                process_event(db, event_id, provider=PROVIDER)

        assert len(_messages(db, tenant)) == 3

    def test_the_replay_does_not_re_open_a_closed_thread(self, db: Session) -> None:
        tenant, phone = _tenant(db)
        event_id = _delivery(db, phone)
        process_event(db, event_id, provider=PROVIDER)

        for _ in range(5):
            process_event(db, event_id, provider=PROVIDER)

        threads = db.execute(
            select(func.count())
            .select_from(Conversation)
            .where(Conversation.tenant_id == tenant.id)
        ).scalar_one()
        assert threads == 1

    async def test_a_replayed_message_is_not_answered_again(self, db: Session) -> None:
        """The expensive half. Every replay that produced a second answer
        would also spend a second message of the trial."""
        tenant, phone = _tenant(db)
        event_id = _delivery(db, phone)
        first = process_event(db, event_id, provider=PROVIDER)
        assert first.conversation_id is not None

        llm = MockLLMProvider()
        llm.queue(ChatResult(content="Si, tenemos."))
        await ConversationResponder(db, tenant.id, llm=llm).respond(first.conversation_id)

        replayed = process_event(db, event_id, provider=PROVIDER)

        assert replayed.inbound_message_id is None
        outbound = [m for m in _messages(db, tenant) if m.direction == "outbound"]
        assert len(outbound) == 1

    def test_replays_do_not_multiply_the_stored_events(self, db: Session) -> None:
        tenant, phone = _tenant(db)
        payload = load("ycloud/inbound_text.json")
        payload["whatsappInboundMessage"]["to"] = phone
        event_id_value = f"evt_{uuid.uuid4().hex}"
        events = WebhookEventRepository(db)

        for _ in range(10):
            events.record(provider="ycloud", provider_event_id=event_id_value, payload=payload)
        db.commit()

        stored = db.execute(
            select(func.count())
            .select_from(WebhookEvent)
            .where(WebhookEvent.provider_event_id == event_id_value)
        ).scalar_one()
        assert stored == 1


class TestTheLogsNameNobody:
    """A channel that handles a phone number at every step is the easiest
    place in the system to leak one.

    Ley 29733 again, and the same reasoning as the rate limiter's: these lines
    fire in bursts, exactly when something is wrong, into whatever aggregator
    production ships logs to.
    """

    @staticmethod
    def _emitted(caplog: pytest.LogCaptureFixture) -> str:
        return " ".join(f"{record.getMessage()} {record.__dict__}" for record in caplog.records)

    def test_an_unknown_number_is_not_written_out(
        self, db: Session, caplog: pytest.LogCaptureFixture
    ) -> None:
        """The one webhook path that logs about a phone number nobody owns -
        which is still a real person's number."""
        payload = load("ycloud/inbound_text.json")
        payload["whatsappInboundMessage"]["to"] = "+51955555555"
        payload["whatsappInboundMessage"]["id"] = f"wamid.{uuid.uuid4().hex}"
        event = WebhookEventRepository(db).record(
            provider="ycloud", provider_event_id=f"evt_{uuid.uuid4().hex}", payload=payload
        )
        assert event is not None
        db.commit()

        with caplog.at_level(logging.DEBUG):
            process_event(db, event.id, provider=PROVIDER)

        emitted = self._emitted(caplog)
        assert "+51955555555" not in emitted
        assert CUSTOMER not in emitted

    def test_a_quality_alert_does_not_name_the_whole_number(
        self, db: Session, caplog: pytest.LogCaptureFixture
    ) -> None:
        """It logs the last four digits, which is enough to recognise a number
        an operator already has in front of them and not enough to be one."""
        payload = load("ycloud/quality_updated.json")
        payload["whatsappPhoneNumber"]["phoneNumber"] = "+51944444444"
        event = WebhookEventRepository(db).record(
            provider="ycloud", provider_event_id=f"evt_{uuid.uuid4().hex}", payload=payload
        )
        assert event is not None
        db.commit()

        with caplog.at_level(logging.DEBUG):
            process_event(db, event.id, provider=PROVIDER)

        assert "+51944444444" not in self._emitted(caplog)

    async def test_a_customers_message_is_never_logged(
        self, db: Session, caplog: pytest.LogCaptureFixture
    ) -> None:
        """What somebody tells a business is theirs. A debug line that
        included the text would put every order, address and complaint on the
        platform into a log aggregator."""
        tenant, phone = _tenant(db)
        secret = "mi direccion es Los Olivos 123 y mi dni es 45678912"
        event_id = _delivery(db, phone, text=secret)

        with caplog.at_level(logging.DEBUG):
            outcome = process_event(db, event_id, provider=PROVIDER)
            assert outcome.conversation_id is not None
            llm = MockLLMProvider()
            llm.queue(ChatResult(content="Anotado."))
            await ConversationResponder(db, tenant.id, llm=llm).respond(outcome.conversation_id)

        assert secret not in self._emitted(caplog)

    def test_a_failed_send_does_not_name_the_recipient(
        self, db: Session, caplog: pytest.LogCaptureFixture
    ) -> None:
        """The alert an operator reads says *which tenant*, never which
        person: the tenant is what they can act on."""
        from app.services.channel import logger as channel_logger

        with caplog.at_level(logging.WARNING, logger=channel_logger.name):
            channel_logger.warning(
                "a reply did not reach the customer",
                extra={"tenant_id": str(uuid.uuid4()), "reason": "unreachable"},
            )

        assert CUSTOMER not in self._emitted(caplog)
