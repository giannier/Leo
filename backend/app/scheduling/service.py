"""Offering and booking appointments (feature 019, phase D; spec 014).

`Scheduling` works for one business. It reads what is taken, offers free slots (`offer`), and writes
the calendar: `book`, `reschedule`, `cancel`, `set_status`. Every write leaves an event, raises the
appointment's `version` when the time moves (so a reminder claimed for the old time is not sent) and
refuses what the rules refuse: too soon, too far, too many live appointments for one person.

Two customers asking for the same minute: the database has the last word. The insert runs in a
savepoint and an overlap on the resource (`ex_appointments_resource_no_overlap`, SQLSTATE 23P01)
becomes `SlotTaken`, which the agent turns into "se acaba de ocupar, ¿te va el siguiente?".
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models.business import Product
from app.models.scheduling import Appointment, Resource, SchedulingSettings
from app.repositories.business import BusinessProfileRepository
from app.repositories.product import ProductRepository
from app.repositories.scheduling import (
    AppointmentEventRepository,
    AppointmentRepository,
    BlockRepository,
    ResourceRepository,
    SchedulingSettingsRepository,
)
from app.scheduling.availability import Interval, day_windows, free_starts, overlaps

DEFAULT_ZONE = "America/Lima"
DEFAULT_RESOURCE_NAME = "Atención"
# What `offer` looks at when nobody says how far: two weeks is what a customer chooses among.
DEFAULT_OFFER_DAYS = 14
EXCLUSION_VIOLATION = "23P01"
STATUS_BY_ACTION = {
    "confirmar": "confirmada",
    "completar": "completada",
    "no_asistio": "no_asistio",
}


class SchedulingError(ValueError):
    """A booking the rules refuse, with a code for the API and the agent."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


class SlotTaken(SchedulingError):
    def __init__(self) -> None:
        super().__init__("slot_taken", "Ese horario se acaba de ocupar.")


@dataclass(frozen=True)
class Slot:
    start: datetime
    resource_id: uuid.UUID


class Scheduling:
    def __init__(
        self, session: Session, tenant_id: uuid.UUID, *, now: datetime | None = None
    ) -> None:
        self._session = session
        self._tenant_id = tenant_id
        self._clock = now
        self.resources = ResourceRepository(session, tenant_id)
        self.blocks = BlockRepository(session, tenant_id)
        self.appointments = AppointmentRepository(session, tenant_id)
        self.events = AppointmentEventRepository(session, tenant_id)
        self.products = ProductRepository(session, tenant_id)
        self._settings = SchedulingSettingsRepository(session, tenant_id)

    @property
    def session(self) -> Session:
        return self._session

    @property
    def tenant_id(self) -> uuid.UUID:
        return self._tenant_id

    # ----------------------------------------------------------------- setup

    def now(self) -> datetime:
        return self._clock or datetime.now(UTC)

    def settings(self) -> SchedulingSettings:
        return self._settings.ensure()

    def zone(self) -> ZoneInfo:
        profile = BusinessProfileRepository(self._session, self._tenant_id).current()
        try:
            return ZoneInfo((profile.timezone if profile else None) or DEFAULT_ZONE)
        except ZoneInfoNotFoundError:
            return ZoneInfo(DEFAULT_ZONE)

    def business_hours(self) -> dict[str, Any]:
        profile = BusinessProfileRepository(self._session, self._tenant_id).current()
        return dict(profile.opening_hours) if profile and profile.opening_hours else {}

    def ensure_resource(self) -> Resource:
        """The first resource, created on demand: a business with one chair needs no setup."""
        existing = self.resources.all()
        if existing:
            return existing[0]
        # Two first requests at once must not each create "Atención" (that doubles the capacity):
        # the lock makes the second wait for the first to commit, then find its resource.
        self._session.execute(
            text("SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))"),
            {"key": f"scheduling-resource:{self._tenant_id}"},
        )
        existing = self.resources.all()
        if existing:
            return existing[0]
        return self.resources.add(nombre=DEFAULT_RESOURCE_NAME)

    def bookable(self) -> list[Product]:
        return [
            p
            for p in self.products.list(Product.agendable.is_(True), Product.is_active.is_(True))
            if p.deleted_at is None and p.duracion_min
        ]

    def hours_of(self, resource: Resource) -> dict[str, Any]:
        """The resource's own hours, or the business's."""
        return dict(resource.horario) if resource.horario else self.business_hours()

    def _eligible(self, product: Product) -> list[Resource]:
        """The active resources that take `product`.

        A business that never set one up has a single resource, `Atención`, that follows its
        opening hours: it is created the first time it is needed.
        """
        resources = list(self.resources.active())
        if not resources and not self.resources.all():
            resources = [self.ensure_resource()]
        return [r for r in resources if not r.product_ids or product.id in r.product_ids]

    # ----------------------------------------------------------------- offer

    def _busy(self, start: datetime, end: datetime) -> dict[uuid.UUID, list[Interval]]:
        busy: dict[uuid.UUID, list[Interval]] = {}
        for a in self.appointments.live_overlapping(start, end):
            busy.setdefault(a.resource_id, []).append((a.starts_at, a.ocupa_hasta))
        for b in self.blocks.overlapping(start, end):
            busy.setdefault(b.resource_id, []).append((b.starts_at, b.ends_at))
        return busy

    def offer(
        self,
        product: Product,
        *,
        on_or_after: date | None = None,
        days: int = DEFAULT_OFFER_DAYS,
        ignore: uuid.UUID | None = None,
    ) -> list[Slot]:
        """Every free start of `product` in the next `days` local days, soonest first.

        `ignore` is an appointment being moved: its own range is not an obstacle.
        """
        if not product.duracion_min:
            return []
        rules = self.settings()
        zone = self.zone()
        now = self.now()
        earliest = now + timedelta(hours=rules.anticipacion_min_horas)
        latest = now + timedelta(days=rules.anticipacion_max_dias)
        first = max(
            on_or_after or earliest.astimezone(zone).date(), earliest.astimezone(zone).date()
        )
        last = min(first + timedelta(days=days - 1), latest.astimezone(zone).date())
        if last < first:
            return []
        window_start = datetime.combine(first, datetime.min.time(), tzinfo=zone).astimezone(UTC)
        window_end = datetime.combine(
            last + timedelta(days=1), datetime.min.time(), tzinfo=zone
        ).astimezone(UTC)
        busy = self._busy(window_start - timedelta(days=1), window_end + timedelta(days=1))
        if ignore is not None:
            moving = self.appointments.get(ignore)
            if moving is not None and moving.resource_id in busy:
                busy[moving.resource_id] = [
                    i
                    for i in busy[moving.resource_id]
                    if i != (moving.starts_at, moving.ocupa_hasta)
                ]
        duration = timedelta(minutes=product.duracion_min)
        buffer = timedelta(minutes=product.buffer_min or 0)
        taken: dict[datetime, Slot] = {}
        for resource in self._eligible(product):
            hours = self.hours_of(resource)
            day = first
            while day <= last:
                for start in free_starts(
                    hours=hours,
                    zone=zone,
                    day=day,
                    duration=duration,
                    buffer=buffer,
                    busy=busy.get(resource.id, []),
                    earliest=earliest,
                    latest=latest,
                ):
                    taken.setdefault(start, Slot(start, resource.id))
                day += timedelta(days=1)
        return [taken[start] for start in sorted(taken)]

    # ----------------------------------------------------------------- write

    def _rules(self, product: Product, start: datetime, *, contact_id: uuid.UUID | None) -> None:
        rules = self.settings()
        now = self.now()
        if not product.agendable or not product.duracion_min or not product.is_active:
            raise SchedulingError("not_bookable", "Ese servicio no se agenda.")
        if start < now + timedelta(hours=rules.anticipacion_min_horas):
            raise SchedulingError(
                "too_soon",
                f"Se agenda con al menos {rules.anticipacion_min_horas} horas de anticipación.",
            )
        if start > now + timedelta(days=rules.anticipacion_max_dias):
            raise SchedulingError(
                "too_far", f"Se agenda hasta {rules.anticipacion_max_dias} días por adelantado."
            )
        if contact_id is not None:
            held = len(self.appointments.live_of_contact(contact_id, now))
            if held >= rules.max_citas_activas:
                raise SchedulingError(
                    "limit", f"Ya tiene {held} citas pendientes: puede cambiar una de esas."
                )

    def book(
        self,
        product: Product,
        start: datetime,
        *,
        by: str,
        contact_id: uuid.UUID | None = None,
        conversation_id: uuid.UUID | None = None,
        nombre_cliente: str | None = None,
        telefono: str | None = None,
        notas: str | None = None,
        actor_user_id: uuid.UUID | None = None,
    ) -> Appointment:
        """Book `product` at `start` on the first free resource.

        Raises `SchedulingError` for a rule and `SlotTaken` when the minute is gone.
        """
        self._rules(product, start, contact_id=contact_id)
        assert product.duracion_min is not None
        duration = timedelta(minutes=product.duracion_min)
        buffer = timedelta(minutes=product.buffer_min or 0)
        end = start + duration
        resource = self._first_free(product, start, end, end + buffer)
        row = self._insert(
            product,
            resource,
            start,
            end,
            end + buffer,
            by=by,
            contact_id=contact_id,
            conversation_id=conversation_id,
            nombre_cliente=nombre_cliente,
            telefono=telefono,
            notas=notas,
        )
        self._event(row, "agendada", by, actor_user_id, {"inicio": start.isoformat()})
        return row

    def _first_free(
        self, product: Product, start: datetime, end: datetime, until: datetime
    ) -> Resource:
        """The first resource open from `start` to `end` and free until `until` (the buffer)."""
        busy = self._busy(start, until)
        zone = self.zone()
        for resource in self._eligible(product):
            if self._inside_hours(self.hours_of(resource), zone, start, end) and not overlaps(
                start, until, busy.get(resource.id, [])
            ):
                return resource
        raise SlotTaken()

    def _inside_hours(
        self, hours: dict[str, Any], zone: ZoneInfo, start: datetime, until: datetime
    ) -> bool:
        day = start.astimezone(zone).date()
        return any(
            w_start <= start and until <= w_end for w_start, w_end in day_windows(hours, day, zone)
        )

    def _insert(
        self,
        product: Product,
        resource: Resource,
        start: datetime,
        end: datetime,
        occupied_until: datetime,
        *,
        by: str,
        contact_id: uuid.UUID | None,
        conversation_id: uuid.UUID | None,
        nombre_cliente: str | None,
        telefono: str | None,
        notas: str | None,
    ) -> Appointment:
        try:
            with self._session.begin_nested():
                return self.appointments.add(
                    product_id=product.id,
                    resource_id=resource.id,
                    contact_id=contact_id,
                    conversation_id=conversation_id,
                    nombre_cliente=nombre_cliente,
                    telefono=telefono,
                    starts_at=start,
                    ends_at=end,
                    ocupa_hasta=occupied_until,
                    created_by=by,
                    notas=notas,
                )
        except IntegrityError as exc:
            if getattr(exc.orig, "sqlstate", None) == EXCLUSION_VIOLATION:
                raise SlotTaken() from exc
            raise

    def reschedule(
        self,
        appointment: Appointment,
        start: datetime,
        *,
        by: str,
        actor_user_id: uuid.UUID | None = None,
    ) -> Appointment:
        """Move a live appointment to `start`; the old place is free again."""
        if appointment.status not in ("agendada", "confirmada"):
            raise SchedulingError("not_live", "Esa cita ya no se puede cambiar.")
        product = self.products.get(appointment.product_id)
        assert product is not None and product.duracion_min is not None
        self._rules(product, start, contact_id=None)
        duration = timedelta(minutes=product.duracion_min)
        buffer = timedelta(minutes=product.buffer_min or 0)
        end = start + duration
        previous = appointment.starts_at
        resource = self._first_free_for_move(appointment, product, start, end, end + buffer)
        try:
            with self._session.begin_nested():
                appointment.resource_id = resource.id
                appointment.starts_at = start
                appointment.ends_at = end
                appointment.ocupa_hasta = end + buffer
                appointment.status = "agendada"
                appointment.version += 1
                appointment.recordatorio_24h_en = None
                appointment.recordatorio_2h_en = None
                self._session.flush()
        except IntegrityError as exc:
            if getattr(exc.orig, "sqlstate", None) == EXCLUSION_VIOLATION:
                raise SlotTaken() from exc
            raise
        self._event(
            appointment,
            "reprogramada",
            by,
            actor_user_id,
            {"antes": previous.isoformat(), "ahora": start.isoformat()},
        )
        return appointment

    def _first_free_for_move(
        self,
        appointment: Appointment,
        product: Product,
        start: datetime,
        end: datetime,
        until: datetime,
    ) -> Resource:
        own = (appointment.starts_at, appointment.ocupa_hasta)
        busy = self._busy(start, until)
        zone = self.zone()
        for resource in self._eligible(product):
            blocked = [i for i in busy.get(resource.id, []) if i != own]
            if self._inside_hours(self.hours_of(resource), zone, start, end) and not overlaps(
                start, until, blocked
            ):
                return resource
        raise SlotTaken()

    def cancel(
        self,
        appointment: Appointment,
        *,
        by: str,
        actor_user_id: uuid.UUID | None = None,
        motivo: str | None = None,
    ) -> Appointment:
        if appointment.status not in ("agendada", "confirmada"):
            raise SchedulingError("not_live", "Esa cita ya no está activa.")
        appointment.status = "cancelada"
        appointment.version += 1
        self._session.flush()
        self._event(appointment, "cancelada", by, actor_user_id, {"motivo": motivo or ""})
        return appointment

    def set_status(
        self,
        appointment: Appointment,
        action: str,
        *,
        by: str,
        actor_user_id: uuid.UUID | None = None,
    ) -> Appointment:
        """`confirmar`, `completar` or `no_asistio`."""
        status = STATUS_BY_ACTION.get(action)
        if status is None:
            raise SchedulingError("unknown_action", "Esa acción no existe.")
        if appointment.status == "cancelada":
            raise SchedulingError("not_live", "Esa cita está cancelada.")
        if status == "confirmada" and appointment.status != "agendada":
            raise SchedulingError("not_live", "Solo se confirma una cita agendada.")
        appointment.status = status
        self._session.flush()
        self._event(appointment, status, by, actor_user_id, {})
        return appointment

    def _event(
        self,
        appointment: Appointment,
        evento: str,
        actor: str,
        actor_user_id: uuid.UUID | None,
        detalle: dict[str, Any],
    ) -> None:
        self.events.add(
            appointment_id=appointment.id,
            evento=evento,
            actor=actor,
            detalle={**detalle, **({"user_id": str(actor_user_id)} if actor_user_id else {})},
        )

    # ------------------------------------------------------------------ read

    def conflicts(self) -> list[tuple[Appointment, str]]:
        """Live appointments to come that no longer fit: the hours changed, the resource was
        switched off, or a block was put over them. Nothing is cancelled on its own (spec 014,
        C-003): a person decides what to do with each one."""
        now = self.now()
        zone = self.zone()
        resources = {r.id: r for r in self.resources.all()}
        found: list[tuple[Appointment, str]] = []
        for appointment in self.appointments.between(now, now + timedelta(days=366)):
            if appointment.status not in ("agendada", "confirmada"):
                continue
            resource = resources.get(appointment.resource_id)
            if resource is None or not resource.is_active:
                found.append((appointment, "recurso_inactivo"))
            elif not self._inside_hours(
                self.hours_of(resource), zone, appointment.starts_at, appointment.ends_at
            ):
                found.append((appointment, "fuera_de_horario"))
            elif any(
                b.resource_id == resource.id
                for b in self.blocks.overlapping(appointment.starts_at, appointment.ocupa_hasta)
            ):
                found.append((appointment, "bloqueada"))
        return found

    def upcoming_of(self, contact_id: uuid.UUID) -> list[Appointment]:
        return list(self.appointments.live_of_contact(contact_id, self.now() - timedelta(hours=1)))
