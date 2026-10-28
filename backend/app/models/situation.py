"""A business's own rules, as cards: situations (Feature 017, TASK-462).

A situation says **when** it applies (`cuando_aplica`, matched against the
customer's message by embedding similarity) and **what** the assistant does
(`que_hace`), with optional files, a hand-over, an example answer, the stages it
applies in and an "always" flag. It is the most specific layer: it wins over the
business profile, the rubro and the process template (C-03).

Only `activa` situations reach the real channel. A `borrador` is tried in the
simulator first (RF-009). `origen` says where the card came from: written by
hand, copied from a rubro suggestion, migrated from the old free-text
`restrictions` (RF-011), or, in phase 3, learned.
"""

from __future__ import annotations

import uuid

from pgvector.sqlalchemy import Vector
from sqlalchemy import Boolean, CheckConstraint, ForeignKey, Integer, Text
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.dialects.postgresql import UUID as PgUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin, UUIDPrimaryKeyMixin, enum_check
from app.models.knowledge import EMBEDDING_DIMENSIONS

SITUATION_STATES = ("borrador", "activa", "inactiva")
SITUATION_ORIGINS = ("manual", "sugerida_rubro", "migrada", "aprendida")


class Situation(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "situations"
    __table_args__ = (
        enum_check("estado", SITUATION_STATES, "estado"),
        enum_check("origen", SITUATION_ORIGINS, "origen"),
        CheckConstraint("char_length(titulo) BETWEEN 1 AND 80", name="titulo_length"),
        CheckConstraint("char_length(cuando_aplica) BETWEEN 1 AND 300", name="cuando_length"),
        # 4000 only for migrated free text; the API takes up to 1000.
        CheckConstraint("char_length(que_hace) BETWEEN 1 AND 4000", name="que_hace_length"),
        CheckConstraint("char_length(ejemplo_respuesta) <= 600", name="ejemplo_length"),
    )

    tenant_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("tenants.id"), nullable=False, index=True
    )
    titulo: Mapped[str] = mapped_column(Text, nullable=False)
    cuando_aplica: Mapped[str] = mapped_column(Text, nullable=False)
    que_hace: Mapped[str] = mapped_column(Text, nullable=False)
    media_asset_ids: Mapped[list[uuid.UUID]] = mapped_column(
        ARRAY(PgUUID(as_uuid=True)), nullable=False, server_default="{}"
    )
    derivar: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="false")
    ejemplo_respuesta: Mapped[str | None] = mapped_column(Text, nullable=True)
    # None: every stage.
    etapas: Mapped[list[str] | None] = mapped_column(ARRAY(Text), nullable=True)
    siempre: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="false")
    # The item of the rubro template it came from, and the version (feature 020, migration 0072).
    # Where it started; the business's later edits do not change them.
    plantilla_clave: Mapped[str | None] = mapped_column(Text, nullable=True)
    plantilla_version: Mapped[int | None] = mapped_column(Integer, nullable=True)
    estado: Mapped[str] = mapped_column(Text, nullable=False, server_default="borrador")
    origen: Mapped[str] = mapped_column(Text, nullable=False, server_default="manual")
    # Of `cuando_aplica`, computed by the worker after a save; None until then.
    embedding: Mapped[list[float] | None] = mapped_column(
        Vector(EMBEDDING_DIMENSIONS), nullable=True
    )
    embedding_model: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_by: Mapped[uuid.UUID | None] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
