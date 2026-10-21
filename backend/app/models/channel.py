"""The real WhatsApp channel: contacts, raw webhooks and delivery history.

Feature 002. Three tables, and each one exists to answer a question the
sandbox never had to ask.

`contacts` is the person on the other end. It carries `last_inbound_at`
because Meta's 24-hour session window is computed from it, and `opted_out_at`
because somebody who wrote STOP must not be answered again. Both are the
authority for a rule the business is not allowed to get wrong: the first
protects the customer's number from being blocked, the second protects the
person.

`webhook_events` is the raw delivery, stored before anything is understood
about it. A provider retries, so `provider_event_id` is unique and that
uniqueness - not a `SELECT` in front of the insert - is what makes processing
happen once. Keeping the untouched payload also means a parser that got a
field wrong can be fixed and the event replayed, rather than the message being
lost because the code that read it had a bug.

`message_status_events` is the delivery history of one outbound message.
`messages.delivery_status` holds where it got to; this holds how it got there,
which is the only thing that answers "the customer says they never received
it" three days later.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Index, Integer, Text, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PgUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import (
    Base,
    CreatedAtMixin,
    TimestampMixin,
    UUIDPrimaryKeyMixin,
    enum_check,
)

# How long after a customer's last message the business may still answer
# without a template. Meta's rule, not ours (HU-03).
SESSION_WINDOW_HOURS = 24

WEBHOOK_PROVIDERS = ("ycloud",)
WEBHOOK_STATUSES = ("received", "processed", "failed", "dead_letter")

# Attempts before an event stops being retried and starts waiting for a human
# (RF-001). Visible in the admin panel, never silently discarded.
MAX_WEBHOOK_ATTEMPTS = 5


class Contact(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """One person who has written to a tenant's number."""

    __tablename__ = "contacts"
    __table_args__ = (
        # One row per person per business. The same phone writing to two
        # different businesses is two contacts, and neither may see the other.
        Index("uq_contacts_tenant_id_phone_e164", "tenant_id", "phone_e164", unique=True),
        # Meta's business-scoped user id (BSUID) identifies the person when no
        # phone number is shared. Partial, so contacts without one never collide.
        Index(
            "uq_contacts_tenant_id_external_user_id",
            "tenant_id",
            "external_user_id",
            unique=True,
            postgresql_where=text("external_user_id IS NOT NULL"),
        ),
        # At least one of the two says who this is.
        CheckConstraint("phone_e164 IS NOT NULL OR external_user_id IS NOT NULL", name="identity"),
    )

    tenant_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("tenants.id"), nullable=False
    )
    # Absent when the person writes with a BSUID only (Feature 004, 0020).
    phone_e164: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Meta's business-scoped user id (BSUID); also `sandbox:<user_id>` for the
    # simulator's contact (C-19). Absent for contacts that arrived by phone.
    external_user_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    # What WhatsApp reports as the person's profile name. Absent when they
    # have none, and never trusted as an identity - only as a display label.
    profile_name: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Another contact of the same tenant that is probably the same person: a
    # message brought this one's BSUID with the other's phone. They are not
    # merged; the operator sees the mark (Milestone B, decision 12).
    posible_duplicado_de: Mapped[uuid.UUID | None] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("contacts.id", ondelete="SET NULL"), nullable=True
    )

    # Where the customer came from ("Instagram", "Chat web"...): the link or what they said. A lead
    # takes it (feature 019, phase F).
    origen: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Set when this person asked not to be contacted. Auditable by being a
    # timestamp rather than a boolean: "when did they opt out" is a question
    # that gets asked, and a flag cannot answer it.
    opted_out_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    # The 24-hour window is `last_inbound_at + 24h`, computed on read. A
    # stored `window_open` flag would need a job to expire it, and would be
    # wrong for exactly as long as that job was late.
    last_inbound_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    # What the customer told the assistant for an order: `{"dni": "...",
    # "correo": "..."}` (`datos_requeridos`). Validated per field when saved and
    # masked in traces and logs (C-20); the database only guarantees it is JSON.
    custom_data: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, server_default="{}")


class WebhookEvent(Base, UUIDPrimaryKeyMixin, CreatedAtMixin):
    """One delivery from a provider, exactly as it arrived.

    Deliberately **not** tenant-scoped. The tenant is a conclusion drawn from
    the payload - which number it was addressed to - and drawing it requires
    parsing, which is the step this row exists to make retryable. A malformed
    event that cannot be attributed to anyone still has to be stored, or the
    parser bug that caused it destroys the message.
    """

    __tablename__ = "webhook_events"
    __table_args__ = (
        enum_check("provider", WEBHOOK_PROVIDERS, "webhook_events_provider"),
        enum_check("status", WEBHOOK_STATUSES, "webhook_events_status"),
        # The dead-letter queue in the admin panel reads by status, newest
        # first.
        Index("ix_webhook_events_status_created_at", "status", "created_at"),
    )

    provider: Mapped[str] = mapped_column(Text, nullable=False, server_default="ycloud")
    # Idempotency, enforced by the database. A provider that retries a
    # delivery hits this constraint and the second insert is discarded, which
    # is cheaper and more reliable than checking first and racing.
    provider_event_id: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)

    status: Mapped[str] = mapped_column(Text, nullable=False, server_default="received")
    error_msg: Mapped[str | None] = mapped_column(Text, nullable=True)
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    processed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class MessageStatusEvent(Base, UUIDPrimaryKeyMixin, CreatedAtMixin):
    """One step in the delivery of an outbound message.

    Append-only, and ordered by the provider's timestamp rather than ours:
    `delivered` and `read` webhooks routinely arrive out of order, and the
    history is worthless if it records the order we happened to receive them.
    """

    __tablename__ = "message_status_events"
    __table_args__ = (
        Index("ix_message_status_events_message_id_provider_ts", "message_id", "provider_ts"),
    )

    message_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("messages.id"), nullable=False
    )
    status: Mapped[str] = mapped_column(Text, nullable=False)
    provider_ts: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
