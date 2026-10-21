"""Booking appointments: the calendar, its rules and the double booking (feature 019, phase D)."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models.business import Product
from app.models.scheduling import Appointment, ResourceBlock
from app.models.tenant import Tenant
from app.scheduling.service import Scheduling, SchedulingError, SlotTaken
from tests.integration.test_turn_pipeline import _conversation, _tenant

LIMA = ZoneInfo("America/Lima")
# A Monday: the tests pin the clock so the rules (notice, horizon) are exact.
TODAY = date(2026, 10, 26)
NOW = datetime(2026, 10, 26, 8, 0, tzinfo=LIMA).astimezone(UTC)
HOURS = {d: [["09:00", "13:00"], ["15:00", "19:00"]] for d in ("mon", "tue", "wed", "thu", "fri")}


def at(hour: int, minute: int = 0, *, days: int = 0) -> datetime:
    day = TODAY + timedelta(days=days)
    return datetime(day.year, day.month, day.day, hour, minute, tzinfo=LIMA).astimezone(UTC)


def _business(db: Session, *, hours: dict[str, object] | None = None) -> Tenant:
    tenant = _tenant(db, template="servicios")
    from sqlalchemy import select

    from app.models.business import BusinessProfile

    profile = db.execute(
        select(BusinessProfile).where(BusinessProfile.tenant_id == tenant.id)
    ).scalar_one()
    profile.opening_hours = HOURS if hours is None else hours
    profile.timezone = "America/Lima"
    db.flush()
    return tenant


def _service(
    db: Session, tenant: Tenant, *, minutes: int = 60, buffer: int = 0, name: str = "Consulta"
) -> Product:
    product = Product(
        tenant_id=tenant.id,
        name=name,
        price=Decimal("80"),
        agendable=True,
        duracion_min=minutes,
        buffer_min=buffer,
    )
    db.add(product)
    db.flush()
    return product


def _scheduling(db: Session, tenant: Tenant) -> Scheduling:
    scheduling = Scheduling(db, tenant.id, now=NOW)
    scheduling.ensure_resource()
    return scheduling


class TestOffering:
    def test_the_first_offer_follows_the_notice_and_the_business_hours(self, db: Session) -> None:
        tenant = _business(db)
        service = _service(db, tenant)
        scheduling = _scheduling(db, tenant)

        slots = scheduling.offer(service, days=1)

        # Today is Monday 08:00 and the notice is 2 hours: the first start is 10:00.
        assert slots[0].start == at(10)
        assert at(9) not in [s.start for s in slots]
        assert slots[-1].start == at(18)

    def test_a_booked_hour_is_not_offered_again(self, db: Session) -> None:
        tenant = _business(db)
        service = _service(db, tenant)
        scheduling = _scheduling(db, tenant)
        scheduling.book(service, at(10), by="user")

        starts = [s.start for s in scheduling.offer(service, days=1)]

        assert at(10) not in starts and at(10, 30) not in starts and at(11) in starts

    def test_a_blocked_afternoon_is_not_offered(self, db: Session) -> None:
        tenant = _business(db)
        service = _service(db, tenant)
        scheduling = _scheduling(db, tenant)
        resource = scheduling.ensure_resource()
        db.add(
            ResourceBlock(
                tenant_id=tenant.id, resource_id=resource.id, starts_at=at(15), ends_at=at(19)
            )
        )
        db.flush()

        starts = [s.start for s in scheduling.offer(service, days=1)]

        assert starts and all(s < at(15) for s in starts)

    def test_a_resource_with_its_own_hours_does_not_use_the_business_s(self, db: Session) -> None:
        tenant = _business(db)
        service = _service(db, tenant)
        scheduling = _scheduling(db, tenant)
        scheduling.ensure_resource().horario = {"mon": [["16:00", "18:00"]]}
        db.flush()

        starts = [s.start for s in scheduling.offer(service, days=1)]

        assert starts == [at(16), at(16, 30), at(17)]

    def test_a_resource_that_takes_only_other_services_is_not_used(self, db: Session) -> None:
        tenant = _business(db)
        service = _service(db, tenant)
        other = _service(db, tenant, name="Otro")
        scheduling = _scheduling(db, tenant)
        scheduling.ensure_resource().product_ids = [other.id]
        db.flush()

        assert scheduling.offer(service, days=1) == []
        assert scheduling.offer(other, days=1)

    def test_nothing_beyond_the_horizon(self, db: Session) -> None:
        tenant = _business(db)
        service = _service(db, tenant)
        scheduling = _scheduling(db, tenant)
        scheduling.settings().anticipacion_max_dias = 3
        db.flush()

        days = {s.start.astimezone(LIMA).date() for s in scheduling.offer(service, days=30)}

        assert max(days) <= TODAY + timedelta(days=3)

    def test_without_opening_hours_nothing_can_be_booked(self, db: Session) -> None:
        tenant = _business(db, hours={})
        service = _service(db, tenant)

        assert _scheduling(db, tenant).offer(service) == []


class TestBooking:
    def test_the_rules_refuse_what_is_too_soon_too_far_or_too_many(self, db: Session) -> None:
        tenant = _business(db)
        service = _service(db, tenant)
        scheduling = _scheduling(db, tenant)
        contact = _conversation(db, tenant, channel="whatsapp").contact_id

        with pytest.raises(SchedulingError) as soon:
            scheduling.book(service, at(9), by="agent")  # 1 hour away, 2 required
        with pytest.raises(SchedulingError) as far:
            scheduling.book(service, at(10, days=60), by="agent")
        scheduling.book(service, at(10), by="agent", contact_id=contact)
        scheduling.book(service, at(11), by="agent", contact_id=contact)
        with pytest.raises(SchedulingError) as many:
            scheduling.book(service, at(12), by="agent", contact_id=contact)

        assert (soon.value.code, far.value.code, many.value.code) == (
            "too_soon",
            "too_far",
            "limit",
        )

    def test_outside_the_opening_hours_is_refused(self, db: Session) -> None:
        tenant = _business(db)
        service = _service(db, tenant)
        scheduling = _scheduling(db, tenant)

        with pytest.raises(SlotTaken):
            scheduling.book(service, at(13, 30), by="user")  # lunch
        with pytest.raises(SlotTaken):
            scheduling.book(service, at(12, 30), by="user")  # would end after 13:00

    def test_the_same_minute_cannot_be_booked_twice(self, db: Session) -> None:
        tenant = _business(db)
        service = _service(db, tenant)
        scheduling = _scheduling(db, tenant)
        scheduling.book(service, at(10), by="user")

        with pytest.raises(SlotTaken):
            scheduling.book(service, at(10, 30), by="agent")

    def test_the_buffer_after_a_service_is_kept_free(self, db: Session) -> None:
        tenant = _business(db)
        service = _service(db, tenant, minutes=60, buffer=30)
        scheduling = _scheduling(db, tenant)
        scheduling.book(service, at(10), by="user")  # busy until 11:30

        with pytest.raises(SlotTaken):
            scheduling.book(service, at(11), by="user")
        assert scheduling.book(service, at(11, 30), by="user").starts_at == at(11, 30)

    def test_a_second_resource_takes_the_overflow(self, db: Session) -> None:
        tenant = _business(db)
        service = _service(db, tenant)
        scheduling = _scheduling(db, tenant)
        second = scheduling.resources.add(nombre="Consultorio 2")

        first = scheduling.book(service, at(10), by="user")
        other = scheduling.book(service, at(10), by="user")

        assert {first.resource_id, other.resource_id} == {
            scheduling.ensure_resource().id,
            second.id,
        }
        with pytest.raises(SlotTaken):
            scheduling.book(service, at(10), by="user")

    def test_the_database_has_the_last_word_when_the_code_is_raced(
        self, db: Session, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Two requests both saw the minute free: the exclusion constraint refuses the second."""
        tenant = _business(db)
        service = _service(db, tenant)
        scheduling = _scheduling(db, tenant)
        scheduling.book(service, at(10), by="user")
        resource = scheduling.ensure_resource()
        monkeypatch.setattr(scheduling, "_first_free", lambda *args, **kwargs: resource)

        with pytest.raises(SlotTaken):
            scheduling.book(service, at(10), by="agent")

    def test_the_constraint_is_in_the_table_itself(self, db: Session) -> None:
        tenant = _business(db)
        service = _service(db, tenant)
        resource = _scheduling(db, tenant).ensure_resource()
        row = {
            "tenant_id": tenant.id,
            "product_id": service.id,
            "resource_id": resource.id,
            "created_by": "user",
        }
        db.add(Appointment(**row, starts_at=at(10), ends_at=at(11), ocupa_hasta=at(11)))
        db.flush()

        with pytest.raises(IntegrityError), db.begin_nested():
            db.add(
                Appointment(**row, starts_at=at(10, 30), ends_at=at(11, 30), ocupa_hasta=at(11, 30))
            )
            db.flush()

    def test_a_cancelled_appointment_frees_its_place(self, db: Session) -> None:
        tenant = _business(db)
        service = _service(db, tenant)
        scheduling = _scheduling(db, tenant)
        first = scheduling.book(service, at(10), by="user")

        scheduling.cancel(first, by="user")
        again = scheduling.book(service, at(10), by="agent")

        assert first.status == "cancelada" and again.status == "agendada"


class TestChangingAnAppointment:
    def test_rescheduling_moves_it_raises_the_version_and_frees_the_old_place(
        self, db: Session
    ) -> None:
        tenant = _business(db)
        service = _service(db, tenant)
        scheduling = _scheduling(db, tenant)
        appointment = scheduling.book(service, at(10), by="agent")
        appointment.recordatorio_24h_en = NOW
        version = appointment.version

        scheduling.reschedule(appointment, at(11), by="agent")

        assert appointment.starts_at == at(11) and appointment.version == version + 1
        assert appointment.recordatorio_24h_en is None  # the reminder is for the new time
        assert at(10) in [s.start for s in scheduling.offer(service, days=1)]

    def test_it_can_move_onto_part_of_its_own_time(self, db: Session) -> None:
        tenant = _business(db)
        service = _service(db, tenant)
        scheduling = _scheduling(db, tenant)
        appointment = scheduling.book(service, at(10), by="agent")

        scheduling.reschedule(appointment, at(10, 30), by="agent")

        assert appointment.starts_at == at(10, 30)

    def test_it_cannot_move_onto_somebody_else(self, db: Session) -> None:
        tenant = _business(db)
        service = _service(db, tenant)
        scheduling = _scheduling(db, tenant)
        scheduling.book(service, at(11), by="user")
        mine = scheduling.book(service, at(10), by="agent")

        with pytest.raises(SlotTaken):
            scheduling.reschedule(mine, at(11), by="agent")
        assert mine.starts_at == at(10)

    def test_confirming_and_closing(self, db: Session) -> None:
        tenant = _business(db)
        service = _service(db, tenant)
        scheduling = _scheduling(db, tenant)
        appointment = scheduling.book(service, at(10), by="agent")

        scheduling.set_status(appointment, "confirmar", by="user")
        assert appointment.status == "confirmada"
        scheduling.set_status(appointment, "no_asistio", by="user")
        assert appointment.status == "no_asistio"
        with pytest.raises(SchedulingError):
            scheduling.cancel(appointment, by="user")

    def test_every_change_leaves_an_event(self, db: Session) -> None:
        tenant = _business(db)
        service = _service(db, tenant)
        scheduling = _scheduling(db, tenant)
        appointment = scheduling.book(service, at(10), by="agent")
        scheduling.reschedule(appointment, at(11), by="agent")
        scheduling.cancel(appointment, by="user", motivo="ya no puede")

        events = [e.evento for e in scheduling.events.for_appointment(appointment.id)]

        assert events == ["agendada", "reprogramada", "cancelada"]


class TestEachBusinessSeesItsOwn:
    def test_another_business_neither_sees_nor_blocks_my_appointments(self, db: Session) -> None:
        mine, theirs = _business(db), _business(db)
        service_mine, service_theirs = _service(db, mine), _service(db, theirs)
        a, b = _scheduling(db, mine), _scheduling(db, theirs)
        booked = a.book(service_mine, at(10), by="user")

        b.book(service_theirs, at(10), by="user")  # the same minute, another business: free

        assert b.appointments.get(booked.id) is None
        assert [x.id for x in a.appointments.between(at(0), at(23))] == [booked.id]


class TestAFirstBusinessNeedsNoSetup:
    def test_the_single_resource_is_created_the_first_time_it_is_needed(self, db: Session) -> None:
        tenant = _business(db)
        service = _service(db, tenant)
        scheduling = Scheduling(db, tenant.id, now=NOW)  # nobody created a resource

        slots = scheduling.offer(service, days=1)

        assert slots and slots[0].start == at(10)
        assert [r.nombre for r in scheduling.resources.all()] == ["Atención"]

    def test_a_business_that_switched_every_resource_off_does_not_get_a_new_one(
        self, db: Session
    ) -> None:
        tenant = _business(db)
        service = _service(db, tenant)
        scheduling = _scheduling(db, tenant)
        scheduling.ensure_resource().is_active = False
        db.flush()

        assert scheduling.offer(service, days=1) == []
        assert len(scheduling.resources.all()) == 1


class TestWhatNoLongerFits:
    def test_a_changed_schedule_a_block_or_a_switched_off_resource_are_listed_not_cancelled(
        self, db: Session
    ) -> None:
        tenant = _business(db)
        service = _service(db, tenant)
        scheduling = _scheduling(db, tenant)
        fits = scheduling.book(service, at(10, days=1), by="user")
        moved_hours = scheduling.book(service, at(16, days=1), by="user")
        blocked = scheduling.book(service, at(11, days=2), by="user")
        cancelled = scheduling.book(service, at(10, days=3), by="user")
        scheduling.cancel(cancelled, by="user")
        resource = scheduling.ensure_resource()
        resource.horario = {d: [["09:00", "13:00"]] for d in ("mon", "tue", "wed", "thu", "fri")}
        db.add(
            ResourceBlock(
                tenant_id=tenant.id,
                resource_id=resource.id,
                starts_at=at(11, days=2),
                ends_at=at(12, days=2),
            )
        )
        db.flush()

        found = {a.id: reason for a, reason in scheduling.conflicts()}

        assert found == {moved_hours.id: "fuera_de_horario", blocked.id: "bloqueada"}
        assert (
            fits.status == "agendada" and moved_hours.status == "agendada"
        )  # nothing was cancelled

        resource.is_active = False
        db.flush()
        assert {reason for _, reason in scheduling.conflicts()} == {"recurso_inactivo"}

    def test_the_past_is_not_a_conflict(self, db: Session) -> None:
        tenant = _business(db)
        service = _service(db, tenant)
        scheduling = _scheduling(db, tenant)
        scheduling.book(service, at(10, days=1), by="user")
        later = Scheduling(db, tenant.id, now=at(10, days=2))  # the appointment already happened

        assert later.conflicts() == []
