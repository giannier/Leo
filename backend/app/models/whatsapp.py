"""WhatsApp numbers and the assisted connection workflow.

Connecting a number is not self-service: the tenant files a request and a
platform admin performs the provider-side work (Feature 001 scope).
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Index, LargeBinary, Text, func, text
from sqlalchemy.dialects.postgresql import UUID as PgUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin, UUIDPrimaryKeyMixin, enum_check

WHATSAPP_PROVIDERS = ("ycloud", "meta")
NUMBER_STATUSES = ("connected", "disconnected")
REQUEST_STATUSES = ("requested", "in_progress", "connected", "rejected")
OPEN_REQUEST_STATUSES = ("requested", "in_progress")


class WhatsAppNumber(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """A live WhatsApp business number bound to a tenant."""

    __tablename__ = "whatsapp_numbers"
    __table_args__ = (
        enum_check("provider", WHATSAPP_PROVIDERS, "whatsapp_numbers_provider"),
        enum_check("status", NUMBER_STATUSES, "whatsapp_numbers_status"),
    )

    tenant_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("tenants.id"), nullable=False
    )
    # Globally unique, not per tenant: a phone number can only be connected
    # to one business anywhere in the platform (RF-008).
    phone_e164: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    display_name: Mapped[str] = mapped_column(Text, nullable=False)
    provider: Mapped[str] = mapped_column(Text, nullable=False, server_default="ycloud")

    # Fernet-encrypted provider credentials (RF-009). Never stored in clear,
    # never logged, and never returned by an API response.
    credentials_enc: Mapped[bytes | None] = mapped_column(LargeBinary, nullable=True)

    status: Mapped[str] = mapped_column(Text, nullable=False, server_default="connected")
    # Populated by Feature 002 from provider webhooks.
    quality_rating: Mapped[str | None] = mapped_column(Text, nullable=True)
    connected_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class ConnectionRequest(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """A tenant's request to have a number connected by an admin."""

    __tablename__ = "connection_requests"
    __table_args__ = (
        enum_check("status", REQUEST_STATUSES, "connection_requests_status"),
        # At most one open request per phone number. Rejected and connected
        # requests are excluded so a customer can retry after a rejection
        # (Clarification 012).
        Index(
            "uq_connection_requests_open_phone",
            "phone_e164",
            unique=True,
            postgresql_where=text("status IN ('requested', 'in_progress')"),
        ),
        Index("ix_connection_requests_status", "status"),
    )

    tenant_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("tenants.id"), nullable=False
    )
    phone_e164: Mapped[str] = mapped_column(Text, nullable=False)
    display_name: Mapped[str] = mapped_column(Text, nullable=False)
    profile_photo_key: Mapped[str | None] = mapped_column(Text, nullable=True)
    business_description: Mapped[str | None] = mapped_column(Text, nullable=True)

    status: Mapped[str] = mapped_column(Text, nullable=False, server_default="requested")
    # Shown to the customer, so it must stay free of internal detail.
    rejection_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Internal only.
    admin_notes: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Chains a retry back to the request it replaces.
    previous_request_id: Mapped[uuid.UUID | None] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("connection_requests.id"), nullable=True
    )
    processed_by: Mapped[uuid.UUID | None] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("users.id"), nullable=True
    )
