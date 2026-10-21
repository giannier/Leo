"""The platform catalogue of process templates and rubros (Feature 017, TASK-460).

Two of the four layers of the configuration (017, C-03 and C-04) are written by
the platform, not by a business: the **rules of the process template** (how a
sale runs: `productos`, `certificaciones`, `servicios`) and what the platform
knows about a **rubro** (rules, frequent questions, objections and suggested
situations). They live here, versioned:

* a `catalog_versions` row is one version of one template or rubro;
* a `publicada` version never changes: a change is a new version (the
  repository refuses writes, and so does a trigger in the migration);
* rules carry a `rule_key` that stays the same across versions, which is what a
  business switches off (TASK-461).

The order of the stages, their conditions, tools and funnel stay in code
(`process/templates/`). The catalogue tables are platform data: no `tenant_id`,
read by every business, written only from the platform panel (TASK-466). Two
tables at the end are per business: the rules it switched off and the version
whose news its reviewer saw (TASK-461).
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Integer,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.dialects.postgresql import UUID as PgUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, CreatedAtMixin, UUIDPrimaryKeyMixin, enum_check
from app.models.knowledge import EMBEDDING_DIMENSIONS

CATALOG_KINDS = ("proceso", "rubro")
VERSION_STATES = ("borrador", "publicada", "retirada")


class Rubro(Base):
    """A rubro (`business_profiles.industry`) and the template it suggests."""

    __tablename__ = "rubros"

    key: Mapped[str] = mapped_column(Text, primary_key=True)
    nombre: Mapped[str] = mapped_column(Text, nullable=False)
    plantilla_sugerida: Mapped[str] = mapped_column(Text, nullable=False)
    # The questions agents of this rubro usually ask to know a customer (feature 019, phase E).
    preguntas_calificacion: Mapped[list[str]] = mapped_column(
        ARRAY(Text), nullable=False, server_default=text("'{}'::text[]")
    )
    # What the owner should know before turning something on, from the WhatsApp policy (019).
    aviso_politica: Mapped[str | None] = mapped_column(Text, nullable=True)
    # What the agents of this rubro usually do (feature 019): the first suggestion in the setup.
    capacidades_sugeridas: Mapped[list[str]] = mapped_column(
        ARRAY(Text), nullable=False, server_default=text("ARRAY['atender','vender']::text[]")
    )


class CatalogVersion(UUIDPrimaryKeyMixin, CreatedAtMixin, Base):
    """One version of a process template or a rubro."""

    __tablename__ = "catalog_versions"
    __table_args__ = (
        enum_check("kind", CATALOG_KINDS, "kind"),
        enum_check("estado", VERSION_STATES, "estado"),
        UniqueConstraint("kind", "key", "version"),
    )

    kind: Mapped[str] = mapped_column(Text, nullable=False)
    key: Mapped[str] = mapped_column(Text, nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    estado: Mapped[str] = mapped_column(Text, nullable=False, server_default="borrador")
    # The result of the eval run that allowed publishing it (TASK-466).
    evals: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    notas: Mapped[str | None] = mapped_column(Text, nullable=True)
    publicada_en: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    publicada_por: Mapped[uuid.UUID | None] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("users.id"), nullable=True
    )


class _VersionChild(UUIDPrimaryKeyMixin):
    version_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True),
        ForeignKey("catalog_versions.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    orden: Mapped[int] = mapped_column(Integer, nullable=False)


class CatalogRule(_VersionChild, Base):
    """One rule. `etapa` is set for process rules and NULL for rubro rules."""

    __tablename__ = "catalog_rules"
    __table_args__ = (UniqueConstraint("version_id", "rule_key"),)

    rule_key: Mapped[str] = mapped_column(Text, nullable=False)
    etapa: Mapped[str | None] = mapped_column(Text, nullable=True)
    texto: Mapped[str] = mapped_column(Text, nullable=False)


class CatalogFaq(_VersionChild, Base):
    """A frequent question of a rubro and how to answer it."""

    __tablename__ = "catalog_faqs"

    pregunta: Mapped[str] = mapped_column(Text, nullable=False)
    respuesta: Mapped[str] = mapped_column(Text, nullable=False)


class CatalogObjection(_VersionChild, Base):
    """A typical objection of a rubro and how to handle it."""

    __tablename__ = "catalog_objections"

    objecion: Mapped[str] = mapped_column(Text, nullable=False)
    manejo: Mapped[str] = mapped_column(Text, nullable=False)


class CatalogSuggestedSituation(_VersionChild, Base):
    """A situation the rubro suggests; the business completes `campos` to activate it."""

    __tablename__ = "catalog_suggested_situations"

    titulo: Mapped[str] = mapped_column(Text, nullable=False)
    cuando_aplica: Mapped[str] = mapped_column(Text, nullable=False)
    que_hace: Mapped[str] = mapped_column(Text, nullable=False)
    # {campo: etiqueta}: what the business fills in ("minimo_unidades": "Desde cuántas...").
    campos: Mapped[dict[str, str]] = mapped_column(JSONB, nullable=False, server_default="{}")


class CatalogEmbedding(Base):
    """The vector of a rubro FAQ or objection, so `consultar_conocimiento` finds it.

    Kept apart from the frozen version rows: computing a vector is not a change to
    the published content (migration 0031).
    """

    __tablename__ = "catalog_embeddings"
    __table_args__ = (enum_check("item_kind", ("faq", "objecion"), "item_kind"),)

    item_id: Mapped[uuid.UUID] = mapped_column(PgUUID(as_uuid=True), primary_key=True)
    item_kind: Mapped[str] = mapped_column(Text, nullable=False)
    version_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True),
        ForeignKey("catalog_versions.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    embedding: Mapped[list[float]] = mapped_column(Vector(EMBEDDING_DIMENSIONS), nullable=False)
    embedding_model: Mapped[str] = mapped_column(Text, nullable=False)


class TenantRuleSwitch(Base):
    """A template or rubro rule a business switched off (017, C-01 and C-04)."""

    __tablename__ = "tenant_rule_switches"
    __table_args__ = (enum_check("kind", CATALOG_KINDS, "kind"),)

    tenant_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("tenants.id"), primary_key=True
    )
    kind: Mapped[str] = mapped_column(Text, primary_key=True)
    rule_key: Mapped[str] = mapped_column(Text, primary_key=True)
    apagada: Mapped[bool] = mapped_column(Boolean, nullable=False)
    cambiado_por: Mapped[uuid.UUID | None] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )


class TenantCatalogSeen(Base):
    """The version of a template or rubro whose news the reviewer already saw."""

    __tablename__ = "tenant_catalog_seen"
    __table_args__ = (enum_check("kind", CATALOG_KINDS, "kind"),)

    tenant_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("tenants.id"), primary_key=True
    )
    kind: Mapped[str] = mapped_column(Text, primary_key=True)
    key: Mapped[str] = mapped_column(Text, primary_key=True)
    version_vista: Mapped[int] = mapped_column(Integer, nullable=False)
