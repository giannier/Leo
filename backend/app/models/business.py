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
# How fast the assistant moves a customer toward the close, how much it says
# per message, and which model tier answers (Feature 004). `INTELLIGENCE_LEVELS`
# is ordered from cheapest to best; a plan permits a subset (`plans.allowed_intelligence`).
AGENT_PACES = ("directo", "equilibrado", "paso_a_paso")
AGENT_DETAIL_LEVELS = ("breve", "moderado", "extenso")
INTELLIGENCE_LEVELS = ("economico", "medio", "mejor")
DEFAULT_PROCESS_TEMPLATE = "productos"

PAYMENT_METHODS = ("yape", "plin", "transferencia", "tarjeta", "efectivo", "link")

DEFAULT_ESCALATION_RULES = '["cliente_molesto", "pide_humano", "fuera_de_catalogo"]'

# What somebody can write to be left alone (HU-03). Both words are here
# because both are used: "BAJA" is what a Peruvian customer types, "STOP" is
# what anyone who has used an English service types, and honouring only one of
# them means ignoring a request that was perfectly clear to the person making
# it.
DEFAULT_OPT_OUT_KEYWORDS = '["stop", "baja"]'


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
        enum_check("ritmo", AGENT_PACES, "ritmo"),
        enum_check("detalle", AGENT_DETAIL_LEVELS, "detalle"),
        enum_check("nivel_inteligencia", INTELLIGENCE_LEVELS, "nivel_inteligencia"),
        CheckConstraint("seguimiento_horas BETWEEN 1 AND 20", name="seguimiento_horas"),
        CheckConstraint("seguimiento_max BETWEEN 1 AND 3", name="seguimiento_max"),
        CheckConstraint("derivacion_pausa_horas BETWEEN 1 AND 72", name="derivacion_pausa_horas"),
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
    # Sent when somebody writes something this version cannot read - a voice
    # note, a video, a location (Feature 002, C-001). Configurable because the
    # right answer differs by business: a bakery asks for text, a clinic may
    # prefer to hand straight over to a person.
    unsupported_fallback: Mapped[str | None] = mapped_column(Text, nullable=True)

    # What a customer can write to be left alone (HU-03). Configurable per
    # tenant because the word people actually use is local: "BAJA" in Peru,
    # "STOP" from anyone who has used an English service, and a clinic may
    # want "NO MOLESTAR" as well. Empty means "use the defaults".
    opt_out_keywords: Mapped[list[str]] = mapped_column(
        JSONB, nullable=False, server_default=DEFAULT_OPT_OUT_KEYWORDS
    )

    # Sales behaviour (Feature 004). `restrictions` doubles as the tenant's
    # "additional rules"; `agent_name`, `tone` and `is_active` are reused as is.
    ritmo: Mapped[str] = mapped_column(Text, nullable=False, server_default="equilibrado")
    detalle: Mapped[str] = mapped_column(Text, nullable=False, server_default="moderado")
    seguimiento_activo: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default="false"
    )
    seguimiento_horas: Mapped[int] = mapped_column(Integer, nullable=False, server_default="4")
    seguimiento_max: Mapped[int] = mapped_column(Integer, nullable=False, server_default="2")
    nivel_inteligencia: Mapped[str] = mapped_column(
        Text, nullable=False, server_default="economico"
    )
    # How long the assistant stays quiet after handing a conversation to a person.
    derivacion_pausa_horas: Mapped[int] = mapped_column(Integer, nullable=False, server_default="6")
    # Which process template drives the conversation, and its validated options
    # (`app.schemas.process_options`). The template itself lives in code.
    process_template_key: Mapped[str] = mapped_column(
        Text, nullable=False, server_default=DEFAULT_PROCESS_TEMPLATE
    )
    process_options: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default="{}"
    )
    # Edited system messages (plantillas.md section 7), by key.
    system_messages: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default="{}"
    )

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
    # The QR or account image sent with this method (Feature 004). Removing the
    # asset leaves the method in place, just without its image.
    media_asset_id: Mapped[uuid.UUID | None] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("media_assets.id", ondelete="SET NULL"), nullable=True
    )
