"""Orders, their items, payment proofs and per-tenant sequences (Feature 004, 0019).

An order is what the assistant builds with the customer and a person confirms.
Money is always `Decimal`, never `float` (Constitution). Item names and unit
prices are copied when the item is added, so changing the catalogue later never
rewrites what a customer agreed to.

None of these tables has a database-level guarantee that the order, the
conversation and the contact belong to the same tenant (there are no composite
foreign keys in this schema). That guarantee lives in the services, which only
ever read the parent through a tenant-scoped repository.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    ARRAY,
    CHAR,
    Boolean,
    CheckConstraint,
    Date,
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

ORDER_STATES = ("borrador", "por_confirmar", "confirmado", "en_curso", "completado", "descartado")
PAYMENT_MODES = ("previo", "contra_entrega")
DELIVERY_TYPES = ("delivery", "recojo")
PROOF_STATES = ("por_verificar", "aprobado", "rechazado")
# What a proof can be flagged for. Observations are never an automatic rejection
# (C-08): they tell the operator where to look.
PROOF_OBSERVATIONS = ("monto_distinto", "codigo_repetido", "baja_confianza", "sin_datos")

# The row of `tenant_sequences` that numbers a tenant's orders.
ORDERS_SEQUENCE = "orders"


class TenantSequence(Base):
    """A counter per tenant and name, so each business numbers its orders from 1.

    Incremented with one atomic `INSERT ... ON CONFLICT DO UPDATE ... RETURNING`
    (see `TenantSequenceRepository.next_value`), which serialises concurrent
    callers on the row instead of racing a read-then-write.
    """

    __tablename__ = "tenant_sequences"

    tenant_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("tenants.id"), primary_key=True
    )
    name: Mapped[str] = mapped_column(Text, primary_key=True)
    value: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")


class Order(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """One purchase being assembled, awaiting confirmation or already confirmed."""

    __tablename__ = "orders"
    __table_args__ = (
        enum_check("estado", ORDER_STATES, "estado"),
        enum_check("modalidad_pago", PAYMENT_MODES, "modalidad_pago"),
        # NULL passes: an order that needs no delivery has no delivery type.
        enum_check("tipo_entrega", DELIVERY_TYPES, "tipo_entrega"),
        # Not in data-model.md: no total can be negative.
        CheckConstraint(
            "subtotal >= 0 AND descuento >= 0 AND envio >= 0 AND total >= 0",
            name="amounts_non_negative",
        ),
        UniqueConstraint("tenant_id", "numero", name="uq_orders_tenant_id_numero"),
        Index(
            "ix_orders_tenant_id_is_sandbox_estado_created_at",
            "tenant_id",
            "is_sandbox",
            "estado",
            text("created_at DESC"),
        ),
        Index("ix_orders_conversation_id_created_at", "conversation_id", text("created_at DESC")),
    )

    tenant_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("tenants.id"), nullable=False
    )
    conversation_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("conversations.id"), nullable=False
    )
    contact_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("contacts.id"), nullable=False
    )
    # The business-facing number, from `tenant_sequences`; unique per tenant.
    numero: Mapped[int] = mapped_column(Integer, nullable=False)
    estado: Mapped[str] = mapped_column(Text, nullable=False, server_default="borrador")
    modalidad_pago: Mapped[str] = mapped_column(Text, nullable=False, server_default="previo")
    moneda: Mapped[str] = mapped_column(CHAR(3), nullable=False)
    subtotal: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False, server_default="0")
    descuento: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False, server_default="0")
    envio: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False, server_default="0")
    total: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False, server_default="0")
    promotion_id: Mapped[uuid.UUID | None] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("promotions.id", ondelete="SET NULL"), nullable=True
    )
    # Name, type and value when it was applied: the promotion may change later.
    promotion_snapshot: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    tipo_entrega: Mapped[str | None] = mapped_column(Text, nullable=True)
    zona_envio: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Copy of the customer's data when the order moves to `por_confirmar`.
    datos_cliente: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    motivo_rechazo: Mapped[str | None] = mapped_column(Text, nullable=True)
    # True after a payment proof was rejected; any change of items or customer
    # data turns it off, which is what lets the customer leave VERIFICACION (C-21).
    rechazo_vigente: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="false")
    # Written by the operator on confirming; goes into the notice to the customer.
    siguiente_paso: Mapped[str | None] = mapped_column(Text, nullable=True)
    is_sandbox: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="false")
    confirmed_by: Mapped[uuid.UUID | None] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_activity_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class OrderItem(Base, UUIDPrimaryKeyMixin, CreatedAtMixin):
    """One line of an order, with the name and price as they were when added."""

    __tablename__ = "order_items"
    __table_args__ = (
        CheckConstraint("cantidad BETWEEN 1 AND 999", name="cantidad_range"),
        # Not in data-model.md: a price or a subtotal cannot be negative.
        CheckConstraint("precio_unitario >= 0 AND subtotal >= 0", name="amounts_non_negative"),
        Index("ix_order_items_order_id", "order_id"),
    )

    tenant_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("tenants.id"), nullable=False
    )
    # The one cascade of this migration: an item has no life without its order.
    order_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("orders.id", ondelete="CASCADE"), nullable=False
    )
    # SET NULL: removing a product must not remove what was sold.
    product_id: Mapped[uuid.UUID | None] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("products.id", ondelete="SET NULL"), nullable=True
    )
    nombre: Mapped[str] = mapped_column(Text, nullable=False)
    precio_unitario: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    cantidad: Mapped[int] = mapped_column(Integer, nullable=False)
    bundle_size: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # [{"nombre": "SIAF", "product_id": null}]: catalogue products or free names.
    bundle_selections: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONB, nullable=False, server_default="[]"
    )
    subtotal: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)


class PaymentProof(Base, UUIDPrimaryKeyMixin):
    """A payment receipt the customer sent, as the vision step read it.

    The system never confirms a payment: it records what the image says and
    flags what looks off, and a person decides (C-08).
    """

    __tablename__ = "payment_proofs"
    __table_args__ = (
        enum_check("estado", PROOF_STATES, "estado"),
        # Not in data-model.md: numeric(3,2) alone would accept up to 9.99.
        CheckConstraint("confianza BETWEEN 0 AND 1", name="confianza_range"),
        Index("ix_payment_proofs_order_id_created_at", "order_id", text("created_at DESC")),
        Index(
            "ix_payment_proofs_tenant_id_codigo_operacion",
            "tenant_id",
            "codigo_operacion",
            postgresql_where=text("codigo_operacion IS NOT NULL"),
        ),
    )

    tenant_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("tenants.id"), nullable=False
    )
    order_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("orders.id"), nullable=False
    )
    conversation_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("conversations.id"), nullable=False
    )
    message_id: Mapped[uuid.UUID | None] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("messages.id", ondelete="SET NULL"), nullable=True
    )
    # Private object storage; served only through a short-lived signed URL.
    storage_key: Mapped[str] = mapped_column(Text, nullable=False)
    estado: Mapped[str] = mapped_column(Text, nullable=False, server_default="por_verificar")
    monto: Mapped[Decimal | None] = mapped_column(Numeric(12, 2), nullable=True)
    moneda: Mapped[str | None] = mapped_column(CHAR(3), nullable=True)
    fecha_operacion: Mapped[date | None] = mapped_column(Date, nullable=True)
    codigo_operacion: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Yape, Plin, BCP, Interbank, BBVA...
    entidad: Mapped[str | None] = mapped_column(Text, nullable=True)
    destinatario: Mapped[str | None] = mapped_column(Text, nullable=True)
    confianza: Mapped[Decimal | None] = mapped_column(Numeric(3, 2), nullable=True)
    observaciones: Mapped[list[str]] = mapped_column(
        ARRAY(Text), nullable=False, server_default="{}"
    )
    # The raw vision output, without the image.
    extraccion: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    motivo_rechazo: Mapped[str | None] = mapped_column(Text, nullable=True)
    reviewed_by: Mapped[uuid.UUID | None] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # `clock_timestamp()`, not `now()`: "the latest proof" decides whether a
    # payment is rejected, and two proofs written in one transaction would tie
    # under `now()` (same reasoning as `messages` and the stage events).
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.clock_timestamp()
    )
