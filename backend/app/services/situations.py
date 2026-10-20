"""A business's situations: write, change state, copy a rubro suggestion (017, TASK-462).

The embedding of "cuándo aplica" is not computed here: a save leaves it empty
(or empties it when the text changes) and the worker fills it (`EMBED_JOB`, and
an hourly sweep for anything a lost job left behind). Until then the situation
can only enter a turn through "siempre"; the panel never waits for a model call.
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from sqlalchemy.orm import Session

from app.models.situation import Situation
from app.process.templates.base import Stage
from app.providers.llm.base import LLMProvider
from app.repositories.catalog import CatalogRepository
from app.repositories.media import MediaAssetRepository
from app.repositories.situation import SituationRepository

logger = logging.getLogger(__name__)

EMBED_JOB = "embed_situations"
MAX_QUE_HACE = 1000
MIGRATED_TITLE = "Reglas anteriores del negocio"
MIGRATED_WHEN = "Siempre: son las reglas que el negocio escribió antes de las situaciones."


class SituationError(ValueError):
    """A situation that cannot be saved as asked. `code` is the API error."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class SituationInput:
    titulo: str
    cuando_aplica: str
    que_hace: str
    media_asset_ids: Sequence[uuid.UUID] = ()
    derivar: bool = False
    ejemplo_respuesta: str | None = None
    etapas: Sequence[str] | None = None
    siempre: bool = False


class SituationService:
    def __init__(self, session: Session, tenant_id: uuid.UUID) -> None:
        self._session = session
        self._tenant_id = tenant_id
        self._repo = SituationRepository(session, tenant_id)

    # ---------------------------------------------------------------- checks

    def _check(self, data: SituationInput) -> dict[str, Any]:
        if len(data.que_hace) > MAX_QUE_HACE:
            raise SituationError(
                "que_hace_too_long", f"'qué hace' admite hasta {MAX_QUE_HACE} caracteres"
            )
        stages = None
        if data.etapas is not None:
            known = {s.value for s in Stage}
            unknown = [s for s in data.etapas if s not in known]
            if unknown:
                raise SituationError("unknown_stage", f"etapas desconocidas: {', '.join(unknown)}")
            stages = sorted(set(data.etapas)) or None
        media = MediaAssetRepository(self._session, self._tenant_id)
        for asset_id in data.media_asset_ids:
            if media.get(asset_id) is None:
                raise SituationError("unknown_media", "un archivo no existe en tu biblioteca")
        return {
            "titulo": data.titulo,
            "cuando_aplica": data.cuando_aplica,
            "que_hace": data.que_hace,
            "media_asset_ids": list(dict.fromkeys(data.media_asset_ids)),
            "derivar": data.derivar,
            "ejemplo_respuesta": data.ejemplo_respuesta or None,
            "etapas": stages,
            "siempre": data.siempre,
        }

    # ----------------------------------------------------------------- write

    def get(self, situation_id: uuid.UUID) -> Situation | None:
        return self._repo.get(situation_id)

    def listing(
        self, *, estado: str | None = None, origen: str | None = None
    ) -> Sequence[Situation]:
        return self._repo.listing(estado=estado, origen=origen)

    def create(
        self, data: SituationInput, *, by: uuid.UUID | None, origen: str = "manual"
    ) -> Situation:
        values = self._check(data)
        # The clock, not the transaction's `now()`: two saves in one transaction
        # keep their order (the selector lists "siempre" ones by creation).
        return self._repo.add(
            **values,
            estado="borrador",
            origen=origen,
            created_by=by,
            created_at=datetime.now(UTC),
        )

    def update(self, situation: Situation, data: SituationInput) -> Situation:
        values = self._check(data)
        if values["cuando_aplica"] != situation.cuando_aplica:
            situation.embedding = None
            situation.embedding_model = None
        for key, value in values.items():
            setattr(situation, key, value)
        self._session.flush()
        return situation

    def set_state(self, situation: Situation, estado: str) -> Situation:
        situation.estado = estado
        self._session.flush()
        return situation

    def delete(self, situation: Situation) -> None:
        self._session.delete(situation)
        self._session.flush()

    def from_suggested(
        self,
        suggested_id: uuid.UUID,
        rubro_key: str | None,
        valores: Mapping[str, str],
        *,
        by: uuid.UUID | None,
    ) -> Situation:
        """A draft copied from a suggestion of the business's published rubro.

        Every field the suggestion asks for must be filled; the values are
        appended to "qué hace" so the assistant reads them with the rule.
        """
        catalog = CatalogRepository(self._session)
        version = catalog.published("rubro", rubro_key) if rubro_key else None
        suggestion = (
            next(
                (s for s in catalog.suggested_situations(version.id) if s.id == suggested_id),
                None,
            )
            if version is not None
            else None
        )
        if suggestion is None:
            raise SituationError("unknown_suggestion", "esa situación sugerida no es de tu rubro")
        # The same suggestion twice is a double click or a screen that forgot it had been
        # made: hand back the copy the business already has instead of a second one.
        already = next(
            (
                existing
                for existing in self._repo.listing(origen="sugerida_rubro")
                if existing.titulo == suggestion.titulo[:80]
            ),
            None,
        )
        if already is not None:
            return already
        missing = [label for key, label in suggestion.campos.items() if not valores.get(key)]
        if missing:
            raise SituationError("missing_fields", f"completa: {', '.join(missing)}")
        que_hace = suggestion.que_hace
        if suggestion.campos:
            filled = "; ".join(
                f"{label}: {valores[key].strip()}" for key, label in suggestion.campos.items()
            )
            que_hace = f"{que_hace} ({filled})"
        return self.create(
            SituationInput(
                titulo=suggestion.titulo[:80],
                cuando_aplica=suggestion.cuando_aplica,
                que_hace=que_hace,
            ),
            by=by,
            origen="sugerida_rubro",
        )

    def set_migrated_text(self, text: str | None) -> Situation | None:
        """The wizard's old free text, kept as the migrated situation (RF-011)."""
        existing = self._repo.migrated()
        cleaned = (text or "").strip()[:4000]
        if not cleaned:
            if existing is not None:
                existing.estado = "inactiva"
                self._session.flush()
            return existing
        if existing is None:
            return self._repo.add(
                titulo=MIGRATED_TITLE,
                cuando_aplica=MIGRATED_WHEN,
                que_hace=cleaned,
                siempre=True,
                estado="activa",
                origen="migrada",
            )
        existing.que_hace = cleaned
        existing.estado = "activa"
        self._session.flush()
        return existing


async def embed_pending(session: Session, tenant_id: uuid.UUID, llm: LLMProvider) -> int:
    """Embed the "cuándo aplica" of every situation of the tenant that lacks it."""
    pending = SituationRepository(session, tenant_id).needing_embedding(llm.embedding_model)
    if not pending:
        return 0
    vectors = await llm.embed([s.cuando_aplica for s in pending], input_type="passage")
    for situation, vector in zip(pending, vectors, strict=True):
        situation.embedding = list(vector)
        situation.embedding_model = llm.embedding_model
    session.flush()
    return len(pending)
