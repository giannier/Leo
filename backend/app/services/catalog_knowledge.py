"""The rubro's frequent questions and objections as knowledge (017, TASK-461).

They are not in the fixed prompt (that would spend the token budget on every
turn): `consultar_conocimiento` retrieves the ones close to the question, after
the business's own documents, and labels them as guidance on **how** to answer.
They never carry business facts (the rubro texts are written that way).

Vectors are computed by a worker job for every published rubro version that
lacks them for the platform's embedding model, so a search never embeds the
catalogue itself. A search with a different embedding model (a tenant with its
own credentials) finds nothing rather than comparing incomparable vectors.
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import Sequence
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.catalog import CatalogEmbedding, CatalogFaq, CatalogObjection, CatalogVersion
from app.providers.llm.base import LLMProvider

logger = logging.getLogger(__name__)

# Cosine distance under which a rubro item counts as related to the question.
# Unrelated short Spanish texts sit well above it with the platform's model.
MAX_DISTANCE = 0.6
MAX_RESULTS = 2
# The worker job that embeds them; the platform enqueues it on publish (TASK-466).
INDEX_CATALOG_JOB = "index_catalog_knowledge"


@dataclass(frozen=True)
class RubroHit:
    kind: str  # "faq" | "objecion"
    text: str


def _items(session: Session, version_id: uuid.UUID) -> list[tuple[uuid.UUID, str, str]]:
    faqs = session.execute(select(CatalogFaq).where(CatalogFaq.version_id == version_id)).scalars()
    objections = session.execute(
        select(CatalogObjection).where(CatalogObjection.version_id == version_id)
    ).scalars()
    return [(f.id, "faq", f"{f.pregunta}\n{f.respuesta}") for f in faqs] + [
        (o.id, "objecion", f"{o.objecion}\n{o.manejo}") for o in objections
    ]


async def index_published_rubros(session: Session, llm: LLMProvider) -> int:
    """Embed every item of the published rubro versions still missing a vector. Returns how many."""
    versions = session.execute(
        select(CatalogVersion.id).where(
            CatalogVersion.kind == "rubro", CatalogVersion.estado == "publicada"
        )
    ).scalars()
    done = 0
    for version_id in versions:
        existing = {
            row.item_id: row
            for row in session.execute(
                select(CatalogEmbedding).where(CatalogEmbedding.version_id == version_id)
            ).scalars()
        }
        pending = [
            item
            for item in _items(session, version_id)
            if item[0] not in existing or existing[item[0]].embedding_model != llm.embedding_model
        ]
        if not pending:
            continue
        vectors = await llm.embed([text for _, _, text in pending], input_type="passage")
        for (item_id, kind, _), vector in zip(pending, vectors, strict=True):
            row = existing.get(item_id)
            if row is None:
                session.add(
                    CatalogEmbedding(
                        item_id=item_id,
                        item_kind=kind,
                        version_id=version_id,
                        embedding=list(vector),
                        embedding_model=llm.embedding_model,
                    )
                )
            else:
                row.embedding = list(vector)
                row.embedding_model = llm.embedding_model
        session.flush()
        done += len(pending)
    return done


def search_rubro(
    session: Session,
    version_id: uuid.UUID | None,
    query_vector: Sequence[float],
    *,
    embedding_model: str,
    limit: int = MAX_RESULTS,
) -> list[RubroHit]:
    """The rubro items closest to the question, within `MAX_DISTANCE`."""
    if version_id is None:
        return []
    distance = CatalogEmbedding.embedding.cosine_distance(list(query_vector))
    rows = session.execute(
        select(CatalogEmbedding.item_id, CatalogEmbedding.item_kind, distance)
        .where(
            CatalogEmbedding.version_id == version_id,
            CatalogEmbedding.embedding_model == embedding_model,
            distance < MAX_DISTANCE,
        )
        .order_by(distance)
        .limit(limit)
    ).all()
    hits: list[RubroHit] = []
    for item_id, kind, _ in rows:
        if kind == "faq":
            faq = session.get(CatalogFaq, item_id)
            if faq is not None:
                hits.append(RubroHit(kind, f"Pregunta: {faq.pregunta} → {faq.respuesta}"))
        else:
            objection = session.get(CatalogObjection, item_id)
            if objection is not None:
                hits.append(RubroHit(kind, f"Objeción: {objection.objecion} → {objection.manejo}"))
    return hits
