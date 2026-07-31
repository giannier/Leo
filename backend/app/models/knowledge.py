"""RAG knowledge base: uploaded sources and their embedded chunks."""

from __future__ import annotations

import uuid
from datetime import datetime

from pgvector.sqlalchemy import Vector
from sqlalchemy import DateTime, ForeignKey, Index, Integer, Text, text
from sqlalchemy.dialects.postgresql import UUID as PgUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import (
    Base,
    CreatedAtMixin,
    TimestampMixin,
    UUIDPrimaryKeyMixin,
    enum_check,
)

KNOWLEDGE_TYPES = ("faq", "policy", "pdf", "text")
KNOWLEDGE_STATUSES = ("pending", "indexing", "indexed", "failed")

# Matches the embedding width of the configured LLMProvider. Changing this
# requires re-embedding every chunk, so it is a migration, not a setting.
EMBEDDING_DIMENSIONS = 1536


class KnowledgeSource(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """A document or snippet the tenant wants the agent to know about."""

    __tablename__ = "knowledge_sources"
    __table_args__ = (
        enum_check("type", KNOWLEDGE_TYPES, "knowledge_sources_type"),
        enum_check("status", KNOWLEDGE_STATUSES, "knowledge_sources_status"),
        Index(
            "ix_knowledge_sources_tenant_id_active",
            "tenant_id",
            postgresql_where=text("deleted_at IS NULL"),
        ),
    )

    tenant_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("tenants.id"), nullable=False
    )
    type: Mapped[str] = mapped_column(Text, nullable=False)
    title: Mapped[str] = mapped_column(Text, nullable=False)
    # Inline content for faq/policy/text; `file_key` instead for pdf.
    content: Mapped[str | None] = mapped_column(Text, nullable=True)
    file_key: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Counted against the plan's max_rag_chars budget before indexing starts.
    char_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    status: Mapped[str] = mapped_column(Text, nullable=False, server_default="pending")
    error_msg: Mapped[str | None] = mapped_column(Text, nullable=True)

    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class KnowledgeChunk(Base, UUIDPrimaryKeyMixin, CreatedAtMixin):
    """One embedded slice of a knowledge source."""

    __tablename__ = "knowledge_chunks"
    __table_args__ = (
        # HNSW with cosine distance: the retrieval query ranks by
        # `embedding <=> query`, which only uses an index built for that
        # operator class.
        Index(
            "ix_knowledge_chunks_embedding",
            "embedding",
            postgresql_using="hnsw",
            postgresql_ops={"embedding": "vector_cosine_ops"},
        ),
    )

    tenant_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("tenants.id"), nullable=False, index=True
    )
    # Deleting a source must take its chunks with it; orphaned embeddings
    # would keep answering questions about deleted documents.
    source_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True),
        ForeignKey("knowledge_sources.id", ondelete="CASCADE"),
        nullable=False,
    )
    content: Mapped[str] = mapped_column(Text, nullable=False)
    embedding: Mapped[list[float] | None] = mapped_column(
        Vector(EMBEDDING_DIMENSIONS), nullable=True
    )
