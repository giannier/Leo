"""Per-tenant LLM configuration, including bring-your-own paid credentials.

A tenant may keep the platform's shared model or supply their own paid API
key, chosen from the panel. The key is stored Fernet-encrypted, never in
clear, and never returned by an API response.

Deliberately covers the **chat** model only. Embeddings stay on the platform
provider for every tenant, because `knowledge_chunks.embedding` has one fixed
width and vectors produced by different models are not comparable even when
the widths happen to match. Letting a tenant change embedding provider would
silently corrupt their retrieval rather than fail loudly.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, LargeBinary, Text, func
from sqlalchemy.dialects.postgresql import UUID as PgUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, enum_check

# 'platform' means "use whatever the platform is configured with" - the
# default, and the only option that costs the tenant nothing.
LLM_MODES = ("platform", "byo_openai", "byo_openai_compatible")


class TenantLLMSettings(Base):
    """One row per tenant that has customised its model. Absent means default."""

    __tablename__ = "tenant_llm_settings"
    __table_args__ = (enum_check("mode", LLM_MODES, "tenant_llm_settings_mode"),)

    tenant_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("tenants.id"), primary_key=True
    )

    mode: Mapped[str] = mapped_column(Text, nullable=False, server_default="platform")

    # Null while mode is 'platform'.
    chat_model: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Only for 'byo_openai_compatible' (Azure OpenAI, a gateway, a proxy).
    base_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Fernet ciphertext. Never logged, never serialised to an API response;
    # the panel shows `key_hint` instead.
    api_key_enc: Mapped[bytes | None] = mapped_column(LargeBinary, nullable=True)
    # Last four characters, so the panel can show which key is configured
    # without ever decrypting it.
    key_hint: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Set when a call with this credential last failed authentication, so the
    # panel can warn the customer instead of silently falling back.
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    last_verified_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )
