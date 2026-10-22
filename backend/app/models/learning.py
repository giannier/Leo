"""The agent learns and a person decides (feature 017, phases 2 to 4; 019, phase H).

Nothing the assistant learns is applied by itself: it becomes a **suggestion** (a situation, a real
example, a fact for the knowledge, a way of writing) that waits in the reviewer's inbox. What a
person approves goes into force at once and leaves a line in the **history** with what it was
before, so it can be undone. Learning costs model calls, so it is off until the owner turns it on
and has a weekly ceiling (`learning_usage`).
"""

from __future__ import annotations

import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    Boolean,
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
    text,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.dialects.postgresql import UUID as PgUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, CreatedAtMixin, TimestampMixin, UUIDPrimaryKeyMixin, enum_check

SUGGESTION_TYPES = ("situacion", "ejemplo", "dato", "estilo")
SUGGESTION_SOURCES = (
    "toma_de_control",
    "venta_confirmada",
    "venta_perdida",
    "pregunta_sin_respuesta",
    "importacion",
)
SUGGESTION_STATES = ("pendiente", "aprobada", "rechazada")
HISTORY_ENTITIES = ("situacion", "interruptor", "perfil", "faq", "ejemplo", "estilo")
HISTORY_ACTIONS = ("crear", "editar", "estado", "borrar", "deshacer")
QUESTION_STATES = ("abierta", "sugerida", "descartada")


class LearningSettings(Base):
    __tablename__ = "learning_settings"
    __table_args__ = (CheckConstraint("tope_semanal BETWEEN 0 AND 500", name="tope_range"),)

    tenant_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("tenants.id"), primary_key=True
    )
    # Off until the owner turns it on: every analysis is a model call.
    activo: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="false")
    # Model calls the learning may make in a week (the sources and the extractors count).
    tope_semanal: Mapped[int] = mapped_column(Integer, nullable=False, server_default="20")
    # Whether the text of this business's approved situations may be offered to the platform
    # operator as a candidate for the rubro (never a chat, never a customer's data).
    compartir_con_rubro: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default="false"
    )
    compartir_por: Mapped[uuid.UUID | None] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    compartir_en: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )


class LearningUsage(Base, UUIDPrimaryKeyMixin):
    """What the learning has spent in a week (the Monday that starts it)."""

    __tablename__ = "learning_usage"
    __table_args__ = (
        UniqueConstraint("tenant_id", "semana", name="uq_learning_usage_tenant_semana"),
    )

    tenant_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("tenants.id"), nullable=False
    )
    semana: Mapped[date] = mapped_column(Date, nullable=False)
    llamadas: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    minutos_audio: Mapped[Decimal] = mapped_column(
        Numeric(8, 2), nullable=False, server_default="0"
    )


class Suggestion(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "suggestions"
    __table_args__ = (
        enum_check("tipo", SUGGESTION_TYPES, "tipo"),
        enum_check("fuente", SUGGESTION_SOURCES, "fuente"),
        enum_check("estado", SUGGESTION_STATES, "estado"),
        # One per proposal, rejected ones included: «no se vuelve a proponer igual».
        UniqueConstraint("tenant_id", "huella", name="uq_suggestions_tenant_id_huella"),
        Index("ix_suggestions_tenant_id_estado", "tenant_id", "estado", text("created_at DESC")),
    )

    tenant_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("tenants.id"), nullable=False
    )
    tipo: Mapped[str] = mapped_column(Text, nullable=False)
    fuente: Mapped[str] = mapped_column(Text, nullable=False)
    estado: Mapped[str] = mapped_column(Text, nullable=False, server_default="pendiente")
    # One line for the list.
    resumen: Mapped[str] = mapped_column(Text, nullable=False)
    # The card as proposed; its fields depend on `tipo`.
    propuesta: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    # The conversations that gave rise to it, for the reviewer to read.
    conversaciones: Mapped[list[uuid.UUID]] = mapped_column(
        ARRAY(PgUUID(as_uuid=True)), nullable=False, server_default="{}"
    )
    # With what it clashes: `[{tipo, id, titulo, motivo}]`.
    choques: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONB, nullable=False, server_default=text("'[]'::jsonb")
    )
    huella: Mapped[str] = mapped_column(Text, nullable=False)
    decidida_por: Mapped[uuid.UUID | None] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    decidida_en: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    nota: Mapped[str | None] = mapped_column(Text, nullable=True)
    # What approving it created, to find it again: `{situation_id}`, `{faq_id}`, `{example_id}`.
    aplicada: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )


class ConfigHistory(Base, UUIDPrimaryKeyMixin, CreatedAtMixin):
    """A change to what the assistant knows or how it behaves, with what it was before."""

    __tablename__ = "config_history"
    __table_args__ = (
        enum_check("entidad", HISTORY_ENTITIES, "entidad"),
        enum_check("accion", HISTORY_ACTIONS, "accion"),
        Index("ix_config_history_tenant_id_created_at", "tenant_id", text("created_at DESC")),
    )

    tenant_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("tenants.id"), nullable=False
    )
    entidad: Mapped[str] = mapped_column(Text, nullable=False)
    entidad_id: Mapped[uuid.UUID | None] = mapped_column(PgUUID(as_uuid=True), nullable=True)
    accion: Mapped[str] = mapped_column(Text, nullable=False)
    resumen: Mapped[str] = mapped_column(Text, nullable=False)
    antes: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    despues: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    actor: Mapped[uuid.UUID | None] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    sugerencia_id: Mapped[uuid.UUID | None] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("suggestions.id", ondelete="SET NULL"), nullable=True
    )
    # The entry that undid this one, so it is not undone twice.
    deshecho_por: Mapped[uuid.UUID | None] = mapped_column(
        PgUUID(as_uuid=True),
        ForeignKey("config_history.id", ondelete="SET NULL"),
        nullable=True,
    )


class UnansweredQuestion(Base, UUIDPrimaryKeyMixin):
    """A question the assistant could not answer from the knowledge, counted (no model call).

    The raw text is the customer's own, so it is cut short and kept only as long as it is useful:
    a question asked twice becomes a suggestion to add the fact; one asked once is forgotten.
    """

    __tablename__ = "unanswered_questions"
    __table_args__ = (
        enum_check("estado", QUESTION_STATES, "estado"),
        UniqueConstraint("tenant_id", "huella", name="uq_unanswered_questions_tenant_id_huella"),
        CheckConstraint("char_length(pregunta) <= 200", name="pregunta_length"),
    )

    tenant_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("tenants.id"), nullable=False
    )
    pregunta: Mapped[str] = mapped_column(Text, nullable=False)
    huella: Mapped[str] = mapped_column(Text, nullable=False)
    veces: Mapped[int] = mapped_column(Integer, nullable=False, server_default="1")
    conversaciones: Mapped[list[uuid.UUID]] = mapped_column(
        ARRAY(PgUUID(as_uuid=True)), nullable=False, server_default="{}"
    )
    estado: Mapped[str] = mapped_column(Text, nullable=False, server_default="abierta")
    primera_en: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    ultima_en: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
