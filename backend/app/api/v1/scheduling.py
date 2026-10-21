"""The agenda: appointments, resources, blocks and the rules (feature 019, phase D; spec 014).

    GET  /tenant/scheduling                     rules, reminders, resources and bookable services
    PUT  /tenant/scheduling/settings            the rules and the reminders (managers)
    POST /tenant/scheduling/resources           a person, chair or room (managers)
    PUT|DELETE /tenant/scheduling/resources/{id}
    POST /tenant/scheduling/blocks              time nobody can book (managers)
    DELETE /tenant/scheduling/blocks/{id}
    GET  /tenant/scheduling/availability        free times of a service, for the manual form
    GET  /tenant/appointments                   the agenda of a range of days
    POST /tenant/appointments                   a walk-in or a phone call
    POST /tenant/appointments/{id}/action       confirm, complete, no-show or cancel
    POST /tenant/appointments/{id}/reschedule

Every member works the agenda; configuring it is for managers. Times travel as the business's own
local time (`2026-10-27T10:00`) next to the UTC instant, so a panel in another time zone still
shows the hour the customer was promised. Human changes publish `appointment.updated` after the
commit.
"""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime, timedelta
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Query, Response, status
from pydantic import BaseModel, Field, StringConstraints, field_validator
from sqlalchemy import func, select

from app.api.deps import ConfigurationManagerDep, MemberDep, SessionDep
from app.api.errors import ApiError, error_responses
from app.core.events import publish
from app.models.business import Product
from app.models.campaigns import MessageTemplate
from app.models.channel import Contact
from app.models.scheduling import Appointment, Resource
from app.scheduling.availability import DAY_KEYS
from app.scheduling.service import Scheduling, SchedulingError, SlotTaken
from app.scheduling.text import SLOT_FORMAT, describe, parse_slot, slot_token
from app.services.crm_report import contact_name

router = APIRouter(tags=["scheduling"])

AUTH = (
    (401, "`not_authenticated`, `token_expired` or `invalid_token`"),
    (403, "`email_not_verified`, `no_active_tenant` or `tenant_access_denied`"),
)
Name = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=80)]
Note = Annotated[str, StringConstraints(strip_whitespace=True, max_length=500)]
LocalTime = Annotated[str, StringConstraints(pattern=r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}$")]
MAX_RANGE_DAYS = 62


# --------------------------------------------------------------------- schemas


def _valid_hours(value: dict[str, Any] | None) -> dict[str, Any] | None:
    if value is None:
        return None
    clean: dict[str, list[list[str]]] = {}
    for day, spans in value.items():
        if day not in DAY_KEYS or not isinstance(spans, list):
            raise ValueError(f"día no válido: {day}")
        ranges: list[list[str]] = []
        for span in spans:
            if not isinstance(span, list) or len(span) != 2:
                raise ValueError("cada tramo es [inicio, fin]")
            start, end = (str(x).strip() for x in span)
            for clock in (start, end):
                hour, _, minute = clock.partition(":")
                if not (
                    hour.isdigit()
                    and minute.isdigit()
                    and 0 <= int(hour) < 24
                    and 0 <= int(minute) < 60
                ):
                    raise ValueError(f"hora no válida: {clock}")
            if (int(start[:2]), int(start[3:5])) >= (int(end[:2]), int(end[3:5])):
                raise ValueError("el fin debe ser después del inicio")
            ranges.append([start, end])
        if ranges:
            clean[day] = ranges
    return clean


class ResourceIn(BaseModel):
    nombre: Name
    # None follows the business's opening hours.
    horario: dict[str, Any] | None = None
    product_ids: list[uuid.UUID] = Field(default_factory=list, max_length=100)
    is_active: bool = True

    @field_validator("horario")
    @classmethod
    def _hours(cls, value: dict[str, Any] | None) -> dict[str, Any] | None:
        return _valid_hours(value)


class ResourceOut(BaseModel):
    id: uuid.UUID
    nombre: str
    horario: dict[str, Any] | None
    product_ids: list[uuid.UUID]
    is_active: bool


class ServiceOut(BaseModel):
    id: uuid.UUID
    nombre: str
    duracion_min: int
    buffer_min: int


class SettingsIn(BaseModel):
    anticipacion_min_horas: int | None = Field(default=None, ge=0, le=72)
    anticipacion_max_dias: int | None = Field(default=None, ge=1, le=180)
    max_citas_activas: int | None = Field(default=None, ge=1, le=10)
    recordatorio_24h: bool | None = None
    recordatorio_2h: bool | None = None
    plantilla_recordatorio_id: uuid.UUID | None = None


class SettingsOut(BaseModel):
    anticipacion_min_horas: int
    anticipacion_max_dias: int
    max_citas_activas: int
    recordatorio_24h: bool
    recordatorio_2h: bool
    plantilla_recordatorio_id: uuid.UUID | None
    # Whether the chosen template is approved: a reminder outside the 24-hour window needs one.
    plantilla_aprobada: bool


class BlockIn(BaseModel):
    resource_id: uuid.UUID
    desde: LocalTime
    hasta: LocalTime
    motivo: Note | None = None


class BlockOut(BaseModel):
    id: uuid.UUID
    resource_id: uuid.UUID
    desde: str
    hasta: str
    motivo: str | None


class SchedulingOut(BaseModel):
    zona: str
    horario_negocio: dict[str, Any]
    ajustes: SettingsOut
    recursos: list[ResourceOut]
    servicios: list[ServiceOut]
    bloqueos: list[BlockOut]


class AppointmentOut(BaseModel):
    id: uuid.UUID
    servicio_id: uuid.UUID
    servicio: str
    recurso_id: uuid.UUID
    recurso: str
    cliente: str | None
    telefono: str | None
    contact_id: uuid.UUID | None
    conversation_id: uuid.UUID | None
    inicio_local: str
    fin_local: str
    inicio: datetime
    texto: str
    status: str
    notas: str | None
    created_by: str
    # A reminder went out and the customer has not confirmed.
    sin_confirmar: bool
    # How many appointments this customer missed ("no asistió"): information, not a penalty.
    faltas: int = 0


class ConflictOut(AppointmentOut):
    motivo: str  # "fuera_de_horario", "recurso_inactivo" or "bloqueada"


class ConflictList(BaseModel):
    items: list[ConflictOut]


class AppointmentList(BaseModel):
    zona: str
    items: list[AppointmentOut]


class AppointmentIn(BaseModel):
    servicio_id: uuid.UUID
    inicio: LocalTime
    nombre_cliente: Name
    # The customer's contact when booked from a lead: reminders and the history follow it.
    contact_id: uuid.UUID | None = None
    telefono: Annotated[str, StringConstraints(strip_whitespace=True, max_length=30)] | None = None
    notas: Note | None = None


class ActionIn(BaseModel):
    accion: Literal["confirmar", "completar", "no_asistio", "cancelar"]
    motivo: Note | None = None


class RescheduleIn(BaseModel):
    inicio: LocalTime


class SlotOut(BaseModel):
    inicio_local: str
    texto: str
    recurso_id: uuid.UUID


class AvailabilityOut(BaseModel):
    items: list[SlotOut]


# --------------------------------------------------------------------- helpers


def _error(exc: SchedulingError) -> ApiError:
    code = 409 if isinstance(exc, SlotTaken) else 422
    return ApiError(status_code=code, error=exc.code, message=str(exc))


def _not_found() -> ApiError:
    return ApiError(status_code=404, error="not_found", message="Not found.")


def _utc(token: str, scheduling: Scheduling) -> datetime:
    moment = parse_slot(token, scheduling.zone())
    if moment is None:
        raise ApiError(
            status_code=422, error="invalid_time", message="Esa fecha u hora no es válida."
        )
    return moment


def _local(moment: datetime, scheduling: Scheduling) -> str:
    return moment.astimezone(scheduling.zone()).strftime(SLOT_FORMAT)


def _resource_out(row: Resource) -> ResourceOut:
    return ResourceOut(
        id=row.id,
        nombre=row.nombre,
        horario=dict(row.horario) if row.horario else None,
        product_ids=list(row.product_ids or []),
        is_active=row.is_active,
    )


def _settings_out(scheduling: Scheduling) -> SettingsOut:
    row = scheduling.settings()
    template = (
        scheduling.session.get(MessageTemplate, row.plantilla_recordatorio_id)
        if row.plantilla_recordatorio_id
        else None
    )
    return SettingsOut(
        anticipacion_min_horas=row.anticipacion_min_horas,
        anticipacion_max_dias=row.anticipacion_max_dias,
        max_citas_activas=row.max_citas_activas,
        recordatorio_24h=row.recordatorio_24h,
        recordatorio_2h=row.recordatorio_2h,
        plantilla_recordatorio_id=row.plantilla_recordatorio_id,
        plantilla_aprobada=bool(
            template is not None
            and template.tenant_id == scheduling.tenant_id
            and template.status == "approved"
        ),
    )


def _appointments_out(scheduling: Scheduling, rows: list[Appointment]) -> list[AppointmentOut]:
    session = scheduling.session
    products = {
        p.id: p.name
        for p in session.execute(
            select(Product).where(
                Product.tenant_id == scheduling.tenant_id,
                Product.id.in_({r.product_id for r in rows}),
            )
        ).scalars()
    }
    resources = {r.id: r.nombre for r in scheduling.resources.all()}
    contacts = {
        c.id: c
        for c in session.execute(
            select(Contact).where(
                Contact.tenant_id == scheduling.tenant_id,
                Contact.id.in_({r.contact_id for r in rows if r.contact_id}),
            )
        ).scalars()
    }
    zone = scheduling.zone()
    missed: dict[uuid.UUID, int] = {}
    if contacts:
        for contact_id, count in session.execute(
            select(Appointment.contact_id, func.count())
            .where(
                Appointment.tenant_id == scheduling.tenant_id,
                Appointment.status == "no_asistio",
                Appointment.contact_id.in_(list(contacts)),
            )
            .group_by(Appointment.contact_id)
        ).all():
            missed[contact_id] = int(count)
    out: list[AppointmentOut] = []
    for row in rows:
        contact = contacts.get(row.contact_id) if row.contact_id else None
        out.append(
            AppointmentOut(
                id=row.id,
                servicio_id=row.product_id,
                servicio=products.get(row.product_id, "Servicio"),
                recurso_id=row.resource_id,
                recurso=resources.get(row.resource_id, "—"),
                cliente=(contact_name(contact) if contact else None) or row.nombre_cliente,
                telefono=(contact.phone_e164 if contact else None) or row.telefono,
                contact_id=row.contact_id,
                conversation_id=row.conversation_id,
                inicio_local=slot_token(row.starts_at, zone),
                fin_local=slot_token(row.ends_at, zone),
                inicio=row.starts_at,
                texto=describe(row.starts_at, zone),
                status=row.status,
                notas=row.notas,
                created_by=row.created_by,
                sin_confirmar=row.status == "agendada" and row.recordatorio_24h_en is not None,
                faltas=missed.get(row.contact_id, 0) if row.contact_id else 0,
            )
        )
    return out


def _contact(
    session: SessionDep, tenant_id: uuid.UUID, contact_id: uuid.UUID | None
) -> uuid.UUID | None:
    if contact_id is None:
        return None
    contact = session.get(Contact, contact_id)
    if contact is None or contact.tenant_id != tenant_id:
        raise _not_found()
    return contact.id


def _appointment(scheduling: Scheduling, appointment_id: uuid.UUID) -> Appointment:
    row = scheduling.appointments.locked(appointment_id)
    if row is None:
        raise _not_found()
    return row


def _changed(tenant_id: uuid.UUID, row: Appointment) -> None:
    publish(tenant_id, "appointment.updated", appointment_id=row.id)


# ---------------------------------------------------------------------- config


@router.get("/tenant/scheduling", responses=error_responses(*AUTH))
def overview(principal: MemberDep, session: SessionDep) -> SchedulingOut:
    scheduling = Scheduling(session, principal.tenant_id)
    zone = scheduling.zone()
    now = scheduling.now()
    session.commit()  # the settings row may have just been created
    return SchedulingOut(
        zona=str(zone),
        horario_negocio=scheduling.business_hours(),
        ajustes=_settings_out(scheduling),
        recursos=[_resource_out(r) for r in scheduling.resources.all()],
        servicios=[
            ServiceOut(
                id=p.id, nombre=p.name, duracion_min=p.duracion_min or 0, buffer_min=p.buffer_min
            )
            for p in scheduling.bookable()
        ],
        bloqueos=[
            BlockOut(
                id=b.id,
                resource_id=b.resource_id,
                desde=slot_token(b.starts_at, zone),
                hasta=slot_token(b.ends_at, zone),
                motivo=b.motivo,
            )
            for b in scheduling.blocks.upcoming(now)
        ],
    )


@router.put(
    "/tenant/scheduling/settings", responses=error_responses(*AUTH, (422, "`needs_template`"))
)
def save_settings(
    body: SettingsIn, principal: ConfigurationManagerDep, session: SessionDep
) -> SettingsOut:
    scheduling = Scheduling(session, principal.tenant_id)
    row = scheduling.settings()
    changes = body.model_dump(exclude_unset=True)
    if "plantilla_recordatorio_id" in changes and changes["plantilla_recordatorio_id"] is not None:
        template = session.get(MessageTemplate, changes["plantilla_recordatorio_id"])
        if template is None or template.tenant_id != principal.tenant_id:
            raise ApiError(
                status_code=422, error="unknown_template", message="Esa plantilla no existe."
            )
    for field, value in changes.items():
        setattr(row, field, value)
    session.flush()
    wants_reminders = row.recordatorio_24h or row.recordatorio_2h
    if wants_reminders and not _settings_out(scheduling).plantilla_aprobada:
        # A reminder goes days ahead, outside the 24-hour window: only a template can carry it.
        raise ApiError(
            status_code=422,
            error="needs_template",
            message="Para activar recordatorios elige una plantilla aprobada por WhatsApp.",
        )
    session.commit()
    return _settings_out(scheduling)


def _resource(scheduling: Scheduling, resource_id: uuid.UUID) -> Resource:
    row = scheduling.resources.get(resource_id)
    if row is None:
        raise _not_found()
    return row


@router.post(
    "/tenant/scheduling/resources",
    status_code=status.HTTP_201_CREATED,
    responses=error_responses(*AUTH),
)
def create_resource(
    body: ResourceIn, principal: ConfigurationManagerDep, session: SessionDep
) -> ResourceOut:
    scheduling = Scheduling(session, principal.tenant_id)
    row = scheduling.resources.add(
        nombre=body.nombre,
        horario=body.horario,
        product_ids=body.product_ids,
        is_active=body.is_active,
    )
    session.commit()
    return _resource_out(row)


@router.put(
    "/tenant/scheduling/resources/{resource_id}",
    responses=error_responses(*AUTH, (404, "`not_found`")),
)
def update_resource(
    resource_id: uuid.UUID,
    body: ResourceIn,
    principal: ConfigurationManagerDep,
    session: SessionDep,
) -> ResourceOut:
    scheduling = Scheduling(session, principal.tenant_id)
    row = _resource(scheduling, resource_id)
    row.nombre = body.nombre
    row.horario = body.horario
    row.product_ids = body.product_ids
    row.is_active = body.is_active
    session.commit()
    return _resource_out(row)


@router.delete(
    "/tenant/scheduling/resources/{resource_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    responses=error_responses(*AUTH, (404, "`not_found`"), (422, "`has_appointments`")),
)
def delete_resource(
    resource_id: uuid.UUID, principal: ConfigurationManagerDep, session: SessionDep
) -> Response:
    scheduling = Scheduling(session, principal.tenant_id)
    row = _resource(scheduling, resource_id)
    if scheduling.appointments.count(Appointment.resource_id == row.id) > 0:
        # Its history stays: it is switched off instead, and takes no new appointment.
        raise ApiError(
            status_code=422,
            error="has_appointments",
            message="Tiene citas en su historial: desactívalo en vez de borrarlo.",
        )
    session.delete(row)
    session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post(
    "/tenant/scheduling/blocks",
    status_code=status.HTTP_201_CREATED,
    responses=error_responses(*AUTH, (404, "`not_found`"), (422, "`invalid_time`")),
)
def create_block(
    body: BlockIn, principal: ConfigurationManagerDep, session: SessionDep
) -> BlockOut:
    scheduling = Scheduling(session, principal.tenant_id)
    _resource(scheduling, body.resource_id)
    start, end = _utc(body.desde, scheduling), _utc(body.hasta, scheduling)
    if end <= start:
        raise ApiError(
            status_code=422, error="invalid_time", message="El fin debe ser después del inicio."
        )
    row = scheduling.blocks.add(
        resource_id=body.resource_id, starts_at=start, ends_at=end, motivo=body.motivo
    )
    session.commit()
    return BlockOut(
        id=row.id,
        resource_id=row.resource_id,
        desde=body.desde,
        hasta=body.hasta,
        motivo=row.motivo,
    )


@router.delete(
    "/tenant/scheduling/blocks/{block_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    responses=error_responses(*AUTH, (404, "`not_found`")),
)
def delete_block(
    block_id: uuid.UUID, principal: ConfigurationManagerDep, session: SessionDep
) -> Response:
    if not Scheduling(session, principal.tenant_id).blocks.delete(block_id):
        raise _not_found()
    session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# ---------------------------------------------------------------------- agenda


@router.get(
    "/tenant/scheduling/availability", responses=error_responses(*AUTH, (404, "`not_found`"))
)
def availability(
    principal: MemberDep,
    session: SessionDep,
    servicio_id: uuid.UUID,
    desde: date | None = None,
    ignore: uuid.UUID | None = None,
) -> AvailabilityOut:
    """Free times of a service from a day, for the form of a manual appointment or a move."""
    scheduling = Scheduling(session, principal.tenant_id)
    product = scheduling.products.get(servicio_id)
    if product is None or not product.agendable:
        raise _not_found()
    zone = scheduling.zone()
    slots = scheduling.offer(product, on_or_after=desde, ignore=ignore)
    session.commit()
    return AvailabilityOut(
        items=[
            SlotOut(
                inicio_local=slot_token(s.start, zone),
                texto=describe(s.start, zone),
                recurso_id=s.resource_id,
            )
            for s in slots[:200]
        ]
    )


@router.get("/tenant/scheduling/conflicts", responses=error_responses(*AUTH))
def conflicts(principal: MemberDep, session: SessionDep) -> ConflictList:
    """Appointments to come that stopped fitting the hours, the resource or the blocks."""
    scheduling = Scheduling(session, principal.tenant_id)
    found = scheduling.conflicts()
    rows = _appointments_out(scheduling, [a for a, _ in found])
    return ConflictList(
        items=[
            ConflictOut(**row.model_dump(), motivo=reason)
            for row, (_, reason) in zip(rows, found, strict=True)
        ]
    )


@router.get("/tenant/appointments", responses=error_responses(*AUTH))
def list_appointments(
    principal: MemberDep,
    session: SessionDep,
    desde: date,
    hasta: date | None = None,
    recurso_id: Annotated[uuid.UUID | None, Query()] = None,
) -> AppointmentList:
    """Every appointment that starts from `desde` to `hasta` (inclusive), in local days."""
    scheduling = Scheduling(session, principal.tenant_id)
    zone = scheduling.zone()
    last = min(hasta or desde, desde + timedelta(days=MAX_RANGE_DAYS))
    start = datetime.combine(desde, datetime.min.time(), tzinfo=zone).astimezone(UTC)
    end = datetime.combine(last + timedelta(days=1), datetime.min.time(), tzinfo=zone).astimezone(
        UTC
    )
    rows = list(scheduling.appointments.between(start, end, resource_id=recurso_id))
    return AppointmentList(zona=str(zone), items=_appointments_out(scheduling, rows))


@router.post(
    "/tenant/appointments",
    status_code=status.HTTP_201_CREATED,
    responses=error_responses(
        *AUTH,
        (404, "`not_found`"),
        (409, "`slot_taken`"),
        (422, "`too_soon`, `too_far`, `not_bookable`"),
    ),
)
def create_appointment(
    body: AppointmentIn, principal: MemberDep, session: SessionDep
) -> AppointmentOut:
    """A walk-in or a phone call: the same rules and the same refusal of a double booking."""
    scheduling = Scheduling(session, principal.tenant_id)
    product = scheduling.products.get(body.servicio_id)
    if product is None:
        raise _not_found()
    try:
        scheduling.ensure_resource()
        row = scheduling.book(
            product,
            _utc(body.inicio, scheduling),
            by="user",
            contact_id=_contact(session, principal.tenant_id, body.contact_id),
            nombre_cliente=body.nombre_cliente,
            telefono=body.telefono,
            notas=body.notas,
            actor_user_id=principal.user_id,
        )
    except SchedulingError as exc:
        session.rollback()
        raise _error(exc) from exc
    session.commit()
    _changed(principal.tenant_id, row)
    return _appointments_out(scheduling, [row])[0]


@router.post(
    "/tenant/appointments/{appointment_id}/action",
    responses=error_responses(*AUTH, (404, "`not_found`"), (422, "`not_live`")),
)
def act(
    appointment_id: uuid.UUID, body: ActionIn, principal: MemberDep, session: SessionDep
) -> AppointmentOut:
    scheduling = Scheduling(session, principal.tenant_id)
    row = _appointment(scheduling, appointment_id)
    try:
        if body.accion == "cancelar":
            scheduling.cancel(row, by="user", actor_user_id=principal.user_id, motivo=body.motivo)
        else:
            scheduling.set_status(row, body.accion, by="user", actor_user_id=principal.user_id)
    except SchedulingError as exc:
        session.rollback()
        raise _error(exc) from exc
    session.commit()
    _changed(principal.tenant_id, row)
    return _appointments_out(scheduling, [row])[0]


@router.post(
    "/tenant/appointments/{appointment_id}/reschedule",
    responses=error_responses(
        *AUTH,
        (404, "`not_found`"),
        (409, "`slot_taken`"),
        (422, "`too_soon`, `too_far`, `not_live`"),
    ),
)
def reschedule(
    appointment_id: uuid.UUID, body: RescheduleIn, principal: MemberDep, session: SessionDep
) -> AppointmentOut:
    scheduling = Scheduling(session, principal.tenant_id)
    row = _appointment(scheduling, appointment_id)
    try:
        scheduling.reschedule(
            row, _utc(body.inicio, scheduling), by="user", actor_user_id=principal.user_id
        )
    except SchedulingError as exc:
        session.rollback()
        raise _error(exc) from exc
    session.commit()
    _changed(principal.tenant_id, row)
    return _appointments_out(scheduling, [row])[0]
