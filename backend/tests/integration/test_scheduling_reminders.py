"""Appointment reminders: who gets one, when, and never twice (feature 019, phase D)."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

from sqlalchemy.orm import Session

from app.core.crypto import encrypt_secret
from app.models.business import Product
from app.models.channel import Contact
from app.models.conversation import Conversation, Message
from app.models.scheduling import Appointment
from app.models.tenant import Tenant
from app.models.whatsapp import WhatsAppNumber
from app.scheduling.reminders import Due, due_reminders, send_reminder
from app.scheduling.service import Scheduling
from tests.integration.campaign_fixtures import FakeWhatsApp, template
from tests.integration.test_scheduling import LIMA, NOW, _business, _service, at
from tests.integration.test_turn_pipeline import _conversation

BODY = (
    "Hola {{nombre}}, te recordamos tu cita de {{servicio}} el {{fecha}} a las {{hora}} "
    "en {{negocio}}. Responde a este mensaje para confirmarla."
)


START = at(10, days=1)  # Tuesday 10:00 in Lima: inside the opening hours


def _shop(
    db: Session, *, window_open: bool = True, with_template: bool = True, hours_before: float = 23
) -> tuple[Tenant, Product, Conversation, Appointment, datetime]:
    """A business with reminders on and an appointment at `START`.

    Returns the moment to check at: `hours_before` hours before it. Booking happens on Monday
    morning (`NOW`), which is when the rules (notice, horizon) are read.
    """
    clock = START - timedelta(hours=hours_before)
    tenant = _business(db)
    service = _service(db, tenant)
    conversation = _conversation(db, tenant, channel="whatsapp")
    contact = db.get(Contact, conversation.contact_id)
    assert contact is not None
    contact.profile_name = "Rosa"
    contact.last_inbound_at = clock - timedelta(hours=2 if window_open else 40)
    db.add(
        WhatsAppNumber(
            tenant_id=tenant.id,
            phone_e164="+51911122233",
            display_name="Clínica",
            provider="ycloud",
            status="connected",
            credentials_enc=encrypt_secret(
                json.dumps({"waba_id": "w1", "phone_number_id": "1122", "api_key": "yc_live_abc"})
            ),
        )
    )
    scheduling = Scheduling(db, tenant.id, now=NOW)
    scheduling.ensure_resource()
    settings = scheduling.settings()
    settings.recordatorio_24h = True
    settings.recordatorio_2h = True
    if with_template:
        settings.plantilla_recordatorio_id = template(
            db, tenant, body=BODY, category="utility", label="Recordatorio"
        ).id
    appointment = scheduling.book(service, START, by="agent", contact_id=contact.id)
    appointment.created_at = NOW - timedelta(days=3)  # booked well before any reminder was due
    db.flush()
    return tenant, service, conversation, appointment, clock


def _due(appointment: Appointment, kind: str) -> Due:
    return Due(appointment.tenant_id, appointment.id, kind, appointment.version)  # type: ignore[arg-type]


class TestWhoIsDue:
    def test_the_day_before_not_earlier_and_not_when_too_close(self, db: Session) -> None:
        _, _, _, appointment, clock = _shop(db, hours_before=23)
        assert [(d.kind, d.appointment_id) for d in due_reminders(db, clock)] == [
            ("24h", appointment.id)
        ]

        assert due_reminders(db, START - timedelta(hours=25)) == []  # too early
        assert [d.kind for d in due_reminders(db, START - timedelta(hours=1))] == ["2h"]

    def test_a_business_that_did_not_turn_them_on_sends_none(self, db: Session) -> None:
        tenant, _, _, _, clock = _shop(db, hours_before=23)
        Scheduling(db, tenant.id, now=NOW).settings().recordatorio_24h = False
        db.flush()

        assert due_reminders(db, clock) == []

    def test_a_late_booking_is_not_reminded_about_a_moment_that_passed(self, db: Session) -> None:
        _, _, _, appointment, clock = _shop(db, hours_before=23)
        appointment.created_at = START - timedelta(hours=20)  # booked 20 hours before it starts
        db.flush()

        assert due_reminders(db, clock) == []

    def test_a_cancelled_one_and_one_without_a_contact_are_skipped(self, db: Session) -> None:
        _, _, _, appointment, clock = _shop(db, hours_before=23)
        appointment.contact_id = None
        db.flush()
        assert due_reminders(db, clock) == []

        appointment.status = "cancelada"
        db.flush()
        assert due_reminders(db, clock) == []


class TestSending:
    async def test_inside_the_window_it_is_plain_text_in_the_open_conversation(
        self, db: Session
    ) -> None:
        _, _, conversation, appointment, clock = _shop(db, window_open=True)

        outcome = await send_reminder(
            db, _due(appointment, "24h"), whatsapp=FakeWhatsApp(), now=clock
        )

        assert outcome.result == "texto" and outcome.message_id is not None
        message = db.get(Message, outcome.message_id)
        assert message is not None and message.conversation_id == conversation.id
        assert message.delivery_status == "queued" and message.author == "system"
        assert "Hola Rosa" in message.content and "Consulta" in message.content
        assert "martes 27 de octubre" in message.content and "10:00" in message.content

    async def test_outside_the_window_it_is_the_approved_template(self, db: Session) -> None:
        _, _, _, appointment, clock = _shop(db, window_open=False)
        whatsapp = FakeWhatsApp()

        outcome = await send_reminder(db, _due(appointment, "24h"), whatsapp=whatsapp, now=clock)

        assert outcome.result == "plantilla"
        (sent,) = whatsapp.templates
        assert sent["values"] == ["Rosa", "Consulta", "martes 27 de octubre", "10:00", "Capacita"]

    async def test_without_an_approved_template_it_is_skipped_and_said(self, db: Session) -> None:
        tenant, _, _, appointment, clock = _shop(db, window_open=False, with_template=False)
        whatsapp = FakeWhatsApp()

        outcome = await send_reminder(db, _due(appointment, "24h"), whatsapp=whatsapp, now=clock)
        events = [
            e.evento for e in Scheduling(db, tenant.id).events.for_appointment(appointment.id)
        ]

        assert outcome.result == "sin_plantilla"
        assert whatsapp.templates == [] and whatsapp.texts == []
        assert "recordatorio_omitido" in events

    async def test_never_twice_and_never_for_an_appointment_that_moved(self, db: Session) -> None:
        tenant, _, _, appointment, clock = _shop(db, window_open=False)
        whatsapp = FakeWhatsApp()
        due = _due(appointment, "24h")

        first = await send_reminder(db, due, whatsapp=whatsapp, now=clock)
        second = await send_reminder(db, due, whatsapp=whatsapp, now=clock)

        assert (first.result, second.result) == ("plantilla", "ya_enviado")
        assert len(whatsapp.templates) == 1 and due_reminders(db, clock) == []

        # A move raises the version: a reminder claimed for the old time is not sent.
        Scheduling(db, tenant.id, now=NOW).reschedule(appointment, at(11, days=1), by="user")
        stale = await send_reminder(db, due, whatsapp=whatsapp, now=clock)
        assert stale.result == "cambiada" and len(whatsapp.templates) == 1

    async def test_not_at_night_and_not_to_somebody_who_opted_out(self, db: Session) -> None:
        _, _, _, appointment, clock = _shop(db, hours_before=12)
        night = datetime(2026, 10, 26, 22, 0, tzinfo=LIMA).astimezone(UTC)  # 22:00 in Lima

        late = await send_reminder(db, _due(appointment, "24h"), whatsapp=FakeWhatsApp(), now=night)
        assert late.result == "fuera_de_horario"
        assert appointment.recordatorio_24h_en is None  # not claimed: the morning run sends it

        contact = db.get(Contact, appointment.contact_id)
        assert contact is not None
        contact.opted_out_at = clock
        db.flush()
        morning = START - timedelta(hours=23)  # 11:00 in Lima
        gone = await send_reminder(
            db, _due(appointment, "24h"), whatsapp=FakeWhatsApp(), now=morning
        )
        assert gone.result == "baja"
