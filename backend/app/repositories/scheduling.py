"""The calendar of one business (feature 019, phase D)."""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from datetime import datetime

from app.models.scheduling import (
    LIVE_STATUSES,
    Appointment,
    AppointmentEvent,
    Resource,
    ResourceBlock,
    SchedulingSettings,
)
from app.repositories.base import BaseRepository


class ResourceRepository(BaseRepository[Resource]):
    model = Resource

    def active(self) -> Sequence[Resource]:
        """In the order they were created: the first free one takes the appointment."""
        statement = self._scoped().where(Resource.is_active.is_(True)).order_by(Resource.created_at)
        return self._session.execute(statement).scalars().all()

    def all(self) -> Sequence[Resource]:
        return self._session.execute(self._scoped().order_by(Resource.created_at)).scalars().all()


class BlockRepository(BaseRepository[ResourceBlock]):
    model = ResourceBlock

    def overlapping(self, start: datetime, end: datetime) -> Sequence[ResourceBlock]:
        statement = self._scoped().where(
            ResourceBlock.starts_at < end, ResourceBlock.ends_at > start
        )
        return self._session.execute(statement).scalars().all()

    def upcoming(self, after: datetime) -> Sequence[ResourceBlock]:
        statement = (
            self._scoped().where(ResourceBlock.ends_at > after).order_by(ResourceBlock.starts_at)
        )
        return self._session.execute(statement).scalars().all()


class AppointmentRepository(BaseRepository[Appointment]):
    model = Appointment

    def live_overlapping(self, start: datetime, end: datetime) -> Sequence[Appointment]:
        """Live appointments whose range (buffer included) touches [start, end)."""
        statement = self._scoped().where(
            Appointment.status.in_(LIVE_STATUSES),
            Appointment.starts_at < end,
            Appointment.ocupa_hasta > start,
        )
        return self._session.execute(statement).scalars().all()

    def between(
        self, start: datetime, end: datetime, *, resource_id: uuid.UUID | None = None
    ) -> Sequence[Appointment]:
        """Every appointment that starts in [start, end), of any status, for the agenda."""
        statement = self._scoped().where(
            Appointment.starts_at >= start, Appointment.starts_at < end
        )
        if resource_id is not None:
            statement = statement.where(Appointment.resource_id == resource_id)
        return self._session.execute(statement.order_by(Appointment.starts_at)).scalars().all()

    def live_of_contact(self, contact_id: uuid.UUID, after: datetime) -> Sequence[Appointment]:
        statement = (
            self._scoped()
            .where(
                Appointment.contact_id == contact_id,
                Appointment.status.in_(LIVE_STATUSES),
                Appointment.starts_at >= after,
            )
            .order_by(Appointment.starts_at)
        )
        return self._session.execute(statement).scalars().all()

    def locked(self, appointment_id: uuid.UUID) -> Appointment | None:
        """One appointment, locked for the change a person or the agent is about to make."""
        statement = self._scoped().where(Appointment.id == appointment_id).with_for_update()
        return self._session.execute(statement).scalar_one_or_none()


class AppointmentEventRepository(BaseRepository[AppointmentEvent]):
    model = AppointmentEvent

    def for_appointment(self, appointment_id: uuid.UUID) -> Sequence[AppointmentEvent]:
        statement = (
            self._scoped()
            .where(AppointmentEvent.appointment_id == appointment_id)
            .order_by(AppointmentEvent.created_at)
        )
        return self._session.execute(statement).scalars().all()


class SchedulingSettingsRepository(BaseRepository[SchedulingSettings]):
    model = SchedulingSettings

    def current(self) -> SchedulingSettings | None:
        return self._session.execute(self._scoped()).scalar_one_or_none()

    def ensure(self) -> SchedulingSettings:
        """The row of this business, created with the defaults the first time it is needed."""
        existing = self.current()
        if existing is not None:
            return existing
        row = self.add()
        self._session.flush()
        self._session.refresh(row)
        return row
