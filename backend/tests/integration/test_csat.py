"""The satisfaction survey from start to end (Feature 013, C-102, C-103)."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.analytics import CsatSurvey
from app.models.channel import Contact
from app.models.conversation import Conversation, Message
from app.models.crm import Lead, PipelineStage
from app.models.metrics import DashboardSettings
from app.models.tenant import Tenant
from app.models.whatsapp import WhatsAppNumber
from app.providers.whatsapp.ycloud import YCloudProvider
from app.repositories.channel import WebhookEventRepository
from app.repositories.crm import PipelineStageRepository
from app.services.analytics import csat
from app.workers.channel import process_event
from tests.fixtures import load
from tests.integration.campaign_fixtures import business, customer

NOW = datetime.now(UTC)


def _won(db: Session, tenant: Tenant, contact: Contact, *, hours_ago: float = 2) -> Conversation:
    thread = Conversation(
        tenant_id=tenant.id, channel="whatsapp", contact_id=contact.id, status="open"
    )
    db.add(thread)
    db.flush()
    PipelineStageRepository(db, tenant.id).ensure_defaults()
    stage = db.execute(
        select(PipelineStage).where(
            PipelineStage.tenant_id == tenant.id, PipelineStage.semantic == "ganado"
        )
    ).scalar_one()
    db.add(
        Lead(
            tenant_id=tenant.id,
            contact_id=contact.id,
            conversation_id=thread.id,
            stage_id=stage.id,
            status="won",
            closed_at=NOW - timedelta(hours=hours_ago),
        )
    )
    db.flush()
    return thread


def _on(db: Session, tenant: Tenant) -> None:
    db.add(DashboardSettings(tenant_id=tenant.id, csat_activo=True))
    db.flush()


def _mine(db: Session, tenant: Tenant) -> list[csat.Candidate]:
    return [c for c in csat.candidates(db, now=NOW) if c.tenant_id == tenant.id]


def _says(db: Session, number: WhatsAppNumber, contact: Contact, text: str) -> Any:
    payload = load("ycloud/inbound_text.json")
    message = payload["whatsappInboundMessage"]
    message.update(id=uuid.uuid4().hex, to=number.phone_e164, **{"from": contact.phone_e164})
    message["text"]["body"] = text
    message["sendTime"] = datetime.now(UTC).isoformat().replace("+00:00", "Z")
    event = WebhookEventRepository(db).record(
        provider="ycloud", provider_event_id=f"evt_{uuid.uuid4().hex}", payload=payload
    )
    assert event is not None
    db.flush()
    return process_event(db, event.id, provider=YCloudProvider())


class TestWhenItGoes:
    def test_off_by_default(self, db: Session) -> None:
        tenant, _ = business(db)
        _won(db, tenant, customer(db, tenant))
        assert _mine(db, tenant) == []

    def test_an_hour_after_a_sale_or_a_close(self, db: Session) -> None:
        tenant, _ = business(db)
        _on(db, tenant)
        sold = _won(db, tenant, customer(db, tenant))
        _won(db, tenant, customer(db, tenant), hours_ago=0.5)  # too soon
        closed = Conversation(
            tenant_id=tenant.id,
            channel="whatsapp",
            contact_id=customer(db, tenant).id,
            status="closed",
        )
        db.add(closed)
        db.flush()
        db.add(
            Message(
                tenant_id=tenant.id,
                conversation_id=closed.id,
                direction="outbound",
                author="system",
                content="Ana cerró la conversación",
                msg_type="event",
                payload={"evento": "cerrada"},
                created_at=NOW - timedelta(hours=3),
            )
        )
        db.flush()
        found = {(c.conversation_id, c.trigger) for c in _mine(db, tenant)}
        assert found == {(sold.id, "ganado"), (closed.id, "cerrada")}

    @pytest.mark.parametrize("case", ["ventana", "baja", "tomada", "reciente", "simulador"])
    def test_when_it_does_not(self, db: Session, case: str) -> None:
        tenant, _ = business(db)
        _on(db, tenant)
        contact = customer(
            db, tenant, wrote_hours_ago=30 if case == "ventana" else 2, opted_out=case == "baja"
        )
        thread = _won(db, tenant, contact)
        if case == "tomada":
            from tests.integration.test_crm_leads import _user

            thread.takeover_user_id = _user(db)
        if case == "simulador":
            thread.channel = "sandbox"
        if case == "reciente":
            db.add(
                CsatSurvey(
                    tenant_id=tenant.id,
                    conversation_id=thread.id,
                    contact_id=contact.id,
                    trigger="ganado",
                    sent_at=NOW - timedelta(days=10),
                )
            )
        db.flush()
        [candidate] = _mine(db, tenant)
        assert csat.why_not(db, candidate, now=NOW) == case
        assert csat.prepare(db, candidate, now=NOW) is None

    def test_it_is_a_fixed_text_queued_with_its_survey(self, db: Session) -> None:
        tenant, _ = business(db)
        _on(db, tenant)
        contact = customer(db, tenant)
        thread = _won(db, tenant, contact)
        from tests.integration.test_crm_leads import _user

        operator = _user(db)
        db.add(
            Message(
                tenant_id=tenant.id,
                conversation_id=thread.id,
                direction="outbound",
                author="human_operator",
                content="Listo",
                payload={"operador_id": str(operator)},
            )
        )
        db.flush()
        [candidate] = _mine(db, tenant)
        message = csat.prepare(db, candidate, now=NOW)
        assert message is not None and message.content == csat.QUESTION
        assert message.delivery_status == "queued"
        survey = db.execute(
            select(CsatSurvey).where(CsatSurvey.message_id == message.id)
        ).scalar_one()
        assert survey.status == "sent" and survey.trigger == "ganado"
        assert survey.operator_id == operator


class TestTheAnswer:
    def _sent(self, db: Session) -> tuple[Tenant, WhatsAppNumber, Contact]:
        tenant, number = business(db)
        _on(db, tenant)
        contact = customer(db, tenant)
        thread = _won(db, tenant, contact)
        db.add(
            CsatSurvey(
                tenant_id=tenant.id,
                conversation_id=thread.id,
                contact_id=contact.id,
                trigger="ganado",
                sent_at=datetime.now(UTC) - timedelta(minutes=5),
            )
        )
        db.flush()
        return tenant, number, contact

    def _survey(self, db: Session, contact: Contact) -> CsatSurvey:
        return db.execute(
            select(CsatSurvey).where(CsatSurvey.contact_id == contact.id)
        ).scalar_one()

    def test_a_score_is_thanked_and_the_assistant_stays_quiet(self, db: Session) -> None:
        _, number, contact = self._sent(db)
        outcome = _says(db, number, contact, "5 muy rápido")
        survey = self._survey(db, contact)
        assert survey.status == "answered" and survey.score == 5
        assert survey.comment == "muy rápido"
        assert outcome.system_reply_id is not None and not outcome.assistant_too
        reply = db.get(Message, outcome.system_reply_id)
        assert reply is not None and reply.content == csat.THANKS

    def test_a_score_with_a_question_also_goes_to_the_assistant(self, db: Session) -> None:
        _, number, contact = self._sent(db)
        outcome = _says(db, number, contact, "4, ¿mañana abren?")
        assert self._survey(db, contact).score == 4
        assert outcome.system_reply_id is not None and outcome.assistant_too

    def test_no_score_closes_it_quietly_and_the_assistant_answers(self, db: Session) -> None:
        _, number, contact = self._sent(db)
        outcome = _says(db, number, contact, "Quiero hacer otro pedido")
        survey = self._survey(db, contact)
        assert survey.status == "answered" and survey.score is None
        assert survey.comment == "Quiero hacer otro pedido"
        assert outcome.system_reply_id is None and outcome.inbound_message_id is not None

    def test_after_48_hours_it_expires(self, db: Session) -> None:
        _, number, contact = self._sent(db)
        survey = self._survey(db, contact)
        survey.sent_at = datetime.now(UTC) - timedelta(hours=49)
        db.flush()
        assert csat.expire(db, now=datetime.now(UTC)) >= 1
        db.refresh(survey)
        assert survey.status == "expired"
        outcome = _says(db, number, contact, "5")
        assert outcome.system_reply_id is None  # too late: a normal message
