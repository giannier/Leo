"""Dashboard data (Feature 010): daily aggregates, model prices, dashboard settings.

`metrics_daily` is one row per business per **local** day (the business's own
time zone), filled by the nightly rollup and recomputable at will; the panels
read it instead of aggregating messages live. Money never goes through a float:
sales are kept per currency as strings of Decimals, LLM cost as NUMERIC.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    Text,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PgUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, UUIDPrimaryKeyMixin

DEFAULT_MINUTES_PER_CONVERSATION = 4


class MetricsDaily(Base):
    __tablename__ = "metrics_daily"

    tenant_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("tenants.id"), primary_key=True
    )
    day: Mapped[date] = mapped_column(Date, primary_key=True)
    conversations: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    conversations_agent_only: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default="0"
    )
    conversations_with_person: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default="0"
    )
    messages_in: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    messages_out_agent: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    messages_out_person: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    first_responses: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    first_response_seconds: Mapped[int] = mapped_column(
        BigInteger, nullable=False, server_default="0"
    )
    handovers: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    handover_reasons: Mapped[dict[str, int]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    orders_confirmed: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    # {"PEN": "120.00"}: one total per currency, never added across currencies.
    sales: Mapped[dict[str, str]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    tokens_in: Mapped[int] = mapped_column(BigInteger, nullable=False, server_default="0")
    tokens_out: Mapped[int] = mapped_column(BigInteger, nullable=False, server_default="0")
    cost_usd: Mapped[Decimal] = mapped_column(Numeric(14, 6), nullable=False, server_default="0")
    computed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class ModelCost(Base, UUIDPrimaryKeyMixin):
    """What a model costs per 1 000 tokens, from a date on (RF-002), kept by an admin."""

    __tablename__ = "model_costs"
    __table_args__ = (Index("ix_model_costs_model_valid_from", "model", "valid_from"),)

    model: Mapped[str] = mapped_column(Text, nullable=False)
    input_per_1k_usd: Mapped[Decimal] = mapped_column(Numeric(12, 6), nullable=False)
    output_per_1k_usd: Mapped[Decimal] = mapped_column(Numeric(12, 6), nullable=False)
    valid_from: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_by: Mapped[uuid.UUID | None] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("users.id"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class DashboardSettings(Base):
    """Per business: the minutes a person would spend per conversation (HU-03)."""

    __tablename__ = "dashboard_settings"

    tenant_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("tenants.id"), primary_key=True
    )
    minutes_per_conversation: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=str(DEFAULT_MINUTES_PER_CONVERSATION)
    )
    # Feature 013: the satisfaction survey (off by default, C-102) and the monthly email
    # to the owner (on by default, C-108).
    csat_activo: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="false")
    reporte_mensual: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="true")
    # The first day of the last month whose report went out (idempotency).
    reporte_ultimo_mes: Mapped[date | None] = mapped_column(Date, nullable=True)

    def as_dict(self) -> dict[str, Any]:  # pragma: no cover - convenience
        return {"minutos_por_conversacion": self.minutes_per_conversation}
