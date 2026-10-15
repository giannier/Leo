"""The inbox's own tables (Feature 007, TASK-701, migration 0034).

* `conversation_notes`: what the team writes about a conversation for itself. Never
  sent to the customer, never read by the agent.
* `notification_settings`: one row per business: how long an unattended handover
  waits before an email, and how long an idle takeover lasts before the agent gets
  the conversation back (NULL: never).
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Index, Integer, Text, func
from sqlalchemy.dialects.postgresql import UUID as PgUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, CreatedAtMixin, UUIDPrimaryKeyMixin

DEFAULT_UNATTENDED_MINUTES = 10
DEFAULT_AUTO_RETURN_MINUTES = 30


class ConversationNote(Base, UUIDPrimaryKeyMixin, CreatedAtMixin):
    __tablename__ = "conversation_notes"
    __table_args__ = (
        CheckConstraint("length(content) BETWEEN 1 AND 2000", name="content"),
        Index("ix_conversation_notes_tenant_id_conversation_id", "tenant_id", "conversation_id"),
    )

    tenant_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("tenants.id"), nullable=False
    )
    conversation_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("conversations.id"), nullable=False
    )
    author_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("users.id"), nullable=False
    )
    content: Mapped[str] = mapped_column(Text, nullable=False)


class NotificationSettings(Base):
    __tablename__ = "notification_settings"
    __table_args__ = (
        CheckConstraint(
            "unattended_queue_minutes BETWEEN 1 AND 1440", name="unattended_queue_minutes"
        ),
        CheckConstraint(
            "auto_return_minutes IS NULL OR auto_return_minutes BETWEEN 5 AND 1440",
            name="auto_return_minutes",
        ),
    )

    tenant_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("tenants.id"), primary_key=True
    )
    unattended_queue_minutes: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=str(DEFAULT_UNATTENDED_MINUTES)
    )
    # No server default here, on purpose: with one, SQLAlchemy leaves out an explicit
    # None on insert and the database writes 30 instead of "never". Whoever creates
    # the row says which (the API always does).
    auto_return_minutes: Mapped[int | None] = mapped_column(Integer, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
