"""The real conversation closest to this message, if the business has approved any (017, 2).

The brief: «casos reales que el agente consulta cuando aparece una situación parecida». One
conversation at most, the nearest by the embedding of what it is about (`tema`) against the
customer's message, and only when it is close. A business with no examples pays nothing: no query,
no embedding. A failed embedding is no reason to lose the turn.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass

from sqlalchemy.orm import Session

from app.providers.llm.base import LLMError, LLMProvider
from app.repositories.learning import ExampleRepository

logger = logging.getLogger(__name__)

# Cosine distance under which a conversation counts as a similar case.
MAX_DISTANCE = 0.45


@dataclass(frozen=True)
class SelectedExample:
    id: uuid.UUID
    titulo: str
    # `(role, text)` in the order they were said; the personal data are already out.
    mensajes: tuple[tuple[str, str], ...]
    distancia: float

    def trace(self) -> dict[str, object]:
        return {"id": str(self.id), "titulo": self.titulo, "distancia": round(self.distancia, 3)}


async def select_examples(
    session: Session, tenant_id: uuid.UUID, *, message: str, llm: LLMProvider
) -> list[SelectedExample]:
    repo = ExampleRepository(session, tenant_id)
    text = message.strip()
    if not text or not repo.any_active(llm.embedding_model):
        return []
    try:
        vector = (await llm.embed([text], input_type="query"))[0]
    except LLMError:
        logger.warning("example selector: embedding failed; no example this turn")
        return []
    return [
        SelectedExample(
            id=example.id,
            titulo=example.titulo,
            mensajes=tuple((str(t.get("de")), str(t.get("texto"))) for t in example.mensajes),
            distancia=distance,
        )
        for example, distance in repo.closest(
            vector, model=llm.embedding_model, max_distance=MAX_DISTANCE, limit=1
        )
    ]
