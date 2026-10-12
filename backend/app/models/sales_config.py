"""What a tenant tells the sales engine about its business (Feature 004).

`business_info` holds the five structured sections (locations, deliveries,
customer types, returns, contact); `faqs` are the questions the assistant
answers verbatim; `promotions` are the offers the pricing code applies. None of
them is free text the model has to interpret: every section has a schema, and
the numbers the assistant quotes come from these rows, never from the model.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    ARRAY,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    Text,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PgUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin, UUIDPrimaryKeyMixin, enum_check

BUSINESS_INFO_SECTIONS = ("locales", "entregas", "tipos_cliente", "devoluciones", "contacto")

# `automatica` applies by itself when the order qualifies; `cierre` (coupon,
# "I'll think about it", last follow-up) applies only if the assistant offered
# it in this conversation (C-10).
PROMOTION_USES = ("automatica", "cierre")
PROMOTION_TYPES = ("porcentaje", "monto_fijo", "nxm", "precio_cantidad")
PROMOTION_SCOPES = ("todo", "productos", "categorias")


class BusinessInfo(Base):
    """One structured section of a tenant's business information."""

    __tablename__ = "business_info"
    __table_args__ = (enum_check("section", BUSINESS_INFO_SECTIONS, "section"),)

    tenant_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("tenants.id"), primary_key=True
    )
    section: Mapped[str] = mapped_column(Text, primary_key=True)
    # Validated against the section's Pydantic schema before it gets here
    # (`app.schemas.business_info`); the database only guarantees it is JSON.
    data: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        # See TimestampMixin: `now()` is transaction-scoped.
        onupdate=func.clock_timestamp(),
    )


class Faq(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """A question and the answer the business wants given."""

    __tablename__ = "faqs"
    __table_args__ = (
        CheckConstraint("char_length(pregunta) BETWEEN 3 AND 300", name="pregunta_length"),
        CheckConstraint("char_length(respuesta) BETWEEN 1 AND 2000", name="respuesta_length"),
        Index("ix_faqs_tenant_id_is_active_orden", "tenant_id", "is_active", "orden"),
    )

    tenant_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("tenants.id"), nullable=False
    )
    pregunta: Mapped[str] = mapped_column(Text, nullable=False)
    respuesta: Mapped[str] = mapped_column(Text, nullable=False)
    orden: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="true")
    # The knowledge source this FAQ is indexed as, so `consultar_conocimiento`
    # finds it. SET NULL: removing the source must not remove the FAQ.
    knowledge_source_id: Mapped[uuid.UUID | None] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("knowledge_sources.id", ondelete="SET NULL"), nullable=True
    )


class Promotion(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """An offer the pricing function can apply. Money is always `Decimal`."""

    __tablename__ = "promotions"
    # A CHECK that evaluates to NULL passes, so each rule states its NOT NULL
    # requirements explicitly instead of trusting the comparison to fail.
    __table_args__ = (
        enum_check("uso", PROMOTION_USES, "uso"),
        enum_check("tipo", PROMOTION_TYPES, "tipo"),
        enum_check("aplica_a", PROMOTION_SCOPES, "aplica_a"),
        CheckConstraint(
            "tipo <> 'nxm' OR (n IS NOT NULL AND m IS NOT NULL AND n > m AND m >= 1)",
            name="nxm",
        ),
        CheckConstraint(
            "tipo <> 'porcentaje' OR (valor IS NOT NULL AND valor > 0 AND valor <= 100)",
            name="porcentaje",
        ),
        CheckConstraint(
            "tipo <> 'monto_fijo' OR (valor IS NOT NULL AND valor > 0)",
            name="monto",
        ),
        CheckConstraint(
            "tipo <> 'precio_cantidad' OR "
            "(valor IS NOT NULL AND valor > 0 AND m IS NOT NULL AND m >= 2)",
            name="precio_cantidad",
        ),
        CheckConstraint(
            "uso <> 'cierre' OR (vigencia_horas IS NOT NULL AND vigencia_horas BETWEEN 1 AND 168)",
            name="cierre",
        ),
        Index("ix_promotions_tenant_id_is_active", "tenant_id", "is_active"),
    )

    tenant_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("tenants.id"), nullable=False
    )
    nombre: Mapped[str] = mapped_column(Text, nullable=False)
    # What the assistant may say about it.
    descripcion: Mapped[str | None] = mapped_column(Text, nullable=True)
    uso: Mapped[str] = mapped_column(Text, nullable=False, server_default="automatica")
    tipo: Mapped[str] = mapped_column(Text, nullable=False)
    # Percentage, fixed amount, or - for `precio_cantidad` - the group's price.
    valor: Mapped[Decimal | None] = mapped_column(Numeric(12, 2), nullable=True)
    # `nxm`: take `n`, pay `m`. `precio_cantidad`: `m` is the group size.
    n: Mapped[int | None] = mapped_column(Integer, nullable=True)
    m: Mapped[int | None] = mapped_column(Integer, nullable=True)
    aplica_a: Mapped[str] = mapped_column(Text, nullable=False, server_default="todo")
    product_ids: Mapped[list[uuid.UUID]] = mapped_column(
        ARRAY(PgUUID(as_uuid=True)), nullable=False, server_default="{}"
    )
    categorias: Mapped[list[str]] = mapped_column(ARRAY(Text), nullable=False, server_default="{}")
    monto_minimo: Mapped[Decimal | None] = mapped_column(Numeric(12, 2), nullable=True)
    # Only for `cierre`: hours from the moment the assistant offers it.
    vigencia_horas: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # May be offered in the last follow-up message.
    para_seguimiento: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="false")
    inicia_en: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    termina_en: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="true")
