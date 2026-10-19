"""Conversations, messages and agent traces.

These tables are born here but shared with Features 002 and 007. Those
features may only add columns through new migrations; renaming or repurposing
what is defined here would break the agent-core that already writes to it.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    ARRAY,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    SmallInteger,
    Text,
    func,
    text,
)
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

# What arrived, or what was sent. `unsupported` is the honest answer for a
# type this version cannot read at all: the message is still stored and still
# counts as an interaction, because discarding it would lose a customer's
# words (Feature 002, C-001).
MESSAGE_TYPES = (
    "text",
    "image",
    "document",
    "audio",
    "sticker",
    "location",
    "contact",
    "unsupported",
    # What the sales engine sends besides text (Feature 004): reply buttons and
    # lists are `interactive`, a library file is `image`, `document`, `audio`
    # or `video`. The structure is in `Message.payload`.
    "video",
    "interactive",
    # Feature 007: takeover, return, close and payment decisions as items of the
    # thread (`author='system'`, the details in `payload`).
    "event",
    # Feature 011: an approved WhatsApp template (campaigns, follow-ups outside the
    # 24-hour window). The name, language and values are in `payload`.
    "template",
)

# Where an outbound message got to. Null for anything inbound - a message the
# customer sent us has no delivery state of ours to report.
DELIVERY_STATUSES = ("queued", "sent", "delivered", "read", "failed")


class Conversation(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """A thread with one customer, on the sandbox or the real channel."""

    __tablename__ = "conversations"
    __table_args__ = (
        enum_check("channel", CHANNELS, "conversations_channel"),
        enum_check("status", CONVERSATION_STATUSES, "conversations_status"),
        # The inbox always filters by tenant and channel together.
        Index("ix_conversations_tenant_id_channel", "tenant_id", "channel"),
        # The inbox orders a business's threads by their latest message (migration 0034).
        Index("ix_conversations_tenant_id_last_message_at", "tenant_id", "last_message_at"),
    )

    tenant_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("tenants.id"), nullable=False
    )
    channel: Mapped[str] = mapped_column(Text, nullable=False)
    # A real foreign key since Feature 002, which is where `contacts` was
    # born. Still nullable, and always will be: a sandbox conversation is the
    # owner talking to their own agent, and there is no contact involved.
    contact_id: Mapped[uuid.UUID | None] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("contacts.id"), nullable=True
    )
    # The panel user who opened a sandbox conversation.
    started_by: Mapped[uuid.UUID | None] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("users.id"), nullable=True
    )
    status: Mapped[str] = mapped_column(Text, nullable=False, server_default="open")
    # --- the inbox (Feature 007, migration 0034) --------------------------
    # The person who has the conversation; the agent stays quiet while set.
    takeover_user_id: Mapped[uuid.UUID | None] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("users.id"), nullable=True
    )
    takeover_started_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    # The last thing that person did here: the auto-return counts from it.
    takeover_activity_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # The inbox's order; written by `MessageRepository.append`.
    last_message_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # Customer messages nobody has opened yet.
    unread_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    # The campaign whose template opened this thread (Feature 011, C-109).
    origin_campaign_id: Mapped[uuid.UUID | None] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("campaigns.id", ondelete="SET NULL"), nullable=True
    )


class Message(Base, UUIDPrimaryKeyMixin, CreatedAtMixin):
    """A single message within a conversation. Append-only."""

    __tablename__ = "messages"
    __table_args__ = (
        enum_check("direction", MESSAGE_DIRECTIONS, "messages_direction"),
        enum_check("author", MESSAGE_AUTHORS, "messages_author"),
        enum_check("msg_type", MESSAGE_TYPES, "messages_msg_type"),
        enum_check("delivery_status", DELIVERY_STATUSES, "messages_delivery_status"),
        # Transcripts are always read in chronological order for one thread.
        Index("ix_messages_conversation_id_created_at", "conversation_id", "created_at"),
        # Inbox search reads message text through this Spanish full-text index (migration 0034).
        Index(
            "ix_messages_content_search",
            text("to_tsvector('spanish', content)"),
            postgresql_using="gin",
        ),
    )

    # `clock_timestamp()`, not `now()`. Postgres freezes `now()` at the start
    # of the transaction, so two messages written in one - a customer's
    # question and the agent's answer, a burst applied together - would carry
    # the *same* timestamp and the transcript's order would be whatever the
    # planner felt like. This is an append-only log whose order is read by the
    # agent as conversation history, so ties are not survivable.
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.clock_timestamp(),
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

    # --- the real channel (Feature 002) -----------------------------------
    # The provider's own id, both ways. Globally unique, which is what makes
    # a redelivered inbound message insert exactly once, and what lets a
    # status webhook find the outbound message it refers to.
    #
    # Null for the sandbox: nothing was sent anywhere, so no provider ever
    # named it.
    provider_message_id: Mapped[str | None] = mapped_column(Text, nullable=True, unique=True)
    msg_type: Mapped[str] = mapped_column(Text, nullable=False, server_default="text")
    delivery_status: Mapped[str | None] = mapped_column(Text, nullable=True)

    # The structure of a typed message (Feature 004, migration 0021). Outbound:
    # the buttons, the list rows or the library file. Inbound: the tapped option
    # (`interactive`) and the ad (`referral`), kept because the turn that reads
    # them runs after the debounce window, when the webhook event is long gone.
    # `content` stays the readable text either way.
    payload: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)


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
    # [{"tool": ..., "args_resumen": ..., "ok": ..., "cached": ..., "ronda": 1..N}]
    # What the trace cost, from `model_costs` when it was written (Feature 010).
    # NULL when the model has no price configured.
    cost_usd: Mapped[Decimal | None] = mapped_column(Numeric(10, 6), nullable=True)
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
    # What the catalogue post-guard did, when it did anything:
    # {"status": "corrected"|"blocked", "invented": [...], "still_invented": [...]}
    #
    # Null on an ordinary turn, which is nearly all of them, so a non-null
    # value is itself the signal that the model named a product the tenant
    # does not sell. Nullable rather than defaulted for exactly that reason -
    # "{}" everywhere would bury the handful of rows worth reading.
    catalog_guard: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)

    # Where the sales engine had the conversation on this turn (Feature 004,
    # migration 0020). All nullable: turns written before the engine existed, and
    # turns that never reached it, carry none.
    stage_key: Mapped[str | None] = mapped_column(Text, nullable=True)
    funnel_code: Mapped[str | None] = mapped_column(Text, nullable=True)
    # The tools the model was offered, not the ones it called (`tools_called`).
    tools_enabled: Mapped[list[str] | None] = mapped_column(ARRAY(Text), nullable=True)
    outbound_count: Mapped[int | None] = mapped_column(SmallInteger, nullable=True)
    model_tier: Mapped[str | None] = mapped_column(Text, nullable=True)
    # The situations that entered the turn, with their distance (017, C-10).
    situaciones: Mapped[list[dict[str, Any]] | None] = mapped_column(JSONB, nullable=True)
