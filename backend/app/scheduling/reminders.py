"""Appointment reminders (feature 019, phase D; spec 014, HU-03).

`due_reminders` (every five minutes, `workers/reminders.py`) finds the appointments that need one:

* 24 hours before: starts within the next day and is more than two hours away;
* 2 hours before: starts within the next two hours;

and only for businesses that turned that reminder on (off by default: each reminder is a message
that costs). An appointment booked after the moment its reminder would have gone gets none.

`send_reminder` claims the reminder with a versioned write before anything is sent: a second worker
that read the same row loses the write, and an appointment moved in the meantime (`version` raised)
is skipped. A claimed reminder whose message then fails is spent, not retried: one missing reminder
is better than two. Inside the customer's 24-hour window the reminder is plain text; outside it only
the business's approved template can carry it, and without one the reminder is skipped and the event
says so.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, time, timedelta
from typing import Any, Literal

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.models.campaigns import MessageTemplate
from app.models.channel import Contact
from app.models.scheduling import LIVE_STATUSES, Appointment, AppointmentEvent, SchedulingSettings
from app.repositories.channel import session_window_is_open
from app.repositories.conversation import ConversationRepository, MessageRepository
from app.scheduling.service import Scheduling
from app.services.campaigns.sender import TemplateSender, business_name, fill, first_name

logger = logging.getLogger(__name__)

Kind = Literal["24h", "2h"]
LEAD: dict[str, timedelta] = {"24h": timedelta(hours=24), "2h": timedelta(hours=2)}
SENDING_HOURS = (time(8, 0), time(21, 0))
# Room left in Meta's window for the message to be accepted.
WINDOW_MARGIN = timedelta(minutes=30)
BATCH = 500


@dataclass(frozen=True)
class Due:
    tenant_id: uuid.UUID
    appointment_id: uuid.UUID
    kind: Kind
    version: int


def due_reminders(session: Session, now: datetime) -> list[Due]:
    """Every reminder that should go out now, soonest appointment first."""
    due: list[Due] = []
    for kind, flag, sent in (
        ("24h", SchedulingSettings.recordatorio_24h, Appointment.recordatorio_24h_en),
        ("2h", SchedulingSettings.recordatorio_2h, Appointment.recordatorio_2h_en),
    ):
        lead = LEAD[kind]
        too_close = now + LEAD["2h"] if kind == "24h" else now
        rows = session.execute(
            select(Appointment.tenant_id, Appointment.id, Appointment.version)
            .join(SchedulingSettings, SchedulingSettings.tenant_id == Appointment.tenant_id)
            .where(
                flag.is_(True),
                sent.is_(None),
                Appointment.status.in_(LIVE_STATUSES),
                Appointment.contact_id.is_not(None),
                Appointment.starts_at > too_close,
                Appointment.starts_at <= now + lead,
                # Booked before the moment the reminder was due: later is not reminded.
                Appointment.created_at <= Appointment.starts_at - lead,
            )
            .order_by(Appointment.starts_at)
            .limit(BATCH)
        ).all()
        due += [Due(r.tenant_id, r.id, kind, r.version) for r in rows]  # type: ignore[arg-type]
    return due


def lock_id_for(session: Session, due: Due) -> uuid.UUID:
    """The id the reminder locks on: the customer's open conversation, else the appointment."""
    row = session.get(Appointment, due.appointment_id)
    if row is not None and row.contact_id is not None:
        thread = ConversationRepository(session, due.tenant_id).open_for_contact(row.contact_id)
        if thread is not None:
            return thread.id
    return due.appointment_id


def _in_sending_hours(now: datetime, scheduling: Scheduling) -> bool:
    return SENDING_HOURS[0] <= now.astimezone(scheduling.zone()).time() < SENDING_HOURS[1]


def reminder_text(name: str | None, service: str, when: str, hour: str, business: str) -> str:
    greeting = f"Hola {name}, " if name else "Hola, "
    return (
        f"{greeting}te recordamos tu cita de {service} el {when} a las {hour} en {business}. "
        "¿Nos confirmas? Responde SÍ para confirmarla, o dinos si necesitas cambiarla."
    )


def _claim(session: Session, due: Due, now: datetime) -> bool:
    column = (
        Appointment.recordatorio_24h_en if due.kind == "24h" else Appointment.recordatorio_2h_en
    )
    sent = session.execute(
        update(Appointment)
        .where(
            Appointment.id == due.appointment_id,
            Appointment.tenant_id == due.tenant_id,
            Appointment.version == due.version,
            Appointment.status.in_(LIVE_STATUSES),
            column.is_(None),
        )
        .values({column.key: now})
    )
    return bool(sent.rowcount)  # type: ignore[attr-defined]


def _event(session: Session, row: Appointment, evento: str, detalle: dict[str, Any]) -> None:
    session.add(
        AppointmentEvent(
            tenant_id=row.tenant_id,
            appointment_id=row.id,
            evento=evento,
            actor="system",
            detalle=detalle,
        )
    )


@dataclass
class Outcome:
    """What `send_reminder` did, for the job's report and for the tests."""

    result: str
    message_id: uuid.UUID | None = None


async def send_reminder(
    session: Session, due: Due, *, whatsapp: Any, storage: Any = None, now: datetime | None = None
) -> Outcome:
    """Send one reminder; the caller commits. Never raises for the provider."""
    moment = now or datetime.now(UTC)
    scheduling = Scheduling(session, due.tenant_id, now=moment)
    row = scheduling.appointments.get(due.appointment_id)
    if row is None or row.version != due.version or row.status not in LIVE_STATUSES:
        return Outcome("cambiada")
    if not _in_sending_hours(moment, scheduling):
        return Outcome("fuera_de_horario")  # not claimed: the next run inside the hours sends it
    contact = session.get(Contact, row.contact_id) if row.contact_id else None
    if contact is None or contact.tenant_id != due.tenant_id or not contact.phone_e164:
        return Outcome("sin_contacto")
    if contact.opted_out_at is not None:
        return Outcome("baja")
    if not _claim(session, due, moment):
        return Outcome("ya_enviado")

    zone = scheduling.zone()
    local = row.starts_at.astimezone(zone)
    product = scheduling.products.get(row.product_id)
    from app.scheduling.text import MONTHS, WEEKDAYS

    when = f"{WEEKDAYS[local.weekday()]} {local.day} de {MONTHS[local.month - 1]}"
    service = product.name if product else "tu cita"
    hour = f"{local:%H:%M}"
    business = business_name(session, due.tenant_id)
    settings = scheduling.settings()

    if session_window_is_open(contact, now=moment + WINDOW_MARGIN):
        thread = ConversationRepository(session, due.tenant_id).open_for_contact(contact.id)
        if thread is not None:
            message = MessageRepository(session, due.tenant_id).append(
                conversation_id=thread.id,
                direction="outbound",
                author="system",
                content=reminder_text(first_name(contact), service, when, hour, business),
                delivery_status="queued",
                payload={"recordatorio": due.kind, "cita_id": str(row.id)},
            )
            _event(session, row, f"recordatorio_{due.kind}", {"canal": "texto"})
            return Outcome("texto", message.id)

    template = (
        session.get(MessageTemplate, settings.plantilla_recordatorio_id)
        if settings.plantilla_recordatorio_id
        else None
    )
    if template is None or template.tenant_id != due.tenant_id or template.status != "approved":
        _event(
            session,
            row,
            "recordatorio_omitido",
            {"motivo": "sin_plantilla_aprobada", "tipo": due.kind},
        )
        return Outcome("sin_plantilla")
    filled = fill(
        session,
        due.tenant_id,
        template,
        contact,
        {"servicio": service, "fecha": when, "hora": hour},
    )
    if filled.skip:
        _event(session, row, "recordatorio_omitido", {"motivo": filled.skip, "tipo": due.kind})
        return Outcome("sin_datos")
    sender = TemplateSender(session, due.tenant_id, provider=whatsapp, storage=storage, now=moment)
    sent = await sender.send(contact, template, filled, kind="recordatorio")
    _event(session, row, f"recordatorio_{due.kind}", {"canal": "plantilla"})
    return Outcome("plantilla", sent.id)
