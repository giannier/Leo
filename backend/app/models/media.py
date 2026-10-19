"""The tenant's media library (Feature 004, data-model 0017).

Images, documents, videos and audio the assistant may send during a sale. Each
asset has a `clave` (a stable handle the tenant and the prompt use) and a
`cuando_usar` (the instruction that tells the model when to send it). The file
itself lives in object storage under `storage_key`.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import UUID as PgUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin, UUIDPrimaryKeyMixin, enum_check

MEDIA_TYPES = ("imagen", "documento", "video", "audio")

# `clave` is lowercase snake_case, so it can be quoted in a prompt and typed in
# a URL without escaping. The same pattern is enforced by the database.
CLAVE_PATTERN = r"^[a-z0-9_]{2,40}$"

# Keys with a meaning of their own: the INICIO instruction asks the model to
# send `saludo` when it exists, and `enviar_metodos_pago` attaches `metodos_pago`
# when no payment method has its own QR.
SPECIAL_CLAVES = ("saludo", "metodos_pago")


class MediaAsset(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """One file the assistant can send, with the rule for when to send it."""

    __tablename__ = "media_assets"
    __table_args__ = (
        CheckConstraint(f"clave ~ '{CLAVE_PATTERN}'", name="clave_format"),
        enum_check("tipo", MEDIA_TYPES, "tipo"),
        CheckConstraint("char_length(cuando_usar) BETWEEN 5 AND 300", name="cuando_usar_length"),
        CheckConstraint("char_length(caption) <= 1024", name="caption_length"),
        # Not in data-model.md: a file cannot have a negative size.
        CheckConstraint("size_bytes >= 0", name="size_non_negative"),
        # The same key twice in one business would make `enviar_recurso` ambiguous.
        # A different business may reuse it, so the constraint includes the tenant.
        UniqueConstraint("tenant_id", "clave", name="uq_media_assets_tenant_id_clave"),
        Index(
            "ix_media_assets_product",
            "tenant_id",
            "product_id",
            postgresql_where=text("product_id IS NOT NULL"),
        ),
    )

    tenant_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("tenants.id"), nullable=False
    )
    clave: Mapped[str] = mapped_column(Text, nullable=False)
    tipo: Mapped[str] = mapped_column(Text, nullable=False)
    titulo: Mapped[str] = mapped_column(Text, nullable=False)
    # Tells the model when to send it ("cuando pregunten por la talla").
    cuando_usar: Mapped[str] = mapped_column(Text, nullable=False)
    caption: Mapped[str | None] = mapped_column(Text, nullable=True)
    storage_key: Mapped[str] = mapped_column(Text, nullable=False)
    filename: Mapped[str] = mapped_column(Text, nullable=False)
    mime_type: Mapped[str] = mapped_column(Text, nullable=False)
    size_bytes: Mapped[int] = mapped_column(Integer, nullable=False)
    # Deleting the product or the promotion keeps the asset, just unlinked.
    product_id: Mapped[uuid.UUID | None] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("products.id", ondelete="SET NULL"), nullable=True
    )
    promotion_id: Mapped[uuid.UUID | None] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("promotions.id", ondelete="SET NULL"), nullable=True
    )
    # The id WhatsApp gave the uploaded file, so it is uploaded once and reused
    # until it expires. NULL means "upload before sending".
    provider_media_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    provider_media_expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="true")
    # The order of a product's photos; the first is the main one (Feature 018).
    orden: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
