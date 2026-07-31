"""Conversations, messages and agent traces.

These tables are born here but shared with Features 002 and 007. Those
features may only add columns through new migrations; renaming or repurposing
what is defined here would break the agent-core that already writes to it.
"""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import ForeignKey, Index, Integer, Text
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

CHANNELS = ("sandbox", "whatsapp")
CONVERSATION_STATUSES = ("open", "closed")
MESSAGE_DIRECTIONS = ("inbound", "outbound")
MESSAGE_AUTHORS = ("customer", "agent", "human_operator", "system")


class Conversation(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """A thread with one customer, on the sandbox or the real channel."""

    __tablename__ = "conversations"
    __table_args__ = (
        enum_check("channel", CHANNELS, "conversations_channel"),
        enum_check("status", CONVERSATION_STATUSES, "conversations_status"),
        # The inbox always filters by tenant and channel together.
        Index("ix_conversations_tenant_id_channel", "tenant_id", "channel"),
    )

    tenant_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("tenants.id"), nullable=False
    )
    channel: Mapped[str] = mapped_column(Text, nullable=False)
    # Deliberately not a foreign key yet: `contacts` arrives in Feature 007,
    # and sandbox conversations have no contact at all.
    contact_id: Mapped[uuid.UUID | None] = mapped_column(PgUUID(as_uuid=True), nullable=True)
    # The panel user who opened a sandbox conversation.
    started_by: Mapped[uuid.UUID | None] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("users.id"), nullable=True
    )
    status: Mapped[str] = mapped_column(Text, nullable=False, server_default="open")


class Message(Base, UUIDPrimaryKeyMixin, CreatedAtMixin):
    """A single message within a conversation. Append-only."""

    __tablename__ = "messages"
    __table_args__ = (
        enum_check("direction", MESSAGE_DIRECTIONS, "messages_direction"),
        enum_check("author", MESSAGE_AUTHORS, "messages_author"),
        # Transcripts are always read in chronological order for one thread.
        Index("ix_messages_conversation_id_created_at", "conversation_id", "created_at"),
    )

    tenant_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("tenants.id"), nullable=False
    )
    conversation_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("conversations.id"), nullable=False
    )
    direction: Mapped[str] = mapped_column(Text, nullable=False)
    author: Mapped[str] = mapped_column(Text, nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)


class AgentTrace(Base, UUIDPrimaryKeyMixin, CreatedAtMixin):
    """What the agent did to produce one outbound message.

    Written for every generated reply so that a wrong answer can be explained
    after the fact: which model, which tools, which retrieved sources.
    """

    __tablename__ = "agent_traces"
    __table_args__ = (Index("ix_agent_traces_tenant_id_created_at", "tenant_id", "created_at"),)

    tenant_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("tenants.id"), nullable=False
    )
    message_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("messages.id"), nullable=False
    )
    model: Mapped[str] = mapped_column(Text, nullable=False)
    # [{"tool": ..., "args_resumen": ..., "ok": ...}]
    tools_called: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONB, nullable=False, server_default="[]"
    )
    # [{"source_id": ..., "chunk_id": ..., "score": ...}]
    rag_sources: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONB, nullable=False, server_default="[]"
    )
    input_tokens: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    output_tokens: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    latency_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
