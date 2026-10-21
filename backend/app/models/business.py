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
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.dialects.postgresql import UUID as PgUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin, UUIDPrimaryKeyMixin, enum_check

# Characters of the business's greeting (`agent_configs.saludo`). It reaches the model on
# every turn, so the cap is also a cost cap: 120 until 2026-10-20, then 400 (migration 0047).
MAX_SALUDO = 400

INDUSTRIES = (
    "restaurante",
    "instituto",
    "retail",
    "servicios",
    "salud",
    "tecnologia",
    "belleza",
    "inmobiliaria",
    "automotriz",
    "hogar",
    "turismo",
    "b2b",
    "otros",
)
AGENT_TONES = ("profesional", "cercano", "juvenil")
AGENT_OBJECTIVES = ("vender", "atender", "ambos")
# How fast the assistant moves a customer toward the close, how much it says
# per message, and which model tier answers (Feature 004). `INTELLIGENCE_LEVELS`
# is ordered from cheapest to best; a plan permits a subset (`plans.allowed_intelligence`).
AGENT_PACES = ("directo", "equilibrado", "paso_a_paso")
AGENT_DETAIL_LEVELS = ("breve", "moderado", "extenso")
INTELLIGENCE_LEVELS = ("economico", "medio", "mejor")
TRATAMIENTOS = ("tu", "usted")
EMOJI_LEVELS = ("no", "pocos", "si")
DISCOUNT_POLICIES = ("no_ofrece", "solo_promociones", "negociable")
DEFAULT_PROCESS_TEMPLATE = "productos"
# How a product is sold: `precio_fijo` goes into an order, `cotizacion` is handed
# to a person with a summary (Feature 004).
SALE_MODES = ("precio_fijo", "cotizacion")
# What a price covers: once, per month or per year (migration 0050).
PRICE_PERIODS = ("unico", "mes", "ano")
# What the agent does (migration 0051). `agendar` and `captar` are planned: the column accepts them,
# the screens offer only `CAPABILITIES_AVAILABLE`.
CAPABILITIES = ("atender", "vender", "cotizar", "agendar", "captar")
CAPABILITIES_AVAILABLE = ("atender", "vender", "cotizar")
BUNDLE_MIN_SIZE = 2
BUNDLE_MAX_SIZE = 50

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
        enum_check("tratamiento", TRATAMIENTOS, "tratamiento"),
        enum_check("emojis", EMOJI_LEVELS, "emojis"),
        enum_check("politica_descuentos", DISCOUNT_POLICIES, "politica_descuentos"),
        CheckConstraint(f"char_length(saludo) <= {MAX_SALUDO}", name="saludo_length"),
        CheckConstraint(
            "descuento_maximo_pct IS NULL OR descuento_maximo_pct BETWEEN 0 AND 100",
            name="descuento_maximo_pct",
        ),
        CheckConstraint("max_situaciones BETWEEN 1 AND 10", name="max_situaciones"),
        CheckConstraint(
            "capacidades <@ ARRAY['atender','vender','cotizar','agendar','captar']::text[] "
            "AND 'atender' = ANY(capacidades)",
            name="capacidades",
        ),
    )

    tenant_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("tenants.id"), primary_key=True
    )

    # What the agent does. The process template and the objective follow from it (feature 019).
    capacidades: Mapped[list[str]] = mapped_column(
        ARRAY(Text), nullable=False, server_default=text("ARRAY['atender','vender']::text[]")
    )
    # True when the owner picked the template by hand: capabilities then leave it alone.
    plantilla_manual: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="false")

    agent_name: Mapped[str] = mapped_column(Text, nullable=False, server_default="Leo")
    tone: Mapped[str] = mapped_column(Text, nullable=False, server_default="cercano")
    objective: Mapped[str] = mapped_column(Text, nullable=False, server_default="ambos")
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

    # Sales behaviour (Feature 004). `agent_name`, `tone` and `is_active` are
    # reused as is. The free-text `restrictions` became situations (017, RF-011).
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
    # The business profile by form (017, RF-010): how the assistant addresses the
    # customer, how many emojis, the greeting, and what it says about discounts.
    tratamiento: Mapped[str] = mapped_column(Text, nullable=False, server_default="tu")
    emojis: Mapped[str] = mapped_column(Text, nullable=False, server_default="pocos")
    saludo: Mapped[str | None] = mapped_column(Text, nullable=True)
    politica_descuentos: Mapped[str] = mapped_column(
        Text, nullable=False, server_default="solo_promociones"
    )
    # Only with `negociable`: the most a person of the business may grant. The
    # assistant never grants it; it hands the request over (017, C-14).
    descuento_maximo_pct: Mapped[Decimal | None] = mapped_column(Numeric(5, 2), nullable=True)
    # Most situations a turn may carry (017, C-10).
    max_situaciones: Mapped[int] = mapped_column(Integer, nullable=False, server_default="5")
    # Feature 011: ask once, after a confirmed sale, whether the customer wants promotions
    # (C-102); and the approved utility template for a follow-up after the 24-hour
    # window closed (C-110).
    optin_preguntar: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="true")
    seguimiento_plantilla_id: Mapped[uuid.UUID | None] = mapped_column(
        PgUUID(as_uuid=True),
        ForeignKey("message_templates.id", ondelete="SET NULL"),
        nullable=True,
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


class ProductCategory(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """A category of the catalogue: its name, its place in a list, on or off.

    Milestone B, decision 13. `products.category` keeps the display text and
    `products.category_id` points here; `ProductRepository` links them on every
    write. The option list (`mostrar_opciones`, tipo categorias) shows the active
    ones that have live products, by `orden` and then by name.
    """

    __tablename__ = "product_categories"
    __table_args__ = (
        CheckConstraint("char_length(btrim(nombre)) BETWEEN 1 AND 80", name="nombre_length"),
        Index(
            "uq_product_categories_tenant_id_nombre",
            "tenant_id",
            text("lower(nombre)"),
            unique=True,
        ),
    )

    tenant_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("tenants.id"), nullable=False
    )
    nombre: Mapped[str] = mapped_column(Text, nullable=False)
    orden: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    activa: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="true")


class Product(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """A catalog item the agent can quote. Soft-deleted."""

    __tablename__ = "products"
    __table_args__ = (
        CheckConstraint("price >= 0", name="ck_products_products_price_non_negative"),
        CheckConstraint("cardinality(tags) <= 10", name="products_tags_count"),
        CheckConstraint("cardinality(datos_a_pedir) <= 6", name="products_datos_a_pedir_count"),
        enum_check("periodo", PRICE_PERIODS, "periodo"),
        Index("ix_products_tags", "tags", postgresql_using="gin"),
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
    # The row of `product_categories` that `category` names (migration 0026).
    category_id: Mapped[uuid.UUID | None] = mapped_column(
        PgUUID(as_uuid=True),
        ForeignKey("product_categories.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    # Object storage keys, not URLs: the bucket may be private.
    image_key: Mapped[str | None] = mapped_column(Text, nullable=True)
    file_key: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Fit for sale (Feature 004, migration 0018).
    modo_venta: Mapped[str] = mapped_column(Text, nullable=False, server_default="precio_fijo")
    # What the price covers: `unico`, `mes` or `ano`. Shown and spoken; totals add the amount.
    periodo: Mapped[str] = mapped_column(Text, nullable=False, server_default="unico")
    # A digital product needs no delivery address.
    es_digital: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="false")
    # A bundle ("paquete") is a product with a size: the N selections that
    # complete it are catalogue products or, with `bundle_permite_libres`, names
    # the customer said ("certificamos cualquier tema").
    bundle_size: Mapped[int | None] = mapped_column(Integer, nullable=True)
    bundle_permite_libres: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default="false"
    )
    # The assistant pushes a highlighted product first.
    destacado: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="false")
    # What the agent asks before handing a quote over, one item per message ("para cuándo").
    datos_a_pedir: Mapped[list[str]] = mapped_column(
        ARRAY(Text), nullable=False, server_default=text("'{}'::text[]")
    )
    # Labels the business writes ("oferta", "SIAF"): the agent also finds the
    # product by them, and the catalogue filters by them (Feature 018).
    tags: Mapped[list[str]] = mapped_column(
        ARRAY(Text), nullable=False, server_default=text("'{}'::text[]")
    )

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
    # Who the account belongs to, as a receipt shows it (004 D-1, 017 RF-010):
    # a receipt to someone else is flagged `destinatario_distinto`.
    titular: Mapped[str | None] = mapped_column(Text, nullable=True)
