"""What the business thinks of a simulator reply (Feature 018, TASK-1804, migration 0038).

One row per rated reply: 1 for "respondió bien", -1 for "no", with the reason. Only replies in
simulator conversations are rated; the rows are what the learning suggestions of Feature 017
(Phase 3) will start from. Nothing here is read by the agent.

"Reiniciar chat" deletes the simulator's messages; the rating stays, with a copy of what the
customer asked (`pregunta`) and what the assistant answered (`respuesta`), and its links to the
message and the conversation become NULL (migration 0039).
"""

from __future__ import annotations

import uuid

from sqlalchemy import CheckConstraint, ForeignKey, Index, SmallInteger, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID as PgUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin, UUIDPrimaryKeyMixin

MOTIVO_MAX = 500


class SandboxFeedback(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "sandbox_feedback"
    __table_args__ = (
        CheckConstraint("rating IN (-1, 1)", name="sandbox_feedback_rating"),
        CheckConstraint(
            f"char_length(motivo) <= {MOTIVO_MAX}", name="sandbox_feedback_motivo_length"
        ),
        UniqueConstraint("message_id", name="uq_sandbox_feedback_message_id"),
        Index("ix_sandbox_feedback_tenant_created", "tenant_id", "created_at"),
    )

    tenant_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("tenants.id"), nullable=False
    )
    conversation_id: Mapped[uuid.UUID | None] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("conversations.id", ondelete="SET NULL"), nullable=True
    )
    message_id: Mapped[uuid.UUID | None] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("messages.id", ondelete="SET NULL"), nullable=True
    )
    rating: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    motivo: Mapped[str | None] = mapped_column(Text, nullable=True)
    pregunta: Mapped[str | None] = mapped_column(Text, nullable=True)
    respuesta: Mapped[str] = mapped_column(Text, nullable=False, server_default="")
    created_by: Mapped[uuid.UUID | None] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("users.id"), nullable=True
    )
