"""The rules that protect the number and the person (TASK-206, HU-03).

Three of them, and none is about making the product better. They are what
keeps a customer's WhatsApp number usable and what keeps the platform on the
right side of somebody who asked to be left alone.

* **The 24-hour window.** Past it Meta will not carry a free-form message, and
  sending anyway is not a failed request - it is a policy violation charged
  against the number's standing.
* **Opt-out.** Somebody who writes STOP is not answered again. Somebody who
  writes anything else afterwards has restarted the conversation themselves.
* **Quality.** A drop to RED is the last warning before the number is limited
  or cut off, and the owner cannot see it anywhere else: the rating lives in
  Meta's console, which an assisted connection gives them no access to.
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
from app.models.business import AgentConfig
from app.models.channel import SESSION_WINDOW_HOURS, Contact
from app.models.conversation import Conversation, Message
from app.models.tenant import Tenant, TenantUser
from app.models.user import User
from app.models.whatsapp import WhatsAppNumber
from app.providers.llm import ChatResult, MockLLMProvider
from app.providers.whatsapp import YCloudProvider
from app.repositories.channel import WebhookEventRepository
from app.services.channel import (
    ConversationResponder,
    ReplySender,
    SessionWindowClosed,
    is_opt_out_request,
)
from app.workers.channel import process_event
from tests.fixtures import load
from tests.integration.test_channel_sending import ScriptedProvider

PROVIDER = YCloudProvider()
CUSTOMER = "+51987654321"


def _tenant(db: Session, *, owner_email: str | None = None) -> tuple[Tenant, str]:
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
    number = WhatsAppNumber(
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
    db.add(number)
    if owner_email:
        user = User(
            email=owner_email,
            full_name="Ana Quispe",
            password_hash="x",
            email_verified_at=datetime.now(UTC),
        )
        db.add(user)
        db.flush()
        db.add(TenantUser(tenant_id=tenant.id, user_id=user.id, role="owner", status="active"))
    db.commit()
    return tenant, phone


def _thread(db: Session, tenant: Tenant, *, last_inbound: datetime | None = None) -> Conversation:
    contact = Contact(
        tenant_id=tenant.id,
        phone_e164=CUSTOMER,
        last_inbound_at=last_inbound or datetime.now(UTC),
    )
    db.add(contact)
    db.flush()
    conversation = Conversation(
        tenant_id=tenant.id, channel="whatsapp", contact_id=contact.id, status="open"
    )
    db.add(conversation)
    db.flush()
    return conversation


def _store(db: Session, payload: dict[str, Any]) -> uuid.UUID:
    event = WebhookEventRepository(db).record(
        provider="ycloud", provider_event_id=f"evt_{uuid.uuid4().hex}", payload=payload
    )
    assert event is not None
    db.commit()
    return event.id


def _inbound(to_phone: str, text: str) -> dict[str, Any]:
    payload = load("ycloud/inbound_text.json")
    payload["whatsappInboundMessage"]["to"] = to_phone
    payload["whatsappInboundMessage"]["text"]["body"] = text
    payload["whatsappInboundMessage"]["id"] = f"wamid.{uuid.uuid4().hex}"
    return payload


class TestRecognisingAnOptOut:
    @pytest.mark.parametrize("text", ["STOP", "stop", "Baja", "BAJA.", " baja "])
    def test_the_words_people_actually_type(self, text: str) -> None:
        assert is_opt_out_request(text, ["stop", "baja"]) is True

    @pytest.mark.parametrize(
        "text",
        [
            "tienen bajativos?",
            "no paren de atender",
            "stop the count",
            "quiero darme de baja del delivery pero no del catalogo",
        ],
    )
    def test_a_sentence_that_merely_contains_the_word_is_not_one(self, text: str) -> None:
        """Substring matching would silence a customer asking about
        bajativos, and they could never work out why nobody answers."""
        assert is_opt_out_request(text, ["stop", "baja"]) is False

    def test_a_tenant_can_add_their_own(self) -> None:
        assert is_opt_out_request("NO MOLESTAR", ["stop", "baja", "no molestar"]) is True

    def test_nothing_matches_when_none_are_configured(self) -> None:
        assert is_opt_out_request("stop", []) is False

    def test_a_media_message_is_never_an_opt_out(self) -> None:
        assert is_opt_out_request(None, ["stop"]) is False


class TestHonouringAnOptOut:
    def test_writing_stop_marks_the_contact(self, db: Session) -> None:
        tenant, phone = _tenant(db)

        process_event(db, _store(db, _inbound(phone, "BAJA")), provider=PROVIDER)

        contact = db.execute(select(Contact).where(Contact.tenant_id == tenant.id)).scalar_one()
        assert contact.opted_out_at is not None

    def test_the_message_is_still_stored(self, db: Session) -> None:
        """The owner may want to see that somebody left, and why."""
        tenant, phone = _tenant(db)

        process_event(db, _store(db, _inbound(phone, "BAJA")), provider=PROVIDER)

        messages = db.execute(select(Message).where(Message.tenant_id == tenant.id)).scalars().all()
        assert [message.content for message in messages] == ["BAJA"]

    async def test_the_agent_does_not_answer_somebody_who_opted_out(self, db: Session) -> None:
        tenant, _ = _tenant(db)
        conversation = _thread(db, tenant)
        contact = db.execute(select(Contact).where(Contact.tenant_id == tenant.id)).scalar_one()
        contact.opted_out_at = datetime.now(UTC)
        db.add(
            Message(
                tenant_id=tenant.id,
                conversation_id=conversation.id,
                direction="inbound",
                author="customer",
                content="hola?",
                msg_type="text",
            )
        )
        db.flush()
        llm = MockLLMProvider()
        llm.queue(ChatResult(content="no deberia contestar"))

        answer = await ConversationResponder(db, tenant.id, llm=llm).respond(conversation.id)

        assert answer is None
        assert llm.chat_calls == []

    def test_writing_again_brings_them_back(self, db: Session) -> None:
        """Keeping somebody silenced over a word they typed in March means
        their next order goes unanswered."""
        tenant, phone = _tenant(db)
        process_event(db, _store(db, _inbound(phone, "BAJA")), provider=PROVIDER)

        process_event(db, _store(db, _inbound(phone, "hola, cambie de opinion")), provider=PROVIDER)

        contact = db.execute(select(Contact).where(Contact.tenant_id == tenant.id)).scalar_one()
        assert contact.opted_out_at is None

    def test_the_keywords_come_from_the_tenants_configuration(self, db: Session) -> None:
        tenant, phone = _tenant(db)
        config = db.get(AgentConfig, tenant.id)
        assert config is not None
        config.opt_out_keywords = ["no molestar"]
        db.commit()

        process_event(db, _store(db, _inbound(phone, "no molestar")), provider=PROVIDER)

        contact = db.execute(select(Contact).where(Contact.tenant_id == tenant.id)).scalar_one()
        assert contact.opted_out_at is not None

    def test_a_word_the_tenant_removed_no_longer_silences_anyone(self, db: Session) -> None:
        tenant, phone = _tenant(db)
        config = db.get(AgentConfig, tenant.id)
        assert config is not None
        config.opt_out_keywords = ["no molestar"]
        db.commit()

        process_event(db, _store(db, _inbound(phone, "stop")), provider=PROVIDER)

        contact = db.execute(select(Contact).where(Contact.tenant_id == tenant.id)).scalar_one()
        assert contact.opted_out_at is None


class TestTheSessionWindow:
    async def test_a_reply_inside_the_window_goes_out(self, db: Session) -> None:
        tenant, _ = _tenant(db)
        conversation = _thread(db, tenant, last_inbound=datetime.now(UTC) - timedelta(hours=1))
        message = Message(
            tenant_id=tenant.id,
            conversation_id=conversation.id,
            direction="outbound",
            author="agent",
            content="Si, tenemos.",
            msg_type="text",
            delivery_status="queued",
        )
        db.add(message)
        db.flush()
        provider = ScriptedProvider()

        await ReplySender(db, tenant.id, provider=provider, max_attempts=3).send(message)

        assert len(provider.calls) == 1

    async def test_a_reply_past_the_window_is_refused_before_it_is_sent(self, db: Session) -> None:
        """Not because it is wrong, but because Meta will not carry it and
        trying is charged against the number's standing."""
        tenant, _ = _tenant(db)
        conversation = _thread(
            db,
            tenant,
            last_inbound=datetime.now(UTC) - timedelta(hours=SESSION_WINDOW_HOURS + 1),
        )
        message = Message(
            tenant_id=tenant.id,
            conversation_id=conversation.id,
            direction="outbound",
            author="agent",
            content="Perdon la demora.",
            msg_type="text",
            delivery_status="queued",
        )
        db.add(message)
        db.flush()
        provider = ScriptedProvider()

        with pytest.raises(SessionWindowClosed):
            await ReplySender(db, tenant.id, provider=provider, max_attempts=3).send(message)

        assert provider.calls == []

    async def test_the_refused_reply_is_marked_failed_not_left_queued(self, db: Session) -> None:
        """Nothing later makes it deliverable, and an answer stuck in the
        queue for a day is worse than one the owner can see did not go out."""
        tenant, _ = _tenant(db)
        conversation = _thread(
            db,
            tenant,
            last_inbound=datetime.now(UTC) - timedelta(hours=SESSION_WINDOW_HOURS + 1),
        )
        message = Message(
            tenant_id=tenant.id,
            conversation_id=conversation.id,
            direction="outbound",
            author="agent",
            content="Perdon la demora.",
            msg_type="text",
            delivery_status="queued",
        )
        db.add(message)
        db.flush()

        with pytest.raises(SessionWindowClosed):
            await ReplySender(db, tenant.id, provider=ScriptedProvider(), max_attempts=3).send(
                message
            )

        assert message.delivery_status == "failed"


class TestTheQualityRating:
    def test_a_drop_to_red_warns_the_owner(self, db: Session) -> None:
        """They cannot learn it anywhere else: the rating lives in Meta's
        console, which an assisted connection gives them no access to."""
        email = f"ana-{uuid.uuid4().hex[:10]}@example.com"
        tenant, phone = _tenant(db, owner_email=email)
        payload = load("ycloud/quality_updated.json")
        payload["whatsappPhoneNumber"]["phoneNumber"] = phone

        outcome = process_event(db, _store(db, payload), provider=PROVIDER)

        assert len(outcome.emails) == 1
        assert outcome.emails[0].to == (email,)
        assert "RED" in outcome.emails[0].text

    def test_the_same_rating_reported_twice_warns_once(self, db: Session) -> None:
        """Alerting on the level rather than the transition would mail the
        customer every time the provider repeated itself, and an alert that
        arrives daily is one nobody reads on the day it matters."""
        email = f"ana-{uuid.uuid4().hex[:10]}@example.com"
        tenant, phone = _tenant(db, owner_email=email)
        payload = load("ycloud/quality_updated.json")
        payload["whatsappPhoneNumber"]["phoneNumber"] = phone

        first = process_event(db, _store(db, payload), provider=PROVIDER)
        second = process_event(db, _store(db, dict(payload)), provider=PROVIDER)

        assert len(first.emails) == 1
        assert second.emails == ()

    def test_a_healthy_rating_warns_nobody(self, db: Session) -> None:
        email = f"ana-{uuid.uuid4().hex[:10]}@example.com"
        tenant, phone = _tenant(db, owner_email=email)
        payload = load("ycloud/quality_updated.json")
        payload["whatsappPhoneNumber"]["phoneNumber"] = phone
        payload["whatsappPhoneNumber"]["qualityRating"] = "GREEN"

        outcome = process_event(db, _store(db, payload), provider=PROVIDER)

        assert outcome.emails == ()

    def test_recovering_and_dropping_again_warns_again(self, db: Session) -> None:
        """The second drop is as urgent as the first."""
        email = f"ana-{uuid.uuid4().hex[:10]}@example.com"
        tenant, phone = _tenant(db, owner_email=email)

        def _report(rating: str) -> dict[str, Any]:
            payload = load("ycloud/quality_updated.json")
            payload["whatsappPhoneNumber"]["phoneNumber"] = phone
            payload["whatsappPhoneNumber"]["qualityRating"] = rating
            return payload

        process_event(db, _store(db, _report("RED")), provider=PROVIDER)
        process_event(db, _store(db, _report("GREEN")), provider=PROVIDER)
        again = process_event(db, _store(db, _report("RED")), provider=PROVIDER)

        assert len(again.emails) == 1

    def test_the_rating_is_recorded_even_with_no_owner_to_warn(self, db: Session) -> None:
        """The panel and the admin read it from the number either way."""
        tenant, phone = _tenant(db)
        payload = load("ycloud/quality_updated.json")
        payload["whatsappPhoneNumber"]["phoneNumber"] = phone

        process_event(db, _store(db, payload), provider=PROVIDER)

        number = db.execute(
            select(WhatsAppNumber).where(WhatsAppNumber.phone_e164 == phone)
        ).scalar_one()
        assert number.quality_rating == "RED"
