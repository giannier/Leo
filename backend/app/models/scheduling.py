"""Appointments and visits with a calendar of its own (feature 019, phase D; migration 0055).

* `resources`: who or what takes the appointment. Without a `horario` of its own it follows the
  business's opening hours (`business_profiles.opening_hours`), so the hours are written once.
* `resource_blocks`: time nobody can book (lunch, holidays, maintenance).
* `appointments`: the calendar. The database refuses two live appointments that overlap on one
  resource (`ex_appointments_resource_no_overlap`, an exclusion constraint written in the
  migration: it is the last word when two customers ask for the same minute). `ocupa_hasta` is
  `ends_at` plus the service's buffer.
* `appointment_events`: append-only history.
* `scheduling_settings`: the rules and the reminders (off until the owner turns them on).
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Text,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.dialects.postgresql import UUID as PgUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, CreatedAtMixin, TimestampMixin, UUIDPrimaryKeyMixin, enum_check

APPOINTMENT_STATUSES = ("agendada", "confirmada", "completada", "cancelada", "no_asistio")
# The ones that hold a place on the calendar.
LIVE_STATUSES = ("agendada", "confirmada")
APPOINTMENT_ACTORS = ("agent", "user")


class Resource(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "resources"
    __table_args__ = (
        CheckConstraint("char_length(btrim(nombre)) BETWEEN 1 AND 80", name="resources_nombre"),
    )

    tenant_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("tenants.id"), nullable=False, index=True
    )
    nombre: Mapped[str] = mapped_column(Text, nullable=False)
    # {"mon": [["09:00", "13:00"]], ...}; None follows the business's opening hours.
    horario: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    # Empty means it takes every bookable service.
    product_ids: Mapped[list[uuid.UUID]] = mapped_column(
        ARRAY(PgUUID(as_uuid=True)), nullable=False, server_default=text("'{}'::uuid[]")
    )
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="true")


class ResourceBlock(Base, UUIDPrimaryKeyMixin, CreatedAtMixin):
    __tablename__ = "resource_blocks"
    __table_args__ = (CheckConstraint("ends_at > starts_at", name="resource_blocks_range"),)

    tenant_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("tenants.id"), nullable=False, index=True
    )
    resource_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True),
        ForeignKey("resources.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    starts_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    ends_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    motivo: Mapped[str | None] = mapped_column(Text, nullable=True)


class Appointment(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "appointments"
    __table_args__ = (
        enum_check("status", APPOINTMENT_STATUSES, "appointments_status"),
        enum_check("created_by", APPOINTMENT_ACTORS, "appointments_created_by"),
        CheckConstraint(
            "ends_at > starts_at AND ocupa_hasta >= ends_at", name="appointments_range"
        ),
        Index("ix_appointments_tenant_id_starts_at", "tenant_id", "starts_at"),
    )

    tenant_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("tenants.id"), nullable=False
    )
    product_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("products.id"), nullable=False
    )
    resource_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("resources.id"), nullable=False
    )
    contact_id: Mapped[uuid.UUID | None] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("contacts.id"), nullable=True, index=True
    )
    conversation_id: Mapped[uuid.UUID | None] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("conversations.id"), nullable=True
    )
    # A walk-in or a phone call has no contact: the name and phone are written here.
    nombre_cliente: Mapped[str | None] = mapped_column(Text, nullable=True)
    telefono: Mapped[str | None] = mapped_column(Text, nullable=True)
    starts_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    ends_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    ocupa_hasta: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False, server_default="agendada")
    # Raised on every change of time, so a reminder claimed for an older time is not sent.
    version: Mapped[int] = mapped_column(Integer, nullable=False, server_default="1")
    notas: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_by: Mapped[str] = mapped_column(Text, nullable=False)
    recordatorio_24h_en: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    recordatorio_2h_en: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


class AppointmentEvent(Base, UUIDPrimaryKeyMixin, CreatedAtMixin):
    """One thing that happened to an appointment. Append-only."""

    __tablename__ = "appointment_events"
    __table_args__ = (
        Index("ix_appointment_events_appointment_id", "appointment_id", "created_at"),
    )

    tenant_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("tenants.id"), nullable=False
    )
    appointment_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("appointments.id", ondelete="CASCADE"), nullable=False
    )
    evento: Mapped[str] = mapped_column(Text, nullable=False)
    actor: Mapped[str] = mapped_column(Text, nullable=False)
    detalle: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )


class SchedulingSettings(Base):
    __tablename__ = "scheduling_settings"
    __table_args__ = (
        CheckConstraint("anticipacion_min_horas BETWEEN 0 AND 72", name="scheduling_settings_min"),
        CheckConstraint("anticipacion_max_dias BETWEEN 1 AND 180", name="scheduling_settings_max"),
        CheckConstraint("max_citas_activas BETWEEN 1 AND 10", name="scheduling_settings_active"),
    )

    tenant_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("tenants.id"), primary_key=True
    )
    anticipacion_min_horas: Mapped[int] = mapped_column(Integer, nullable=False, server_default="2")
    anticipacion_max_dias: Mapped[int] = mapped_column(Integer, nullable=False, server_default="30")
    max_citas_activas: Mapped[int] = mapped_column(Integer, nullable=False, server_default="2")
    recordatorio_24h: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="false")
    recordatorio_2h: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="false")
    # The approved template that carries a reminder sent outside the 24-hour window.
    plantilla_recordatorio_id: Mapped[uuid.UUID | None] = mapped_column(
        PgUUID(as_uuid=True),
        ForeignKey("message_templates.id", ondelete="SET NULL"),
        nullable=True,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )
