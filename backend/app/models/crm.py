"""The mini-CRM of leads (Feature 012, migration 0040).

* `pipeline_stages`: the columns of the business's kanban. Five are semantic and fixed
  (`nuevo`, `interesado`, `por_pagar`, `ganado`, `perdido`): the sales process moves a
  lead through them by itself. The business may rename them, and add up to three of
  its own between them (`semantic` NULL), which only people use.
* `leads`: one person with commercial intent, one active lead at a time per contact.
* `lead_events`: append-only history of every move, with who (the process, a person,
  the system) and why.
* `lost_reasons`: why a lead was lost, editable by the business.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    CHAR,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PgUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, CreatedAtMixin, TimestampMixin, UUIDPrimaryKeyMixin, enum_check

# In the order a sale goes. `ganado` and `perdido` close a lead.
SEMANTIC_STAGES = ("nuevo", "interesado", "por_pagar", "ganado", "perdido")
DEFAULT_STAGE_LABELS = {
    "nuevo": "Nuevo",
    "interesado": "Interesado",
    "por_pagar": "Por pagar",
    "ganado": "Ganado",
    "perdido": "Perdido",
}
MAX_STAGES = 8
LEAD_STATUSES = ("active", "won", "lost", "discarded")
LEAD_ACTORS = ("agent", "user", "system")
# The quote a customer asked for (feature 019): handed over by the agent, then sent by a person.
QUOTE_STATUSES = ("solicitada", "enviada")
# How warm a lead is (feature 019, phase E): judged by the assistant, read by a person.
LEAD_SCORES = ("caliente", "tibio", "frio")
DEFAULT_LOST_REASONS = {
    "precio": "Precio",
    "sin_stock": "Sin stock",
    "dejo_de_responder": "Dejó de responder",
    "otro": "Otro",
}
LOST_NO_REPLY = "dejo_de_responder"


class PipelineStage(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "pipeline_stages"
    __table_args__ = (
        UniqueConstraint("tenant_id", "key", name="uq_pipeline_stages_tenant_id_key"),
        CheckConstraint(
            "semantic IS NULL OR semantic IN ('nuevo','interesado','por_pagar','ganado','perdido')",
            name="pipeline_stages_semantic",
        ),
        CheckConstraint("char_length(btrim(label)) BETWEEN 1 AND 40", name="pipeline_stages_label"),
    )

    tenant_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("tenants.id"), nullable=False
    )
    key: Mapped[str] = mapped_column(Text, nullable=False)
    label: Mapped[str] = mapped_column(Text, nullable=False)
    position: Mapped[int] = mapped_column(Integer, nullable=False)
    semantic: Mapped[str | None] = mapped_column(Text, nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="true")


class Lead(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "leads"
    __table_args__ = (
        enum_check("status", LEAD_STATUSES, "leads_status"),
        CheckConstraint(
            "quote_status IS NULL OR quote_status IN ('solicitada', 'enviada')",
            name="leads_quote_status",
        ),
        CheckConstraint(
            "score IS NULL OR score IN ('caliente', 'tibio', 'frio')", name="leads_score"
        ),
        Index("ix_leads_tenant_id_score", "tenant_id", "score"),
        CheckConstraint(
            "(amount_estimated IS NULL OR amount_estimated >= 0) "
            "AND (amount_real IS NULL OR amount_real >= 0)",
            name="leads_amounts_not_negative",
        ),
        # RF-002: one active lead per contact.
        Index(
            "uq_leads_active_per_contact",
            "tenant_id",
            "contact_id",
            unique=True,
            postgresql_where=text("status = 'active'"),
        ),
        Index("ix_leads_tenant_id_stage_id", "tenant_id", "stage_id"),
        Index("ix_leads_tenant_id_last_activity_at", "tenant_id", "last_activity_at"),
    )

    tenant_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("tenants.id"), nullable=False
    )
    contact_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("contacts.id"), nullable=False
    )
    # The latest conversation of the contact the lead was moved by (C-001).
    conversation_id: Mapped[uuid.UUID | None] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("conversations.id"), nullable=True
    )
    stage_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("pipeline_stages.id"), nullable=False
    )
    assigned_to: Mapped[uuid.UUID | None] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("users.id"), nullable=True
    )
    amount_estimated: Mapped[Decimal | None] = mapped_column(Numeric(12, 2), nullable=True)
    amount_real: Mapped[Decimal | None] = mapped_column(Numeric(12, 2), nullable=True)
    moneda: Mapped[str | None] = mapped_column(CHAR(3), nullable=True)
    product_interest: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Where the contact came from: the ad's headline, or NULL for "directo" (C-105).
    origen: Mapped[str | None] = mapped_column(Text, nullable=True)
    lost_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    # The order the lead is about, so a sale already won is not counted again.
    order_id: Mapped[uuid.UUID | None] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("orders.id", ondelete="SET NULL"), nullable=True
    )
    previous_lead_id: Mapped[uuid.UUID | None] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("leads.id"), nullable=True
    )
    status: Mapped[str] = mapped_column(Text, nullable=False, server_default="active")
    # The quote the customer asked for: the agent's structured hand-over and, once a person
    # sends it, the amount (`amount_estimated`) and the date (feature 019, migration 0054).
    quote_status: Mapped[str | None] = mapped_column(Text, nullable=True)
    quote_summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    quote_data: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    quote_requested_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    quote_sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # How warm it is and why, with what the customer answered (feature 019, migration 0057).
    score: Mapped[str | None] = mapped_column(Text, nullable=True)
    score_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    qualification: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    scored_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # Once a person moved it, the process only advances it with evidence (C-103).
    moved_by_person: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="false")
    stage_entered_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    last_activity_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class LeadEvent(Base, UUIDPrimaryKeyMixin, CreatedAtMixin):
    """One move of a lead. Append-only (RF-003)."""

    __tablename__ = "lead_events"
    __table_args__ = (
        enum_check("actor", LEAD_ACTORS, "lead_events_actor"),
        Index("ix_lead_events_lead_id_created_at", "lead_id", "created_at"),
    )

    tenant_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("tenants.id"), nullable=False
    )
    lead_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("leads.id", ondelete="CASCADE"), nullable=False
    )
    actor: Mapped[str] = mapped_column(Text, nullable=False)
    actor_user_id: Mapped[uuid.UUID | None] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("users.id"), nullable=True
    )
    from_stage: Mapped[str | None] = mapped_column(Text, nullable=True)
    to_stage: Mapped[str | None] = mapped_column(Text, nullable=True)
    # `turno`, `herramienta:gestionar_pedido`, `operador:confirmar`, `pedido_descartado`,
    # `persona:mover`, `persona:asignar`, ...
    cause: Mapped[str] = mapped_column(Text, nullable=False)
    detail: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, server_default="{}")


class LostReason(Base):
    __tablename__ = "lost_reasons"
    __table_args__ = (
        CheckConstraint("char_length(btrim(label)) BETWEEN 1 AND 60", name="lost_reasons_label"),
    )

    tenant_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("tenants.id"), primary_key=True
    )
    key: Mapped[str] = mapped_column(Text, primary_key=True)
    label: Mapped[str] = mapped_column(Text, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="true")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
