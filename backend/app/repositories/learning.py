"""Suggestions, the history of changes, learning settings and what the learning spent (017)."""

from __future__ import annotations

import uuid
from collections.abc import Collection, Sequence
from datetime import date
from decimal import Decimal

from sqlalchemy import func, or_, select, update
from sqlalchemy.dialects.postgresql import insert

from app.models.learning import (
    ChatImport,
    ConfigHistory,
    ExampleConversation,
    LearningSettings,
    LearningUsage,
    Suggestion,
    UnansweredQuestion,
)
from app.repositories.base import BaseRepository


class LearningSettingsRepository(BaseRepository[LearningSettings]):
    model = LearningSettings

    def current(self) -> LearningSettings | None:
        return self._session.execute(self._scoped()).scalar_one_or_none()

    def ensure(self) -> LearningSettings:
        existing = self.current()
        if existing is not None:
            return existing
        self._session.execute(
            insert(LearningSettings)
            .values(tenant_id=self._tenant_id)
            .on_conflict_do_nothing(index_elements=["tenant_id"])
        )
        self._session.flush()
        row = self.current()
        if row is None:  # pragma: no cover - the insert or a concurrent one just created it
            raise RuntimeError("learning settings could not be created")
        return row


class LearningUsageRepository(BaseRepository[LearningUsage]):
    model = LearningUsage

    def week(self, start: date) -> LearningUsage | None:
        return self._session.execute(
            self._scoped().where(LearningUsage.semana == start)
        ).scalar_one_or_none()

    def spend_calls(self, start: date, calls: int, ceiling: int) -> bool:
        """Add `calls` to the week if that stays within `ceiling`. One statement, so two workers
        at once cannot both take the last call."""
        if calls > ceiling:
            return False
        statement = (
            insert(LearningUsage)
            .values(tenant_id=self._tenant_id, semana=start, llamadas=calls)
            .on_conflict_do_update(
                index_elements=[LearningUsage.tenant_id, LearningUsage.semana],
                set_={"llamadas": LearningUsage.llamadas + calls},
                where=(LearningUsage.llamadas + calls) <= ceiling,
            )
            .returning(LearningUsage.llamadas)
        )
        return self._session.execute(statement).first() is not None

    def add_audio(self, start: date, minutes: Decimal) -> None:
        self._session.execute(
            insert(LearningUsage)
            .values(tenant_id=self._tenant_id, semana=start, minutos_audio=minutes)
            .on_conflict_do_update(
                index_elements=[LearningUsage.tenant_id, LearningUsage.semana],
                set_={"minutos_audio": LearningUsage.minutos_audio + minutes},
            )
        )


class SuggestionRepository(BaseRepository[Suggestion]):
    model = Suggestion

    def by_huella(self, huella: str) -> Suggestion | None:
        return self._session.execute(
            self._scoped().where(Suggestion.huella == huella)
        ).scalar_one_or_none()

    def listing(
        self, *, estado: str | None = None, tipo: str | None = None, limit: int = 100
    ) -> Sequence[Suggestion]:
        statement = self._scoped()
        if estado is not None:
            statement = statement.where(Suggestion.estado == estado)
        if tipo is not None:
            statement = statement.where(Suggestion.tipo == tipo)
        statement = statement.order_by(Suggestion.created_at.desc()).limit(limit)
        return self._session.execute(statement).scalars().all()

    def get_for_update(self, suggestion_id: uuid.UUID) -> Suggestion | None:
        return self._session.execute(
            self._scoped().where(Suggestion.id == suggestion_id).with_for_update()
        ).scalar_one_or_none()

    def many(self, ids: Collection[uuid.UUID]) -> Sequence[Suggestion]:
        if not ids:
            return []
        return (
            self._session.execute(self._scoped().where(Suggestion.id.in_(list(ids))))
            .scalars()
            .all()
        )

    def pending_by_type(self) -> dict[str, int]:
        rows = self._session.execute(
            select(Suggestion.tipo, func.count())
            .where(self._tenant_predicate, Suggestion.estado == "pendiente")
            .group_by(Suggestion.tipo)
        ).all()
        return {str(tipo): int(n) for tipo, n in rows}

    def count_pending_of(self, fuente: str) -> int:
        return self.count(Suggestion.fuente == fuente, Suggestion.estado == "pendiente")


class ConfigHistoryRepository(BaseRepository[ConfigHistory]):
    model = ConfigHistory

    def recent(self, limit: int = 50) -> Sequence[ConfigHistory]:
        return (
            self._session.execute(
                self._scoped().order_by(ConfigHistory.created_at.desc()).limit(limit)
            )
            .scalars()
            .all()
        )

    def mark_undone(self, entry: ConfigHistory, by: uuid.UUID) -> None:
        self._session.execute(
            update(ConfigHistory)
            .where(ConfigHistory.id == entry.id, ConfigHistory.tenant_id == self._tenant_id)
            .values(deshecho_por=by)
        )
        self._session.refresh(entry)


class UnansweredQuestionRepository(BaseRepository[UnansweredQuestion]):
    model = UnansweredQuestion

    def record(
        self, huella: str, text: str, conversation_id: uuid.UUID | None
    ) -> UnansweredQuestion:
        """Count one more time that this question was asked and not answered."""
        existing = self._session.execute(
            self._scoped().where(UnansweredQuestion.huella == huella).with_for_update()
        ).scalar_one_or_none()
        if existing is None:
            return self.add(
                huella=huella,
                pregunta=text,
                conversaciones=[conversation_id] if conversation_id else [],
            )
        existing.veces += 1
        existing.ultima_en = func.now()
        if conversation_id and conversation_id not in existing.conversaciones:
            existing.conversaciones = [*existing.conversaciones, conversation_id][-5:]
        self._session.flush()
        return existing

    def due(self, minimum: int, limit: int = 20) -> Sequence[UnansweredQuestion]:
        """Open questions asked often enough to propose adding the fact."""
        return (
            self._session.execute(
                self._scoped()
                .where(UnansweredQuestion.estado == "abierta", UnansweredQuestion.veces >= minimum)
                .order_by(UnansweredQuestion.veces.desc())
                .limit(limit)
            )
            .scalars()
            .all()
        )


class ExampleRepository(BaseRepository[ExampleConversation]):
    model = ExampleConversation

    def listing(self, *, estado: str | None = None) -> Sequence[ExampleConversation]:
        statement = self._scoped().order_by(ExampleConversation.created_at.desc())
        if estado is not None:
            statement = statement.where(ExampleConversation.estado == estado)
        return self._session.execute(statement).scalars().all()

    def needing_embedding(self, model: str) -> Sequence[ExampleConversation]:
        statement = self._scoped().where(
            or_(
                ExampleConversation.embedding.is_(None),
                ExampleConversation.embedding_model != model,
            )
        )
        return self._session.execute(statement).scalars().all()

    def any_active(self, model: str) -> bool:
        return (
            self._session.execute(
                select(ExampleConversation.id)
                .where(
                    self._tenant_predicate,
                    ExampleConversation.estado == "activo",
                    ExampleConversation.embedding_model == model,
                )
                .limit(1)
            ).first()
            is not None
        )

    def closest(
        self, vector: Sequence[float], *, model: str, max_distance: float, limit: int = 1
    ) -> list[tuple[ExampleConversation, float]]:
        distance = ExampleConversation.embedding.cosine_distance(list(vector))
        statement = (
            select(ExampleConversation, distance)
            .where(
                self._tenant_predicate,
                ExampleConversation.estado == "activo",
                ExampleConversation.embedding_model == model,
                ExampleConversation.embedding.is_not(None),
                distance < max_distance,
            )
            .order_by(distance)
            .limit(limit)
        )
        return [(row, float(d)) for row, d in self._session.execute(statement).all()]


class ChatImportRepository(BaseRepository[ChatImport]):
    model = ChatImport

    def listing(self, limit: int = 20) -> Sequence[ChatImport]:
        return (
            self._session.execute(self._scoped().order_by(ChatImport.creada_en.desc()).limit(limit))
            .scalars()
            .all()
        )

    def running(self) -> ChatImport | None:
        return self._session.execute(
            self._scoped().where(ChatImport.estado == "procesando").limit(1)
        ).scalar_one_or_none()
