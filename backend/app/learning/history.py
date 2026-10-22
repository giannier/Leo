"""The history of changes, and undoing one (017, RF-022).

Every change to the situations, the rule switches, the profile and the facts the learning adds
leaves a line with what it was before. Undoing puts the "before" back and leaves a line of its
own, so the history is never rewritten; a line can be undone once.
"""

from __future__ import annotations

import contextlib
import uuid
from typing import Any

from sqlalchemy.orm import Session

from app.models.learning import ConfigHistory
from app.models.situation import Situation
from app.repositories.learning import ConfigHistoryRepository

SITUATION_FIELDS = (
    "titulo",
    "cuando_aplica",
    "que_hace",
    "derivar",
    "ejemplo_respuesta",
    "etapas",
    "siempre",
    "estado",
    "origen",
)


class HistoryError(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def situation_snapshot(situation: Situation) -> dict[str, Any]:
    snap = {name: getattr(situation, name) for name in SITUATION_FIELDS}
    snap["media_asset_ids"] = [str(i) for i in situation.media_asset_ids]
    snap["etapas"] = list(situation.etapas) if situation.etapas is not None else None
    return snap


class ConfigHistoryService:
    def __init__(
        self, session: Session, tenant_id: uuid.UUID, actor: uuid.UUID | None = None
    ) -> None:
        self._session = session
        self._tenant_id = tenant_id
        self._actor = actor
        self._repo = ConfigHistoryRepository(session, tenant_id)

    def record(
        self,
        entidad: str,
        accion: str,
        resumen: str,
        *,
        entidad_id: uuid.UUID | None = None,
        antes: dict[str, Any] | None = None,
        despues: dict[str, Any] | None = None,
        sugerencia_id: uuid.UUID | None = None,
    ) -> ConfigHistory:
        from datetime import UTC, datetime

        return self._repo.add(
            entidad=entidad,
            entidad_id=entidad_id,
            accion=accion,
            resumen=resumen[:200],
            antes=antes,
            despues=despues,
            actor=self._actor,
            sugerencia_id=sugerencia_id,
            # Stamped here, not by the database: `now()` is the start of the transaction.
            created_at=datetime.now(UTC),
        )

    # ------------------------------------------------------------------ undo

    def undo(self, entry: ConfigHistory) -> ConfigHistory:
        if entry.accion == "deshacer":
            raise HistoryError(
                "not_undoable", "Deshacer un «deshacer» no hace falta: repite el cambio."
            )
        if entry.deshecho_por is not None:
            raise HistoryError("already_undone", "Ese cambio ya se deshizo.")
        handler = {
            "situacion": self._undo_situation,
            "interruptor": self._undo_switch,
            "perfil": self._undo_profile,
            "faq": self._undo_faq,
            "ejemplo": self._undo_example,
            "estilo": self._undo_style,
        }.get(entry.entidad)
        if handler is None:
            raise HistoryError("not_undoable", "Ese cambio no se puede deshacer desde aquí.")
        summary = handler(entry)
        undone = self.record(
            entry.entidad,
            "deshacer",
            f"Deshecho: {summary}",
            entidad_id=entry.entidad_id,
            antes=entry.despues,
            despues=entry.antes,
            sugerencia_id=entry.sugerencia_id,
        )
        self._repo.mark_undone(entry, undone.id)
        return undone

    def _undo_situation(self, entry: ConfigHistory) -> str:
        from app.repositories.situation import SituationRepository

        repo = SituationRepository(self._session, self._tenant_id)
        current = repo.get(entry.entidad_id) if entry.entidad_id else None
        if entry.accion == "crear":
            if current is not None:
                self._session.delete(current)
                self._session.flush()
            return entry.resumen
        if entry.antes is None or entry.entidad_id is None:
            raise HistoryError("not_undoable", "No quedó guardado cómo era antes.")
        before = dict(entry.antes)
        media = [uuid.UUID(i) for i in before.pop("media_asset_ids", [])]
        if current is None:  # it was deleted: bring it back, with the same id
            repo.add(id=entry.entidad_id, media_asset_ids=media, **before)
            return entry.resumen
        if before.get("cuando_aplica") != current.cuando_aplica:
            current.embedding = None
            current.embedding_model = None
        for name, value in before.items():
            setattr(current, name, value)
        current.media_asset_ids = media
        self._session.flush()
        return entry.resumen

    def _undo_switch(self, entry: ConfigHistory) -> str:
        from app.process.layers import LayerResolver, UnknownRule

        before = entry.antes or {}
        if not {"kind", "rule_key", "key"} <= set(before):
            raise HistoryError("not_undoable", "No quedó guardado cómo estaba la regla.")
        try:
            LayerResolver(self._session, self._tenant_id).switch(
                before["kind"],
                before["key"],
                before["rule_key"],
                apagada=bool(before.get("apagada", False)),
                by=self._actor,
            )
        except UnknownRule as exc:
            raise HistoryError("not_undoable", "Esa regla ya no existe en tu plantilla.") from exc
        return entry.resumen

    def _undo_profile(self, entry: ConfigHistory) -> str:
        from app.repositories.business import AgentConfigRepository

        before = entry.antes or {}
        if not before:
            raise HistoryError("not_undoable", "No quedó guardado cómo era antes.")
        AgentConfigRepository(self._session, self._tenant_id).upsert(**before)
        return entry.resumen

    def _undo_faq(self, entry: ConfigHistory) -> str:
        from sqlalchemy import select

        from app.models.sales_config import Faq
        from app.services.knowledge import KnowledgeService, SourceNotFoundError

        if entry.accion != "crear" or entry.entidad_id is None:
            raise HistoryError("not_undoable", "Ese cambio no se puede deshacer desde aquí.")
        faq = self._session.execute(
            select(Faq).where(Faq.tenant_id == self._tenant_id, Faq.id == entry.entidad_id)
        ).scalar_one_or_none()
        if faq is not None:
            if faq.knowledge_source_id is not None:
                with contextlib.suppress(SourceNotFoundError):
                    KnowledgeService(self._session, self._tenant_id).delete(faq.knowledge_source_id)
            self._session.delete(faq)
            self._session.flush()
        return entry.resumen

    def _undo_example(self, entry: ConfigHistory) -> str:
        from app.models.learning import ExampleConversation

        if entry.accion != "crear" or entry.entidad_id is None:
            raise HistoryError("not_undoable", "Ese cambio no se puede deshacer desde aquí.")
        example = self._session.get(ExampleConversation, entry.entidad_id)
        if example is not None and example.tenant_id == self._tenant_id:
            self._session.delete(example)
            self._session.flush()
        return entry.resumen

    def _undo_style(self, entry: ConfigHistory) -> str:
        from app.repositories.business import AgentConfigRepository

        before = entry.antes or {}
        if "estilo_vendedor" not in before:
            raise HistoryError("not_undoable", "No quedó guardado cómo era antes.")
        AgentConfigRepository(self._session, self._tenant_id).upsert(
            estilo_vendedor=before["estilo_vendedor"]
        )
        return entry.resumen
