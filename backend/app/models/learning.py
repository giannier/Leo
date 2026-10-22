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

from pgvector.sqlalchemy import Vector
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
from app.models.knowledge import EMBEDDING_DIMENSIONS

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
IMPORT_STATES = ("procesando", "listo", "fallido")
EXAMPLE_ORIGINS = ("importacion", "venta_confirmada")
EXAMPLE_STATES = ("activo", "inactivo")


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


class ChatImport(Base, UUIDPrimaryKeyMixin):
    """A WhatsApp chat a business exported and handed over so the assistant learns from it.

    **Only the result is kept.** The original (the text, the audios, the names in it) lives for the
    time of the processing in a temporary store and is deleted when it ends, also if it fails; what
    stays here is the authorisation of the business, the counts and what came out as suggestions.
    """

    __tablename__ = "chat_imports"
    __table_args__ = (enum_check("estado", IMPORT_STATES, "estado"),)

    tenant_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("tenants.id"), nullable=False
    )
    estado: Mapped[str] = mapped_column(Text, nullable=False, server_default="procesando")
    # Who authorised the use of these chats, and when (Ley 29733).
    autorizado_por: Mapped[uuid.UUID | None] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    autorizado_en: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    mensajes: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    conversaciones: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    audios: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    audios_transcritos: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    minutos_audio: Mapped[Decimal] = mapped_column(
        Numeric(8, 2), nullable=False, server_default="0"
    )
    sugerencias: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    # Why it failed, in words that carry nothing of the chat.
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    creada_en: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    terminada_en: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class ExampleConversation(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """A real conversation, anonymised, the assistant consults when a similar case comes up."""

    __tablename__ = "example_conversations"
    __table_args__ = (
        enum_check("origen", EXAMPLE_ORIGINS, "origen"),
        enum_check("estado", EXAMPLE_STATES, "estado"),
        CheckConstraint("char_length(titulo) BETWEEN 1 AND 80", name="titulo_length"),
        CheckConstraint("char_length(tema) BETWEEN 1 AND 300", name="tema_length"),
    )

    tenant_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("tenants.id"), nullable=False, index=True
    )
    titulo: Mapped[str] = mapped_column(Text, nullable=False)
    # What the case is about, in a sentence: this is what is matched against the customer's message.
    tema: Mapped[str] = mapped_column(Text, nullable=False)
    # `[{de: "cliente" | "vendedor", texto}]`, with the personal data already taken out.
    mensajes: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONB, nullable=False, server_default=text("'[]'::jsonb")
    )
    origen: Mapped[str] = mapped_column(Text, nullable=False)
    estado: Mapped[str] = mapped_column(Text, nullable=False, server_default="activo")
    embedding: Mapped[list[float] | None] = mapped_column(
        Vector(EMBEDDING_DIMENSIONS), nullable=True
    )
    embedding_model: Mapped[str | None] = mapped_column(Text, nullable=True)
    sugerencia_id: Mapped[uuid.UUID | None] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("suggestions.id", ondelete="SET NULL"), nullable=True
    )


class LearningMark(Base, UUIDPrimaryKeyMixin, CreatedAtMixin):
    """A conversation the learning already looked at for a source, so it is not analysed twice
    (and not paid for twice), whether it gave a suggestion or not."""

    __tablename__ = "learning_marks"
    __table_args__ = (
        enum_check("fuente", SUGGESTION_SOURCES, "fuente"),
        UniqueConstraint(
            "tenant_id", "conversation_id", "fuente", name="uq_learning_marks_tenant_conv_source"
        ),
    )

    tenant_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("tenants.id"), nullable=False
    )
    conversation_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("conversations.id", ondelete="CASCADE"), nullable=False
    )
    fuente: Mapped[str] = mapped_column(Text, nullable=False)
