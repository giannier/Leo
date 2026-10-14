"""Which of the business's situations enter this turn (Feature 017, TASK-463, C-10).

The active situations marked "siempre", plus the ones whose "cuándo aplica" is
close to the customer's message **and** that apply in the current stage, up to
the business's `max_situaciones` (5 by default). Closeness is the cosine
distance between embeddings: one embedding call per turn, no extra chat call.

If the embedding fails, only the "siempre" ones enter: a situation is an extra,
never a reason to lose the turn. The trace keeps the ids and the distances.

The simulator may ask for drafts (`drafts`): they enter as if they were active,
only in that turn (RF-009).
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import Sequence
from dataclasses import dataclass

from sqlalchemy.orm import Session

from app.models.situation import Situation
from app.providers.llm.base import LLMError, LLMProvider
from app.repositories.media import MediaAssetRepository
from app.repositories.situation import SituationRepository

logger = logging.getLogger(__name__)

# Cosine distance under which "cuándo aplica" counts as matching the message.
MAX_DISTANCE = 0.55
DEFAULT_MAX = 5


@dataclass(frozen=True)
class SelectedSituation:
    """A situation as the prompt shows it."""

    id: uuid.UUID
    titulo: str
    cuando_aplica: str
    que_hace: str
    claves: tuple[str, ...] = ()
    derivar: bool = False
    ejemplo_respuesta: str | None = None
    siempre: bool = False
    borrador: bool = False
    # None for "siempre" (not matched by similarity).
    distancia: float | None = None

    def trace(self) -> dict[str, object]:
        return {
            "id": str(self.id),
            "titulo": self.titulo,
            "siempre": self.siempre,
            "borrador": self.borrador,
            "distancia": None if self.distancia is None else round(self.distancia, 3),
        }


def _selected(
    situation: Situation, claves: dict[uuid.UUID, str], distance: float | None
) -> SelectedSituation:
    return SelectedSituation(
        id=situation.id,
        titulo=situation.titulo,
        cuando_aplica=situation.cuando_aplica,
        que_hace=situation.que_hace,
        claves=tuple(claves[i] for i in situation.media_asset_ids if i in claves),
        derivar=situation.derivar,
        ejemplo_respuesta=situation.ejemplo_respuesta,
        siempre=situation.siempre,
        borrador=situation.estado == "borrador",
        distancia=distance,
    )


async def select_situations(
    session: Session,
    tenant_id: uuid.UUID,
    *,
    stage: str,
    message: str,
    llm: LLMProvider,
    max_situaciones: int = DEFAULT_MAX,
    drafts: Sequence[uuid.UUID] = (),
) -> list[SelectedSituation]:
    repo = SituationRepository(session, tenant_id)
    candidates = repo.candidates(stage, drafts=drafts)
    always = [s for s in candidates if s.siempre][:max_situaciones]
    matched: list[tuple[Situation, float]] = []
    room = max_situaciones - len(always)
    text = message.strip()
    has_matchable = any(not s.siempre for s in candidates)
    if room > 0 and text and has_matchable:
        try:
            vector = (await llm.embed([text], input_type="query"))[0]
        except LLMError:
            logger.warning("situation selector: embedding failed; only the 'siempre' ones enter")
        else:
            matched = repo.closest(
                stage,
                vector,
                model=llm.embedding_model,
                max_distance=MAX_DISTANCE,
                limit=room,
                drafts=drafts,
            )

    chosen = [*always, *(s for s, _ in matched)]
    media_ids = {i for s in chosen for i in s.media_asset_ids}
    claves: dict[uuid.UUID, str] = {}
    if media_ids:
        media = MediaAssetRepository(session, tenant_id)
        for asset_id in media_ids:
            asset = media.get(asset_id)
            if asset is not None and asset.is_active:
                claves[asset_id] = asset.clave
    distances = {s.id: d for s, d in matched}
    return [_selected(s, claves, distances.get(s.id)) for s in chosen]
