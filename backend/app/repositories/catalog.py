"""Read and write the platform catalogue of templates and rubros (017, TASK-460).

Platform data, not tenant data: a plain repository like `PlanRepository`, read by
every business and written only by the platform operator (TASK-466). A
published version is frozen (C-04); `RuntimeError` here mirrors the trigger in
migration 0030 so the caller gets a clear error before the database's.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from datetime import UTC, datetime

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.catalog import (
    CatalogFaq,
    CatalogObjection,
    CatalogRule,
    CatalogSuggestedSituation,
    CatalogVersion,
    Rubro,
)


class VersionFrozen(RuntimeError):
    """The version is published or retired: a change is a new version."""


class CatalogRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    # ------------------------------------------------------------------ reads

    def published(self, kind: str, key: str) -> CatalogVersion | None:
        """The newest published version of a template or rubro, or None."""
        statement = (
            select(CatalogVersion)
            .where(
                CatalogVersion.kind == kind,
                CatalogVersion.key == key,
                CatalogVersion.estado == "publicada",
            )
            .order_by(CatalogVersion.version.desc())
            .limit(1)
        )
        return self._session.execute(statement).scalar_one_or_none()

    def version(self, kind: str, key: str, number: int) -> CatalogVersion | None:
        statement = select(CatalogVersion).where(
            CatalogVersion.kind == kind,
            CatalogVersion.key == key,
            CatalogVersion.version == number,
        )
        return self._session.execute(statement).scalar_one_or_none()

    def versions(self, kind: str, key: str) -> Sequence[CatalogVersion]:
        statement = (
            select(CatalogVersion)
            .where(CatalogVersion.kind == kind, CatalogVersion.key == key)
            .order_by(CatalogVersion.version)
        )
        return self._session.execute(statement).scalars().all()

    def rules(self, version_id: uuid.UUID) -> Sequence[CatalogRule]:
        statement = (
            select(CatalogRule)
            .where(CatalogRule.version_id == version_id)
            .order_by(CatalogRule.orden)
        )
        return self._session.execute(statement).scalars().all()

    def faqs(self, version_id: uuid.UUID) -> Sequence[CatalogFaq]:
        statement = (
            select(CatalogFaq).where(CatalogFaq.version_id == version_id).order_by(CatalogFaq.orden)
        )
        return self._session.execute(statement).scalars().all()

    def objections(self, version_id: uuid.UUID) -> Sequence[CatalogObjection]:
        statement = (
            select(CatalogObjection)
            .where(CatalogObjection.version_id == version_id)
            .order_by(CatalogObjection.orden)
        )
        return self._session.execute(statement).scalars().all()

    def suggested_situations(self, version_id: uuid.UUID) -> Sequence[CatalogSuggestedSituation]:
        statement = (
            select(CatalogSuggestedSituation)
            .where(CatalogSuggestedSituation.version_id == version_id)
            .order_by(CatalogSuggestedSituation.orden)
        )
        return self._session.execute(statement).scalars().all()

    def rubro(self, key: str | None) -> Rubro | None:
        return self._session.get(Rubro, key) if key else None

    def rubros(self) -> Sequence[Rubro]:
        return self._session.execute(select(Rubro).order_by(Rubro.key)).scalars().all()

    # ----------------------------------------------------------------- writes

    def new_draft(self, kind: str, key: str, *, notas: str | None = None) -> CatalogVersion:
        """A new draft version, copying the content of the newest version."""
        latest = self._session.execute(
            select(func.max(CatalogVersion.version)).where(
                CatalogVersion.kind == kind, CatalogVersion.key == key
            )
        ).scalar_one()
        source = self.version(kind, key, latest) if latest is not None else None
        draft = CatalogVersion(
            kind=kind,
            key=key,
            version=(latest or 0) + 1,
            notas=notas,
            # What a rubro template suggests for the process (feature 020) carries over too.
            plantilla=dict(source.plantilla) if source is not None and source.plantilla else None,
        )
        self._session.add(draft)
        self._session.flush()
        if source is not None:
            self._copy(source.id, draft.id)
        return draft

    def _copy(self, source: uuid.UUID, target: uuid.UUID) -> None:
        for rule in self.rules(source):
            self._session.add(
                CatalogRule(
                    version_id=target,
                    orden=rule.orden,
                    rule_key=rule.rule_key,
                    etapa=rule.etapa,
                    texto=rule.texto,
                )
            )
        for faq in self.faqs(source):
            self._session.add(
                CatalogFaq(
                    version_id=target,
                    orden=faq.orden,
                    pregunta=faq.pregunta,
                    respuesta=faq.respuesta,
                )
            )
        for objection in self.objections(source):
            self._session.add(
                CatalogObjection(
                    version_id=target,
                    orden=objection.orden,
                    objecion=objection.objecion,
                    manejo=objection.manejo,
                )
            )
        for situation in self.suggested_situations(source):
            self._session.add(
                CatalogSuggestedSituation(
                    version_id=target,
                    orden=situation.orden,
                    clave=situation.clave,
                    etapas=list(situation.etapas) if situation.etapas else None,
                    siempre=situation.siempre,
                    titulo=situation.titulo,
                    cuando_aplica=situation.cuando_aplica,
                    que_hace=situation.que_hace,
                    campos=dict(situation.campos),
                )
            )
        self._session.flush()

    def require_draft(self, version: CatalogVersion) -> None:
        if version.estado != "borrador":
            raise VersionFrozen(
                f"{version.kind} {version.key} v{version.version} is {version.estado}"
            )

    def publish(self, version: CatalogVersion, *, by: uuid.UUID | None) -> None:
        """Publish a draft. Whether its evals passed is the caller's check (TASK-466)."""
        self.require_draft(version)
        version.estado = "publicada"
        version.publicada_en = datetime.now(UTC)
        version.publicada_por = by
        self._session.flush()
