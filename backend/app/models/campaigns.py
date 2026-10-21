"""Campaigns, templates and opt-ins (Feature 011, migration 0041).

* `message_templates`: the business's WhatsApp templates. Written with named variables
  (`{{nombre}}`), sent to Meta as positional ones (`{{1}}`); Meta approves or rejects them.
* `contact_optins`: whether a contact agreed to hear about promotions, with the evidence
  (the button they tapped, or the owner's declaration when importing). One row per contact.
* `optin_imports`: each import of a list with consent declared by the owner, for the
  weekly cap (C-001) and as the record of who declared what.
* `contact_tags`: labels on contacts, the basis of segments.
* `campaigns` / `campaign_recipients`: a broadcast and the snapshot of who gets it, one
  row per contact (never twice the same campaign to the same person, RF-004).
* `meta_rates`: what Meta charges per template message, by country and category (C-107).
"""

from __future__ import annotations

import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import (
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
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PgUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, CreatedAtMixin, TimestampMixin, UUIDPrimaryKeyMixin, enum_check

TEMPLATE_CATEGORIES = ("utility", "marketing")
# What Meta charges for: templates by category, and since 2026-10-01 free-form replies.
RATE_CATEGORIES = (*TEMPLATE_CATEGORIES, "service")
# Service messages free a month per number (C-111).
FREE_SERVICE_MESSAGES = 1000
TEMPLATE_STATUSES = ("draft", "pending", "approved", "rejected", "paused", "disabled")
# The variables a template may use (C-105), in the order the editor offers them.
# `servicio`, `fecha` and `hora` only exist for an appointment reminder (feature 019, phase D).
TEMPLATE_VARIABLES = ("nombre", "negocio", "producto", "servicio", "fecha", "hora")
OPTIN_SOURCES = ("conversation", "import", "form")
OPTIN_STATUSES = ("asked", "granted", "declined", "revoked")
CAMPAIGN_STATUSES = ("draft", "scheduled", "sending", "paused", "completed", "canceled")
RECIPIENT_STATUSES = ("pending", "sent", "delivered", "read", "replied", "failed", "skipped")
# Meta's lowest tier: new numbers reach 250 people a day (C-106).
DEFAULT_DAILY_LIMIT = 250
# The owner may import at most this many consented contacts a week (C-001).
WEEKLY_IMPORT_LIMIT = 200
# A reply or a sale within this long of the template is the campaign's (C-109).
ATTRIBUTION_HOURS = 72
MAX_TAG_LENGTH = 30


class MessageTemplate(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "message_templates"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id", "name", "language", name="uq_message_templates_tenant_id_name_language"
        ),
        enum_check("category", TEMPLATE_CATEGORIES, "message_templates_category"),
        enum_check("status", TEMPLATE_STATUSES, "message_templates_status"),
        CheckConstraint("name ~ '^[a-z0-9_]{1,60}$'", name="message_templates_name"),
        CheckConstraint("char_length(body) BETWEEN 1 AND 1024", name="message_templates_body"),
    )

    tenant_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("tenants.id"), nullable=False
    )
    # Meta's rules: lowercase, digits and underscores.
    name: Mapped[str] = mapped_column(Text, nullable=False)
    # What the owner sees in the panel.
    label: Mapped[str] = mapped_column(Text, nullable=False)
    category: Mapped[str] = mapped_column(Text, nullable=False)
    language: Mapped[str] = mapped_column(Text, nullable=False, server_default="es")
    # With named variables, e.g. "Hola {{nombre}}, ...".
    body: Mapped[str] = mapped_column(Text, nullable=False)
    footer: Mapped[str | None] = mapped_column(Text, nullable=True)
    # An image header from the media library (C-006).
    header_media_asset_id: Mapped[uuid.UUID | None] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("media_assets.id", ondelete="SET NULL"), nullable=True
    )
    # The named variables in the order they become {{1}}, {{2}}...
    variables: Mapped[list[str]] = mapped_column(JSONB, nullable=False, server_default="[]")
    status: Mapped[str] = mapped_column(Text, nullable=False, server_default="draft")
    provider_template_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    rejection_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    # The library entry it was cloned from, if any.
    library_key: Mapped[str | None] = mapped_column(Text, nullable=True)
    submitted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class ContactOptin(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "contact_optins"
    __table_args__ = (
        UniqueConstraint("tenant_id", "contact_id", name="uq_contact_optins_tenant_id_contact_id"),
        enum_check("source", OPTIN_SOURCES, "contact_optins_source"),
        enum_check("status", OPTIN_STATUSES, "contact_optins_status"),
    )

    tenant_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("tenants.id"), nullable=False
    )
    contact_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("contacts.id", ondelete="CASCADE"), nullable=False
    )
    source: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False)
    # The message with the question and the tap, or the import and its declaration.
    evidence: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, server_default="{}")
    asked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    answered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class OptinImport(Base, UUIDPrimaryKeyMixin, CreatedAtMixin):
    __tablename__ = "optin_imports"
    __table_args__ = (Index("ix_optin_imports_tenant_id_created_at", "tenant_id", "created_at"),)

    tenant_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("tenants.id"), nullable=False
    )
    user_id: Mapped[uuid.UUID | None] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    # The exact text the owner accepted.
    declaration: Mapped[str] = mapped_column(Text, nullable=False)
    filename: Mapped[str | None] = mapped_column(Text, nullable=True)
    imported: Mapped[int] = mapped_column(Integer, nullable=False)
    skipped: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")


class ContactTag(Base, CreatedAtMixin):
    __tablename__ = "contact_tags"
    __table_args__ = (
        CheckConstraint("char_length(tag) BETWEEN 1 AND 30", name="contact_tags_tag"),
        Index("ix_contact_tags_tenant_id_tag", "tenant_id", "tag"),
    )

    tenant_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("tenants.id"), primary_key=True
    )
    contact_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("contacts.id", ondelete="CASCADE"), primary_key=True
    )
    tag: Mapped[str] = mapped_column(Text, primary_key=True)


class Campaign(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "campaigns"
    __table_args__ = (
        enum_check("status", CAMPAIGN_STATUSES, "campaigns_status"),
        CheckConstraint("char_length(btrim(name)) BETWEEN 1 AND 80", name="campaigns_name"),
        Index("ix_campaigns_status", "status"),
    )

    tenant_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("tenants.id"), nullable=False
    )
    name: Mapped[str] = mapped_column(Text, nullable=False)
    template_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("message_templates.id"), nullable=False
    )
    # The declarative filter (`app.services.campaigns.segments.Segment`).
    segment: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, server_default="{}")
    # A/B (Feature 013, C-106): half of the recipients get this template instead.
    template_b_id: Mapped[uuid.UUID | None] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("message_templates.id"), nullable=True
    )
    scheduled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # Local hours of the business, within 08:00-21:00 (C-003).
    window_from: Mapped[str] = mapped_column(Text, nullable=False, server_default="09:00")
    window_to: Mapped[str] = mapped_column(Text, nullable=False, server_default="20:00")
    status: Mapped[str] = mapped_column(Text, nullable=False, server_default="draft")
    # Why it stopped: `calidad` (the number's rating dropped), `persona`, `sin_numero`.
    pause_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    estimated_cost_usd: Mapped[Decimal | None] = mapped_column(Numeric(10, 4), nullable=True)
    created_by: Mapped[uuid.UUID | None] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class CampaignRecipient(Base):
    __tablename__ = "campaign_recipients"
    __table_args__ = (
        enum_check("status", RECIPIENT_STATUSES, "campaign_recipients_status"),
        enum_check("variant", ("A", "B"), "campaign_recipients_variant"),
        Index("ix_campaign_recipients_tenant_id_sent_at", "tenant_id", "sent_at"),
        Index("ix_campaign_recipients_contact_id", "contact_id"),
        Index("ix_campaign_recipients_message_id", "message_id"),
    )

    campaign_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("campaigns.id", ondelete="CASCADE"), primary_key=True
    )
    contact_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("contacts.id", ondelete="CASCADE"), primary_key=True
    )
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("tenants.id"), nullable=False
    )
    status: Mapped[str] = mapped_column(Text, nullable=False, server_default="pending")
    # Which template of an A/B campaign this person gets (C-106 of Feature 013).
    variant: Mapped[str] = mapped_column(Text, nullable=False, server_default="A")
    skip_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    message_id: Mapped[uuid.UUID | None] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("messages.id", ondelete="SET NULL"), nullable=True
    )
    conversation_id: Mapped[uuid.UUID | None] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("conversations.id", ondelete="SET NULL"), nullable=True
    )
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    replied_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class MetaRate(Base):
    __tablename__ = "meta_rates"
    __table_args__ = (enum_check("category", RATE_CATEGORIES, "meta_rates_category"),)

    country: Mapped[str] = mapped_column(Text, primary_key=True)
    category: Mapped[str] = mapped_column(Text, primary_key=True)
    valid_from: Mapped[date] = mapped_column(Date, primary_key=True)
    rate_usd: Mapped[Decimal] = mapped_column(Numeric(8, 5), nullable=False)
