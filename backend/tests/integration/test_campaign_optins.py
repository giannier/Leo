"""Permission for promotions: by button after a sale, revoked by BAJA, imported with a
declaration (Feature 011, HU-02, C-102, C-103, C-001)."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.business import AgentConfig
from app.models.campaigns import ContactOptin, OptinImport
from app.models.channel import Contact
from app.models.conversation import Conversation, Message
from app.models.tenant import Tenant
from app.models.whatsapp import WhatsAppNumber
from app.providers.whatsapp.ycloud import YCloudProvider
from app.repositories.campaigns import ContactTagRepository
from app.repositories.channel import WebhookEventRepository
from app.repositories.orders import OrderRepository
from app.services.campaigns import CampaignError
from app.services.campaigns.optins import (
    ANSWER_NO,
    ANSWER_YES,
    BUTTON_NO,
    BUTTON_YES,
    OptinService,
)
from app.services.channel import ReplySender
from app.services.order_notices import CustomerNotifier
from app.workers.channel import process_event
from tests.fixtures import load
from tests.integration.campaign_fixtures import FakeWhatsApp, business, customer

PROVIDER = YCloudProvider()


def _thread(db: Session, tenant: Tenant, contact: Contact) -> Conversation:
    conversation = Conversation(
        tenant_id=tenant.id, channel="whatsapp", contact_id=contact.id, status="open"
    )
    db.add(conversation)
    db.flush()
    return conversation


def _optin(db: Session, contact: Contact) -> ContactOptin | None:
    return db.execute(
        select(ContactOptin).where(ContactOptin.contact_id == contact.id)
    ).scalar_one_or_none()


def _tap(db: Session, number: WhatsAppNumber, contact: Contact, button: str) -> Any:
    payload = load("ycloud/inbound_interactive_button.json")
    message = payload["whatsappInboundMessage"]
    message["id"] = uuid.uuid4().hex
    message["to"] = number.phone_e164
    message["from"] = contact.phone_e164
    message["sendTime"] = datetime.now(UTC).isoformat().replace("+00:00", "Z")
    message["interactive"]["button_reply"] = {"id": button, "title": "Sí, avísame"}
    event = WebhookEventRepository(db).record(
        provider="ycloud", provider_event_id=f"evt_{uuid.uuid4().hex}", payload=payload
    )
    assert event is not None
    db.flush()
    return process_event(db, event.id, provider=PROVIDER)


def _text(db: Session, number: WhatsAppNumber, contact: Contact, text: str) -> Any:
    payload = load("ycloud/inbound_text.json")
    message = payload["whatsappInboundMessage"]
    message["id"] = uuid.uuid4().hex
    message["to"] = number.phone_e164
    message["from"] = contact.phone_e164
    message["text"]["body"] = text
    message["sendTime"] = datetime.now(UTC).isoformat().replace("+00:00", "Z")
    event = WebhookEventRepository(db).record(
        provider="ycloud", provider_event_id=f"evt_{uuid.uuid4().hex}", payload=payload
    )
    assert event is not None
    db.flush()
    return process_event(db, event.id, provider=PROVIDER)


class TestTheQuestion:
    def test_it_is_asked_once_with_two_buttons(self, db: Session) -> None:
        tenant, _ = business(db, name="Panadería Rosa")
        contact = customer(db, tenant, permission=None)
        thread = _thread(db, tenant, contact)
        service = OptinService(db, tenant.id)

        question = service.prepare_question(thread, contact, now=datetime.now(UTC))
        assert question is not None and question.delivery_status == "queued"
        assert question.msg_type == "interactive" and question.payload is not None
        assert [b["id"] for b in question.payload["botones"]] == [BUTTON_YES, BUTTON_NO]
        assert "Panadería Rosa" in question.content
        optin = _optin(db, contact)
        assert optin is not None and optin.status == "asked"
        assert optin.evidence["mensaje_pregunta"] == str(question.id)

        assert service.prepare_question(thread, contact, now=datetime.now(UTC)) is None

    @pytest.mark.parametrize("case", ["apagado", "baja", "ventana", "simulador"])
    def test_when_it_is_not_asked(self, db: Session, case: str) -> None:
        tenant, _ = business(db)
        contact = customer(
            db,
            tenant,
            permission=None,
            opted_out=case == "baja",
            wrote_hours_ago=30 if case == "ventana" else 1,
        )
        thread = _thread(db, tenant, contact)
        if case == "apagado":
            config = db.get(AgentConfig, tenant.id)
            assert config is not None
            config.optin_preguntar = False
        if case == "simulador":
            thread.channel = "sandbox"
        db.flush()
        assert (
            OptinService(db, tenant.id).prepare_question(thread, contact, now=datetime.now(UTC))
            is None
        )

    async def test_it_follows_a_confirmed_payment_and_goes_as_buttons(self, db: Session) -> None:
        tenant, _ = business(db)
        contact = customer(db, tenant, permission=None)
        thread = _thread(db, tenant, contact)
        order = OrderRepository(db, tenant.id).add_numbered(
            conversation_id=thread.id,
            contact_id=contact.id,
            moneda="PEN",
            subtotal=Decimal("50"),
            total=Decimal("50"),
            estado="confirmado",
        )
        fake = FakeWhatsApp()
        notifier = CustomerNotifier(db, tenant.id, provider=fake)

        prepared = notifier.prepare(order, "pago_confirmado")
        assert prepared.extra is not None
        assert await notifier.deliver(prepared) == "enviado"

        assert len(fake.texts) == 1  # the confirmation
        [(phone, _body, ids)] = fake.buttons
        assert phone == contact.phone_e164 and ids == [BUTTON_YES, BUTTON_NO]

    async def test_a_rejection_does_not_ask(self, db: Session) -> None:
        tenant, _ = business(db)
        contact = customer(db, tenant, permission=None)
        thread = _thread(db, tenant, contact)
        order = OrderRepository(db, tenant.id).add_numbered(
            conversation_id=thread.id,
            contact_id=contact.id,
            moneda="PEN",
            subtotal=Decimal("50"),
            total=Decimal("50"),
            estado="descartado",
        )
        prepared = CustomerNotifier(db, tenant.id, provider=FakeWhatsApp()).prepare(
            order, "pedido_rechazado"
        )
        assert prepared.extra is None


class TestTheAnswer:
    @pytest.mark.parametrize(
        ("button", "status", "answer"),
        [
            (BUTTON_YES, "granted", ANSWER_YES),
            (BUTTON_NO, "declined", ANSWER_NO),
        ],
    )
    def test_a_tap_is_stored_and_answered_without_the_assistant(
        self, db: Session, button: str, status: str, answer: str
    ) -> None:
        tenant, number = business(db)
        contact = customer(db, tenant, permission=None)
        thread = _thread(db, tenant, contact)
        OptinService(db, tenant.id).prepare_question(thread, contact, now=datetime.now(UTC))

        outcome = _tap(db, number, contact, button)

        assert outcome.system_reply_id is not None
        reply = db.get(Message, outcome.system_reply_id)
        assert reply is not None and reply.content == answer and reply.delivery_status == "queued"
        optin = _optin(db, contact)
        assert optin is not None and optin.status == status
        assert optin.evidence["respuesta"] in ("si", "no")
        assert optin.evidence["mensaje_respuesta"] == str(outcome.inbound_message_id)

    def test_any_other_tap_goes_to_the_assistant(self, db: Session) -> None:
        tenant, number = business(db)
        contact = customer(db, tenant, permission=None)
        outcome = _tap(db, number, contact, "camino:comprar")
        assert outcome.system_reply_id is None and outcome.inbound_message_id is not None

    async def test_the_reply_sender_sends_the_buttons(self, db: Session) -> None:
        tenant, _ = business(db)
        contact = customer(db, tenant, permission=None)
        thread = _thread(db, tenant, contact)
        question = OptinService(db, tenant.id).prepare_question(
            thread, contact, now=datetime.now(UTC)
        )
        assert question is not None
        fake = FakeWhatsApp()
        await ReplySender(db, tenant.id, provider=fake, max_attempts=1).send(question)
        assert question.delivery_status == "sent" and len(fake.buttons) == 1


class TestBaja:
    def test_writing_baja_revokes_and_coming_back_does_not_restore(self, db: Session) -> None:
        tenant, number = business(db)
        contact = customer(db, tenant, permission="granted")

        _text(db, number, contact, "BAJA")
        optin = _optin(db, contact)
        assert optin is not None and optin.status == "revoked" and optin.revoked_at is not None

        _text(db, number, contact, "Hola, ¿tienen pan?")
        db.refresh(contact)
        assert contact.opted_out_at is None  # the assistant talks to them again (F002)
        assert _optin(db, contact).status == "revoked"  # type: ignore[union-attr]


CSV = (
    "nombre;telefono\n"
    "Ana Quispe;987654301\n"
    "Luis;+51 987 654 302\n"
    "Sin número;12\n"
    "Ana Quispe;987654301\n"
).encode()


class TestImport:
    def test_it_needs_the_declaration(self, db: Session) -> None:
        tenant, _ = business(db)
        with pytest.raises(CampaignError) as caught:
            OptinService(db, tenant.id).import_csv(
                CSV, filename="x.csv", accepted=False, user_id=None, tags=[]
            )
        assert caught.value.code == "sin_declaracion"

    def test_a_list_becomes_permission_with_its_evidence_and_tags(self, db: Session) -> None:
        tenant, _ = business(db)
        result = OptinService(db, tenant.id).import_csv(
            CSV, filename="clientes.csv", accepted=True, user_id=None, tags=["feria"]
        )
        assert sorted(result.imported) == ["+51987654301", "+51987654302"]
        assert [row["fila"] for row in result.invalid] == [4]
        contacts = list(db.execute(select(Contact).where(Contact.tenant_id == tenant.id)).scalars())
        assert len(contacts) == 2
        ana = next(c for c in contacts if c.phone_e164 == "+51987654301")
        assert ana.custom_data["nombres"] == "Ana Quispe"
        optin = _optin(db, ana)
        assert optin is not None and optin.status == "granted" and optin.source == "import"
        assert "Ley 29733" in optin.evidence["declaracion"]
        assert ContactTagRepository(db, tenant.id).for_contacts([ana.id]) == {ana.id: ["feria"]}
        [record] = db.execute(
            select(OptinImport).where(OptinImport.tenant_id == tenant.id)
        ).scalars()
        assert record.imported == 2 and record.skipped == 1

    def test_a_no_or_a_baja_is_never_turned_into_a_yes(self, db: Session) -> None:
        tenant, _ = business(db)
        customer(db, tenant, phone="+51987654301", permission="declined")
        customer(db, tenant, phone="+51987654302", permission=None, opted_out=True)
        result = OptinService(db, tenant.id).import_csv(
            CSV, filename="x.csv", accepted=True, user_id=None, tags=[]
        )
        assert result.imported == [] and sorted(result.refused) == ["+51987654301", "+51987654302"]

    def test_at_most_200_a_week(self, db: Session) -> None:
        tenant, _ = business(db)
        db.add(
            OptinImport(
                tenant_id=tenant.id,
                declaration="x",
                imported=199,
                skipped=0,
                created_at=datetime.now(UTC) - timedelta(days=2),
            )
        )
        db.flush()
        with pytest.raises(CampaignError) as caught:
            OptinService(db, tenant.id).import_csv(
                CSV, filename="x.csv", accepted=True, user_id=None, tags=[]
            )
        assert caught.value.code == "limite_semanal" and "te quedan 1" in str(caught.value)

    def test_a_file_without_a_phone_column_is_explained(self, db: Session) -> None:
        tenant, _ = business(db)
        with pytest.raises(CampaignError) as caught:
            OptinService(db, tenant.id).import_csv(
                b"nombre\nAna\n", filename="x.csv", accepted=True, user_id=None, tags=[]
            )
        assert caught.value.code == "sin_columna_telefono"
