"""Access to a business's situations (017, TASK-462 and TASK-463)."""

from __future__ import annotations

import uuid
from collections.abc import Sequence

from sqlalchemy import or_, select

from app.models.situation import Situation
from app.repositories.base import BaseRepository


class SituationRepository(BaseRepository[Situation]):
    model = Situation

    def listing(
        self, *, estado: str | None = None, origen: str | None = None
    ) -> Sequence[Situation]:
        statement = self._scoped().order_by(Situation.created_at)
        if estado is not None:
            statement = statement.where(Situation.estado == estado)
        if origen is not None:
            statement = statement.where(Situation.origen == origen)
        return self._session.execute(statement).scalars().all()

    def migrated(self) -> Situation | None:
        statement = self._scoped().where(Situation.origen == "migrada").limit(1)
        return self._session.execute(statement).scalar_one_or_none()

    def needing_embedding(self, model: str) -> Sequence[Situation]:
        statement = self._scoped().where(
            or_(Situation.embedding.is_(None), Situation.embedding_model != model)
        )
        return self._session.execute(statement).scalars().all()

    def candidates(self, stage: str, *, drafts: Sequence[uuid.UUID] = ()) -> Sequence[Situation]:
        """Situations that may enter a turn at `stage`: active ones and the drafts asked for."""
        state = Situation.estado == "activa"
        if drafts:
            state = or_(state, (Situation.estado == "borrador") & Situation.id.in_(drafts))
        statement = (
            self._scoped()
            .where(state, or_(Situation.etapas.is_(None), Situation.etapas.contains([stage])))
            .order_by(Situation.created_at, Situation.id)
        )
        return self._session.execute(statement).scalars().all()

    def closest(
        self,
        stage: str,
        vector: Sequence[float],
        *,
        model: str,
        max_distance: float,
        limit: int,
        exclude: Sequence[uuid.UUID] = (),
        drafts: Sequence[uuid.UUID] = (),
    ) -> list[tuple[Situation, float]]:
        """The matching situations by cosine distance of `cuando_aplica`, nearest first.

        The tenant and stage filters run before the ORDER BY and LIMIT, as in
        `KnowledgeChunkRepository.search`, so another business's row can never
        take the slot.
        """
        if limit <= 0:
            return []
        distance = Situation.embedding.cosine_distance(list(vector))
        state = Situation.estado == "activa"
        if drafts:
            state = or_(state, (Situation.estado == "borrador") & Situation.id.in_(drafts))
        statement = (
            select(Situation, distance)
            .where(
                self._tenant_predicate,
                state,
                Situation.siempre.is_(False),
                Situation.embedding_model == model,
                Situation.embedding.is_not(None),
                or_(Situation.etapas.is_(None), Situation.etapas.contains([stage])),
                distance < max_distance,
            )
            .order_by(distance)
            .limit(limit)
        )
        if exclude:
            statement = statement.where(Situation.id.not_in(exclude))
        return [(row, float(d)) for row, d in self._session.execute(statement).all()]

    def similar(
        self,
        vector: Sequence[float],
        *,
        model: str,
        max_distance: float,
        limit: int = 3,
        exclude: Sequence[uuid.UUID] = (),
    ) -> list[tuple[Situation, float]]:
        """The situations (active or draft, any stage) whose "cuándo aplica" is close to a text:
        what a new card could clash with."""
        distance = Situation.embedding.cosine_distance(list(vector))
        statement = (
            select(Situation, distance)
            .where(
                self._tenant_predicate,
                Situation.estado.in_(("activa", "borrador")),
                Situation.embedding_model == model,
                Situation.embedding.is_not(None),
                distance < max_distance,
            )
            .order_by(distance)
            .limit(limit)
        )
        if exclude:
            statement = statement.where(Situation.id.not_in(exclude))
        return [(row, float(d)) for row, d in self._session.execute(statement).all()]
