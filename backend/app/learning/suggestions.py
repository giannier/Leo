"""Suggestions: what the assistant proposes to learn, and what a person does with it (017).

Nothing is applied by itself. A proposal waits as a *pendiente* suggestion; approving it (as it
is, or edited) puts it in force at once and leaves a line in the history; rejecting it keeps it,
so it is not proposed again (the fingerprint is unique per business). A situation can be tried in
the simulator before deciding: it becomes a draft that only the simulator sees.
"""

from __future__ import annotations

import hashlib
import re
import unicodedata
import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from sqlalchemy.orm import Session

from app.learning.history import ConfigHistoryService, situation_snapshot
from app.models.learning import ExampleConversation, Suggestion
from app.models.sales_config import Faq
from app.models.situation import Situation
from app.providers.llm.base import LLMError, LLMProvider
from app.repositories.business import AgentConfigRepository
from app.repositories.learning import SuggestionRepository
from app.repositories.situation import SituationRepository
from app.services.situations import SituationError, SituationInput, SituationService

# Cosine distance under which a new card is said to look like one the business already has.
CLASH_DISTANCE = 0.22
MAX_CLASHES = 3
MAX_RESUMEN = 200
MAX_ANSWER = 2000
MAX_STYLE = 600


class SuggestionError(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def normalize(text: str) -> str:
    folded = unicodedata.normalize("NFKD", text.casefold())
    plain = "".join(c for c in folded if not unicodedata.combining(c))
    return re.sub(r"[^a-z0-9]+", " ", plain).strip()


def fingerprint(tipo: str, key: str) -> str:
    return hashlib.sha256(f"{tipo}:{normalize(key)}".encode()).hexdigest()[:40]


@dataclass(frozen=True)
class Proposal:
    tipo: str
    fuente: str
    resumen: str
    propuesta: dict[str, Any]
    # What makes it the same proposal when it comes up again (the "when it applies", the question).
    key: str
    conversaciones: Sequence[uuid.UUID] = ()
    choques: Sequence[Mapping[str, Any]] = field(default_factory=tuple)


async def find_clashes(
    session: Session,
    tenant_id: uuid.UUID,
    llm: LLMProvider,
    *,
    cuando_aplica: str,
    exclude: Sequence[uuid.UUID] = (),
) -> list[dict[str, Any]]:
    """The situations the business already has that this one looks like. A failed embedding is no
    reason to lose the suggestion: it just comes without the warning."""
    try:
        vector = (await llm.embed([cuando_aplica], input_type="query"))[0]
    except LLMError:
        return []
    found = SituationRepository(session, tenant_id).similar(
        vector,
        model=llm.embedding_model,
        max_distance=CLASH_DISTANCE,
        limit=MAX_CLASHES,
        exclude=exclude,
    )
    return [
        {
            "tipo": "situacion",
            "id": str(situation.id),
            "titulo": situation.titulo,
            "motivo": "Se parece a una situación que ya tienes: revisa que no se contradigan.",
        }
        for situation, _ in found
    ]


class SuggestionService:
    def __init__(
        self, session: Session, tenant_id: uuid.UUID, actor: uuid.UUID | None = None
    ) -> None:
        self._session = session
        self._tenant_id = tenant_id
        self._actor = actor
        self._repo = SuggestionRepository(session, tenant_id)
        self._situations = SituationService(session, tenant_id)
        self._history = ConfigHistoryService(session, tenant_id, actor)

    # --------------------------------------------------------------- propose

    def propose(self, proposal: Proposal) -> Suggestion | None:
        """Add a suggestion, unless this very proposal exists (pending, approved or rejected)."""
        huella = fingerprint(proposal.tipo, proposal.key)
        if self._repo.by_huella(huella) is not None:
            return None
        return self._repo.add(
            tipo=proposal.tipo,
            fuente=proposal.fuente,
            resumen=proposal.resumen[:MAX_RESUMEN],
            propuesta=dict(proposal.propuesta),
            conversaciones=list(proposal.conversaciones),
            choques=[dict(c) for c in proposal.choques],
            huella=huella,
        )

    # --------------------------------------------------------------- inspect

    def get(self, suggestion_id: uuid.UUID) -> Suggestion:
        suggestion = self._repo.get(suggestion_id)
        if suggestion is None:
            raise SuggestionError("not_found", "Esa sugerencia no existe.")
        return suggestion

    # ----------------------------------------------------------------- draft

    def draft(self, suggestion: Suggestion) -> uuid.UUID:
        """The situation of a suggestion as a draft the simulator can try; idempotent."""
        self._pending(suggestion)
        if suggestion.tipo != "situacion":
            raise SuggestionError(
                "not_a_situation", "Solo una situación se puede probar en el simulador."
            )
        existing = suggestion.aplicada.get("situation_id")
        if existing and self._situations.get(uuid.UUID(existing)) is not None:
            return uuid.UUID(existing)
        situation = self._create_situation(suggestion, suggestion.propuesta)
        suggestion.aplicada = {**suggestion.aplicada, "situation_id": str(situation.id)}
        self._session.flush()
        return situation.id

    # --------------------------------------------------------------- decide

    def approve(
        self,
        suggestion: Suggestion,
        *,
        edited: Mapping[str, Any] | None = None,
        answer: str | None = None,
    ) -> Suggestion:
        """Put it in force. A fact needs the answer the reviewer writes: the assistant never
        proposes what the business should answer, only what customers keep asking."""
        self._pending(suggestion)
        proposal = {**suggestion.propuesta, **(edited or {})}
        if suggestion.tipo == "situacion":
            self._apply_situation(suggestion, proposal)
        elif suggestion.tipo == "dato":
            self._apply_fact(suggestion, proposal, answer)
        elif suggestion.tipo == "ejemplo":
            self._apply_example(suggestion, proposal)
        elif suggestion.tipo == "estilo":
            self._apply_style(suggestion, proposal)
        else:  # pragma: no cover - the kinds are all handled above
            raise SuggestionError("unsupported", "Ese tipo de sugerencia aún no se aplica aquí.")
        suggestion.estado = "aprobada"
        suggestion.decidida_por = self._actor
        suggestion.decidida_en = datetime.now(UTC)
        self._session.flush()
        return suggestion

    def approve_many(self, suggestions: Sequence[Suggestion]) -> list[Suggestion]:
        """Several real examples at once: they are many and low-risk (the brief asks for it).
        Anything that is not a pending example is left alone."""
        done: list[Suggestion] = []
        for suggestion in suggestions:
            if suggestion.tipo == "ejemplo" and suggestion.estado == "pendiente":
                done.append(self.approve(suggestion))
        return done

    def reject(self, suggestion: Suggestion, note: str | None = None) -> Suggestion:
        self._pending(suggestion)
        draft = suggestion.aplicada.get("situation_id")
        if draft:
            situation = self._situations.get(uuid.UUID(draft))
            if situation is not None and situation.estado == "borrador":
                self._situations.delete(situation)
        suggestion.aplicada = {}
        suggestion.estado = "rechazada"
        suggestion.nota = (note or "").strip()[:500] or None
        suggestion.decidida_por = self._actor
        suggestion.decidida_en = datetime.now(UTC)
        self._session.flush()
        return suggestion

    # ----------------------------------------------------------------- apply

    @staticmethod
    def _pending(suggestion: Suggestion) -> None:
        if suggestion.estado != "pendiente":
            raise SuggestionError("not_pending", "Esa sugerencia ya se decidió.")

    def _input(self, proposal: Mapping[str, Any]) -> SituationInput:
        titulo = str(proposal.get("titulo") or "").strip()
        cuando = str(proposal.get("cuando_aplica") or "").strip()
        que_hace = str(proposal.get("que_hace") or "").strip()
        if not titulo or not cuando or not que_hace:
            raise SuggestionError("invalid_edit", "Completa el título, cuándo aplica y qué hace.")
        if len(titulo) > 80 or len(cuando) > 300:
            raise SuggestionError(
                "invalid_edit", "El título admite 80 letras y «cuándo aplica», 300."
            )
        return SituationInput(
            titulo=titulo,
            cuando_aplica=cuando,
            que_hace=que_hace,
            derivar=bool(proposal.get("derivar", False)),
            ejemplo_respuesta=(str(proposal.get("ejemplo_respuesta") or "").strip()[:600] or None),
            siempre=bool(proposal.get("siempre", False)),
        )

    def _create_situation(self, suggestion: Suggestion, proposal: Mapping[str, Any]) -> Situation:
        try:
            return self._situations.create(
                self._input(proposal), by=self._actor, origen="aprendida"
            )
        except SituationError as exc:
            raise SuggestionError("invalid_edit", str(exc)) from exc

    def _apply_situation(self, suggestion: Suggestion, proposal: Mapping[str, Any]) -> None:
        draft = suggestion.aplicada.get("situation_id")
        situation = self._situations.get(uuid.UUID(draft)) if draft else None
        try:
            if situation is None:
                situation = self._create_situation(suggestion, proposal)
            else:
                self._situations.update(situation, self._input(proposal))
        except SituationError as exc:
            raise SuggestionError("invalid_edit", str(exc)) from exc
        self._situations.set_state(situation, "activa")
        suggestion.aplicada = {**suggestion.aplicada, "situation_id": str(situation.id)}
        self._history.record(
            "situacion",
            "crear",
            f"Aprendió: {situation.titulo}",
            entidad_id=situation.id,
            despues=situation_snapshot(situation),
            sugerencia_id=suggestion.id,
        )

    def _apply_fact(
        self, suggestion: Suggestion, proposal: Mapping[str, Any], answer: str | None
    ) -> None:
        question = str(proposal.get("pregunta") or "").strip()
        text = (answer or "").strip()
        if len(question) < 3:
            raise SuggestionError("invalid_edit", "Escribe la pregunta.")
        if not text:
            raise SuggestionError(
                "answer_required", "Escribe la respuesta que debe dar el asistente."
            )
        if len(text) > MAX_ANSWER:
            raise SuggestionError("invalid_edit", f"La respuesta admite hasta {MAX_ANSWER} letras.")
        faq = Faq(tenant_id=self._tenant_id, pregunta=question[:300], respuesta=text)
        self._session.add(faq)
        self._session.flush()
        suggestion.aplicada = {**suggestion.aplicada, "faq_id": str(faq.id)}
        self._history.record(
            "faq",
            "crear",
            f"Aprendió una respuesta: {question[:80]}",
            entidad_id=faq.id,
            despues={"pregunta": faq.pregunta, "respuesta": faq.respuesta},
            sugerencia_id=suggestion.id,
        )

    def _apply_example(self, suggestion: Suggestion, proposal: Mapping[str, Any]) -> None:
        title = str(proposal.get("titulo") or "").strip()
        theme = str(proposal.get("tema") or "").strip()
        turns = proposal.get("mensajes")
        if not title or not theme or not isinstance(turns, list) or not turns:
            raise SuggestionError("invalid_edit", "Un ejemplo necesita título, tema y mensajes.")
        if len(title) > 80 or len(theme) > 300:
            raise SuggestionError("invalid_edit", "El título admite 80 letras y el tema, 300.")
        example = ExampleConversation(
            tenant_id=self._tenant_id,
            titulo=title,
            tema=theme,
            mensajes=[
                {"de": str(t.get("de")), "texto": str(t.get("texto"))[:600]}
                for t in turns
                if isinstance(t, dict) and t.get("texto")
            ],
            origen="venta_confirmada" if suggestion.fuente == "venta_confirmada" else "importacion",
            estado="activo",
            sugerencia_id=suggestion.id,
        )
        self._session.add(example)
        self._session.flush()
        suggestion.aplicada = {**suggestion.aplicada, "example_id": str(example.id)}
        self._history.record(
            "ejemplo",
            "crear",
            f"Aprendió un ejemplo real: {title}",
            entidad_id=example.id,
            despues={"titulo": title, "tema": theme},
            sugerencia_id=suggestion.id,
        )

    def _apply_style(self, suggestion: Suggestion, proposal: Mapping[str, Any]) -> None:
        style = " ".join(str(proposal.get("estilo") or "").split())
        if len(style) < 10:
            raise SuggestionError("invalid_edit", "Describe cómo escriben, en una frase.")
        if len(style) > MAX_STYLE:
            raise SuggestionError("invalid_edit", f"El estilo admite hasta {MAX_STYLE} letras.")
        agents = AgentConfigRepository(self._session, self._tenant_id)
        current = agents.current()
        before = current.estilo_vendedor if current is not None else None
        agents.upsert(estilo_vendedor=style)
        self._history.record(
            "estilo",
            "editar",
            "Aprendió cómo escriben tus vendedores",
            antes={"estilo_vendedor": before},
            despues={"estilo_vendedor": style},
            sugerencia_id=suggestion.id,
        )
