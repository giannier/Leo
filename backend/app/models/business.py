"""Everything a tenant configures about its own business.

These are the entities the onboarding wizard writes to. There is no separate
"wizard state" table: progress is derived from these rows at read time
(Clarification 006/009).
"""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import (
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

from app.models.base import Base, TimestampMixin, UUIDPrimaryKeyMixin, enum_check

INDUSTRIES = ("restaurante", "instituto", "retail", "servicios", "salud", "otros")
AGENT_TONES = ("profesional", "cercano", "juvenil")
AGENT_OBJECTIVES = ("vender", "atender", "ambos")
PAYMENT_METHODS = ("yape", "plin", "transferencia", "tarjeta", "efectivo", "link")

DEFAULT_ESCALATION_RULES = '["cliente_molesto", "pide_humano", "fuera_de_catalogo"]'


class BusinessProfile(Base):
    """Identity of the tenant's business. One row per tenant."""

    __tablename__ = "business_profiles"
    __table_args__ = (enum_check("industry", INDUSTRIES, "business_profiles_industry"),)

    # The tenant id doubles as the primary key: this is a 1-to-1 extension of
    # `tenants`, not a collection.
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("tenants.id"), primary_key=True
    )

    business_name: Mapped[str | None] = mapped_column(Text, nullable=True)
    industry: Mapped[str | None] = mapped_column(Text, nullable=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    website: Mapped[str | None] = mapped_column(Text, nullable=True)
    social_links: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, server_default="{}")

    currency: Mapped[str] = mapped_column(Text, nullable=False, server_default="PEN")
    timezone: Mapped[str] = mapped_column(Text, nullable=False, server_default="America/Lima")
    # {"mon": [["09:00", "18:00"]], ...}
    opening_hours: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default="{}"
    )

    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        # See TimestampMixin: `now()` is transaction-scoped and would stamp
        # two writes in one transaction identically.
        onupdate=func.clock_timestamp(),
    )


class AgentConfig(Base):
    """The tenant's agent personality and guardrails. One row per tenant."""

    __tablename__ = "agent_configs"
    __table_args__ = (
        enum_check("tone", AGENT_TONES, "agent_configs_tone"),
        enum_check("objective", AGENT_OBJECTIVES, "agent_configs_objective"),
    )

    tenant_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("tenants.id"), primary_key=True
    )

    agent_name: Mapped[str] = mapped_column(Text, nullable=False, server_default="Leo")
    tone: Mapped[str] = mapped_column(Text, nullable=False, server_default="cercano")
    objective: Mapped[str] = mapped_column(Text, nullable=False, server_default="ambos")
    # Free text, e.g. "no ofrecer descuentos".
    restrictions: Mapped[str | None] = mapped_column(Text, nullable=True)
    escalation_rules: Mapped[list[str]] = mapped_column(
        JSONB, nullable=False, server_default=DEFAULT_ESCALATION_RULES
    )
    # Sent when the trial quota runs out mid-conversation (Clarification 004).
    farewell_on_pause: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Global on/off switch for the agent.
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="false")
    activated_first_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        # See TimestampMixin: `now()` is transaction-scoped and would stamp
        # two writes in one transaction identically.
        onupdate=func.clock_timestamp(),
    )


class Product(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """A catalog item the agent can quote. Soft-deleted."""

    __tablename__ = "products"
    __table_args__ = (
        CheckConstraint("price >= 0", name="ck_products_products_price_non_negative"),
        # Partial index: soft-deleted rows are never listed, so keeping them
        # out of the index keeps it small and the catalog query fast.
        Index(
            "ix_products_tenant_id_active",
            "tenant_id",
            postgresql_where=text("deleted_at IS NULL"),
        ),
    )

    tenant_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("tenants.id"), nullable=False
    )
    name: Mapped[str] = mapped_column(Text, nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Money is always Decimal, never float (Constitution).
    price: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    # NULL means this product does not track stock at all.
    stock: Mapped[int | None] = mapped_column(Integer, nullable=True)
    category: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Object storage keys, not URLs: the bucket may be private.
    image_key: Mapped[str | None] = mapped_column(Text, nullable=True)
    file_key: Mapped[str | None] = mapped_column(Text, nullable=True)

    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="true")
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class PaymentMethodBusiness(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """How this business gets paid, as the agent should explain it."""

    __tablename__ = "payment_methods_business"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id", "method", name="uq_payment_methods_business_tenant_id_method"
        ),
        enum_check("method", PAYMENT_METHODS, "payment_methods_business_method"),
    )

    tenant_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("tenants.id"), nullable=False
    )
    method: Mapped[str] = mapped_column(Text, nullable=False)
    # e.g. "Yape al 9XXXXXXXX a nombre de ...".
    instructions: Mapped[str] = mapped_column(Text, nullable=False)
    # Turns on the receipt-vision tool that arrives in Feature 004.
    validate_receipts: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="false")
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="true")
