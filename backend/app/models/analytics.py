"""Satisfaction surveys and Leo's own NPS (Feature 013, migration 0043).

* `csat_surveys`: one "del 1 al 5" question to a customer, and their answer (C-102, C-103).
* `nps_responses`: what the business thinks of Leo, once per cycle and person (C-107).
"""

from __future__ import annotations

import uuid
from datetime import date, datetime

from sqlalchemy import (
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import UUID as PgUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, CreatedAtMixin, UUIDPrimaryKeyMixin, enum_check

CSAT_STATUSES = ("sent", "answered", "expired")
CSAT_TRIGGERS = ("ganado", "cerrada")


class CsatSurvey(Base, UUIDPrimaryKeyMixin):
    __tablename__ = "csat_surveys"
    __table_args__ = (
        enum_check("status", CSAT_STATUSES, "csat_surveys_status"),
        enum_check("trigger", CSAT_TRIGGERS, "csat_surveys_trigger"),
        CheckConstraint("score IS NULL OR score BETWEEN 1 AND 5", name="csat_surveys_score"),
        Index("ix_csat_surveys_tenant_id_sent_at", "tenant_id", "sent_at"),
        Index("ix_csat_surveys_contact_id_sent_at", "contact_id", "sent_at"),
    )

    tenant_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("tenants.id"), nullable=False
    )
    conversation_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("conversations.id", ondelete="CASCADE"), nullable=False
    )
    contact_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("contacts.id", ondelete="CASCADE"), nullable=False
    )
    lead_id: Mapped[uuid.UUID | None] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("leads.id", ondelete="SET NULL"), nullable=True
    )
    # The person who wrote last in that conversation; None = the assistant (C-105).
    operator_id: Mapped[uuid.UUID | None] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    message_id: Mapped[uuid.UUID | None] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("messages.id", ondelete="SET NULL"), nullable=True
    )
    trigger: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False, server_default="sent")
    score: Mapped[int | None] = mapped_column(Integer, nullable=True)
    comment: Mapped[str | None] = mapped_column(Text, nullable=True)
    sent_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    answered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class NpsResponse(Base, UUIDPrimaryKeyMixin, CreatedAtMixin):
    __tablename__ = "nps_responses"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id", "user_id", "cycle", name="uq_nps_responses_tenant_id_user_id_cycle"
        ),
        CheckConstraint("score IS NULL OR score BETWEEN 0 AND 10", name="nps_responses_score"),
    )

    tenant_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("tenants.id"), nullable=False
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    # The first day of the cycle the answer belongs to.
    cycle: Mapped[date] = mapped_column(Date, nullable=False)
    score: Mapped[int | None] = mapped_column(Integer, nullable=True)
    comment: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Closed without answering: it does not come back until the next cycle.
    dismissed: Mapped[bool] = mapped_column(nullable=False, server_default="false")
