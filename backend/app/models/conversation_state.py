"""Where a conversation stands in the sales process (Feature 004, migration 0014).

Two tables. `conversation_state` is one row per conversation and holds the
current stage, the sales-funnel column it maps to and the facts that only exist
at conversation level. `conversation_stage_events` is the append-only history of
every change of stage or funnel, so "why did this customer end up here" has an
answer.

Only *stored* facts live in `facts` (C-03). Anything derivable from another
table - the active order, missing customer data, payment state - is computed on
each turn and never written here, so it cannot drift from its source.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from enum import StrEnum
from typing import Any

from sqlalchemy import Boolean, DateTime, ForeignKey, Index, Integer, Text, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PgUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, UUIDPrimaryKeyMixin, enum_check


class FunnelCode(StrEnum):
    """The funnel columns, spelled exactly as the holding's central `control`
    base spells them (C-18), so the dashboard can replicate them as they are.

    `PERDIDO` is reserved: Feature 010 sets it, the engine never does.
    """

    NUEVO = "NUEVO"
    ATENDIDO = "ATENDIDO"
    NEGOCIACION = "NEGOCIACION"
    CUENTAS_ENVIADAS = "CUENTAS_ENVIADAS"
    EN_VERIFICACION = "EN_VERIFICACION"
    GANADO = "GANADO"
    DERIVADO = "DERIVADO"
    PERDIDO = "PERDIDO"


FUNNEL_CODES = tuple(code.value for code in FunnelCode)


class ConversationState(Base):
    """The current stage, funnel column and stored facts of one conversation."""

    __tablename__ = "conversation_state"
    __table_args__ = (
        enum_check("funnel_code", FUNNEL_CODES, "funnel_code"),
        Index("ix_conversation_state_tenant_id_stage_key", "tenant_id", "stage_key"),
        Index("ix_conversation_state_tenant_id_funnel_code", "tenant_id", "funnel_code"),
    )

    # The conversation id is the primary key: this is a 1-to-1 extension of
    # `conversations`, not a collection. There is no `id` column.
    conversation_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("conversations.id"), primary_key=True
    )
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("tenants.id"), nullable=False
    )

    # Which process template drives this conversation, and which version of it.
    # The templates live in code (C-05); these two columns are what let a
    # conversation keep the rules it started with when a template changes.
    template_key: Mapped[str] = mapped_column(Text, nullable=False)
    template_version: Mapped[int] = mapped_column(Integer, nullable=False)
    stage_key: Mapped[str] = mapped_column(Text, nullable=False)
    funnel_code: Mapped[str] = mapped_column(Text, nullable=False)

    facts: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, server_default="{}")
    # Redundant with `conversations.channel = 'sandbox'`, kept because the
    # panel's indexes and the "reset" action filter on it without a join.
    is_sandbox: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="false")
    # Optimistic concurrency: a save carries the version it read.
    version: Mapped[int] = mapped_column(Integer, nullable=False, server_default="1")

    stage_entered_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        # See TimestampMixin: `now()` is transaction-scoped.
        onupdate=func.clock_timestamp(),
    )


class ConversationStageEvent(Base, UUIDPrimaryKeyMixin):
    """One change of stage or funnel column. Append-only."""

    __tablename__ = "conversation_stage_events"
    __table_args__ = (
        Index(
            "ix_conversation_stage_events_conversation_id_created_at",
            "conversation_id",
            "created_at",
        ),
        Index("ix_conversation_stage_events_tenant_id_created_at", "tenant_id", "created_at"),
    )

    tenant_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("tenants.id"), nullable=False
    )
    conversation_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("conversations.id"), nullable=False
    )
    from_stage: Mapped[str | None] = mapped_column(Text, nullable=True)
    to_stage: Mapped[str] = mapped_column(Text, nullable=False)
    from_funnel: Mapped[str | None] = mapped_column(Text, nullable=True)
    to_funnel: Mapped[str] = mapped_column(Text, nullable=False)
    # `turno`, `herramienta:<nombre>`, `comprobante`, `operador:confirmar`, ...
    # Free text on purpose: the vocabulary grows with the features that change
    # facts outside a turn, and a CHECK would turn each of them into a
    # migration for no safety gain.
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    # Facts at the moment of the change, without personal data.
    facts_snapshot: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)

    # `clock_timestamp()`, not `now()`: a turn can move a conversation through
    # several stages inside one transaction, and `now()` would stamp them all
    # identically and lose their order (same reasoning as `messages`).
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.clock_timestamp()
    )
