"""The business's public web chat (feature 019, phase F; migration 0059)."""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import Boolean, CheckConstraint, DateTime, ForeignKey, Integer, Text, func
from sqlalchemy.dialects.postgresql import UUID as PgUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class WebChat(Base):
    """One per business: its public address, the switch, and how it looks.

    `public_key` is what the page and the widget carry; it only says which business, so it may be
    public, and it is rotated if somebody abuses it. The chat is off until the owner turns it on.
    """

    __tablename__ = "web_chats"
    __table_args__ = (
        CheckConstraint("color ~ '^#[0-9a-fA-F]{6}$'", name="web_chats_color"),
        CheckConstraint("limite_diario BETWEEN 10 AND 5000", name="web_chats_limite_diario"),
        CheckConstraint("saludo IS NULL OR char_length(saludo) <= 300", name="web_chats_saludo"),
    )

    tenant_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("tenants.id"), primary_key=True
    )
    public_key: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="false")
    saludo: Mapped[str | None] = mapped_column(Text, nullable=True)
    color: Mapped[str] = mapped_column(Text, nullable=False, server_default="#2563eb")
    # Messages the public may send to the assistant in a day, counted per business.
    limite_diario: Mapped[int] = mapped_column(Integer, nullable=False, server_default="300")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )
