"""After the sale: the returns a customer asks for and the documents of an order (019, phase G).

* `order_returns`: a return, an exchange, a warranty claim or a complaint about an order already
  made. The assistant only *registers* it (what the customer said, the photos they sent); the
  business decides, because refunding is money. `order_return_events` keeps who decided what.
* `order_documents`: the boleta, factura, guía or warranty of an order. Apart from the media
  library on purpose: the library is the business's catalogue of files, and a customer's invoice
  must never be reachable by a key.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PgUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, CreatedAtMixin, TimestampMixin, UUIDPrimaryKeyMixin, enum_check

RETURN_TYPES = ("devolucion", "cambio", "garantia", "reclamo")
RETURN_REASONS = ("defectuoso", "equivocado", "incompleto", "no_conforme", "tardio", "otro")
RETURN_STATES = ("solicitada", "aprobada", "rechazada", "resuelta", "cancelada")
# A customer may not open the same kind of request twice on one order while one is still going.
OPEN_RETURN_STATES = ("solicitada", "aprobada")
RETURN_ACTORS = ("agent", "user")

# The sequence of `tenant_sequences` that numbers a business's returns.
RETURNS_SEQUENCE = "devoluciones"

DOCUMENT_TYPES = ("boleta", "factura", "guia", "proforma", "garantia", "nota_credito", "otro")
DOCUMENT_ORIGINS = ("subido", "generado", "electronico")
DOCUMENT_STATES = ("emitido", "anulado")

MAX_DETAIL = 1000
MAX_RESOLUTION = 500


class OrderReturn(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "order_returns"
    __table_args__ = (
        enum_check("tipo", RETURN_TYPES, "tipo"),
        enum_check("motivo", RETURN_REASONS, "motivo"),
        enum_check("estado", RETURN_STATES, "estado"),
        enum_check("created_by", RETURN_ACTORS, "created_by"),
        CheckConstraint(f"char_length(detalle) <= {MAX_DETAIL}", name="detalle_length"),
        CheckConstraint(
            f"resolucion IS NULL OR char_length(resolucion) <= {MAX_RESOLUTION}",
            name="resolucion_length",
        ),
        UniqueConstraint("tenant_id", "numero", name="uq_order_returns_tenant_id_numero"),
        # The assistant may call the tool twice for one message: the second finds the first.
        Index(
            "uq_order_returns_open_per_order",
            "order_id",
            "tipo",
            unique=True,
            postgresql_where=text("estado IN ('solicitada', 'aprobada')"),
        ),
        Index("ix_order_returns_tenant_id_estado", "tenant_id", "estado", text("created_at DESC")),
        Index("ix_order_returns_order_id", "order_id"),
    )

    tenant_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("tenants.id"), nullable=False
    )
    order_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("orders.id"), nullable=False
    )
    contact_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("contacts.id"), nullable=False
    )
    conversation_id: Mapped[uuid.UUID | None] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("conversations.id", ondelete="SET NULL"), nullable=True
    )
    # The business-facing number, from `tenant_sequences`.
    numero: Mapped[int] = mapped_column(Integer, nullable=False)
    tipo: Mapped[str] = mapped_column(Text, nullable=False)
    motivo: Mapped[str] = mapped_column(Text, nullable=False)
    detalle: Mapped[str] = mapped_column(Text, nullable=False, server_default="")
    # What is being returned: `[{nombre, cantidad, producto_id?}]`. Empty: the whole order.
    items: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONB, nullable=False, server_default=text("'[]'::jsonb")
    )
    # The photos the customer sent while asking: `[{message_id}]`, read in the conversation.
    evidencia: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONB, nullable=False, server_default=text("'[]'::jsonb")
    )
    estado: Mapped[str] = mapped_column(Text, nullable=False, server_default="solicitada")
    # What the business decided and told the customer ("te devolvemos por Yape en 48 h").
    resolucion: Mapped[str | None] = mapped_column(Text, nullable=True)
    resuelta_por: Mapped[uuid.UUID | None] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    resuelta_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_by: Mapped[str] = mapped_column(Text, nullable=False)


class OrderReturnEvent(Base, UUIDPrimaryKeyMixin, CreatedAtMixin):
    """One thing that happened to a return. Append-only: refunds are money, so it is the audit."""

    __tablename__ = "order_return_events"
    __table_args__ = (Index("ix_order_return_events_return_id", "return_id", "created_at"),)

    tenant_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("tenants.id"), nullable=False
    )
    return_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("order_returns.id", ondelete="CASCADE"), nullable=False
    )
    desde: Mapped[str | None] = mapped_column(Text, nullable=True)
    hacia: Mapped[str] = mapped_column(Text, nullable=False)
    actor: Mapped[str] = mapped_column(Text, nullable=False)
    user_id: Mapped[uuid.UUID | None] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    detalle: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )


class OrderDocument(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "order_documents"
    __table_args__ = (
        enum_check("tipo", DOCUMENT_TYPES, "tipo"),
        enum_check("origen", DOCUMENT_ORIGINS, "origen"),
        enum_check("estado", DOCUMENT_STATES, "estado"),
        CheckConstraint("size_bytes >= 0", name="size_non_negative"),
        Index("ix_order_documents_order_id", "order_id", "created_at"),
        Index("ix_order_documents_tenant_id_created_at", "tenant_id", text("created_at DESC")),
    )

    tenant_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("tenants.id"), nullable=False
    )
    order_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("orders.id"), nullable=False
    )
    tipo: Mapped[str] = mapped_column(Text, nullable=False)
    titulo: Mapped[str] = mapped_column(Text, nullable=False)
    # "B001-123": the series and number the document carries, when it has them.
    serie: Mapped[str | None] = mapped_column(Text, nullable=True)
    numero: Mapped[str | None] = mapped_column(Text, nullable=True)
    storage_key: Mapped[str] = mapped_column(Text, nullable=False)
    filename: Mapped[str] = mapped_column(Text, nullable=False)
    mime_type: Mapped[str] = mapped_column(Text, nullable=False)
    size_bytes: Mapped[int] = mapped_column(Integer, nullable=False)
    origen: Mapped[str] = mapped_column(Text, nullable=False, server_default="subido")
    estado: Mapped[str] = mapped_column(Text, nullable=False, server_default="emitido")
    # What an electronic provider answered (its id, the tax authority's status, links).
    proveedor: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    # The id WhatsApp gave the uploaded file, to reuse it while it lasts.
    provider_media_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    provider_media_expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    subido_por: Mapped[uuid.UUID | None] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    # The last time the customer was sent this document.
    enviado_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
