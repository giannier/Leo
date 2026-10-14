"""Retrieval quality of the knowledge base: recall@5 (Feature 006, HU-04 and C-004).

Each demo business's documents are chunked and embedded exactly as the worker
does it, with the platform's real embedding model; then each golden question is
retrieved the way `consultar_conocimiento` retrieves it (`retrieve`: one copy per
passage, trusted sources first). A question passes when the passage that answers
it is among the 5 returned. The suite passes at 80 % per business, which is also
the check C-004 asks for: that the multilingual model holds up in Spanish.

    make evals                       # with the others
    uv run python -m pytest evals/test_knowledge_recall.py -m evals -s
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from evals.knowledge_recall import BUSINESSES, RECALL_AT_1_TARGET, RECALL_TARGET, Business, K
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.models.knowledge import KnowledgeChunk
from app.models.tenant import Tenant
from app.repositories.knowledge import KnowledgeSourceRepository
from app.services.knowledge import DEFAULT_PRIORITY, retrieve
from app.workers.knowledge_indexer import _index_extracted_text

pytestmark = pytest.mark.evals


async def _load(db: Session, business: Business, llm) -> uuid.UUID:  # type: ignore[no-untyped-def]
    tenant = Tenant(
        name=business.name,
        status="trial",
        plan_id="trial",
        trial_ends_at=datetime.now(UTC) + timedelta(days=7),
    )
    db.add(tenant)
    db.flush()
    sources = KnowledgeSourceRepository(db, tenant.id)
    for document in business.documents:
        source = sources.add(
            type=document.type,
            title=document.title,
            content=document.content,
            status="indexing",
            priority=DEFAULT_PRIORITY[document.type],
        )
        outcome = await _index_extracted_text(
            db, tenant.id, source.id, document.content, llm=llm, settings=get_settings()
        )
        assert outcome.succeeded, outcome
    return tenant.id


@pytest.mark.parametrize("business", BUSINESSES, ids=[b.name for b in BUSINESSES])
async def test_recall_at_5(db: Session, llm, business: Business) -> None:  # type: ignore[no-untyped-def]
    tenant_id = await _load(db, business, llm)
    vectors = await llm.embed([q.asks for q in business.questions], input_type="query")

    misses: list[str] = []
    first = 0
    for question, vector in zip(business.questions, vectors, strict=True):
        passages = retrieve(db, tenant_id, vector, k=K)
        if not any(question.expects in p.content for p in passages):
            misses.append(f"  - {question.asks!r} (esperaba {question.expects!r})")
        # Nearest passage only, ignoring the priority order: how sharp the ranking is.
        nearest = min(passages, key=lambda p: p.distance) if passages else None
        first += bool(nearest and question.expects in nearest.content)

    chunks = db.scalar(
        select(func.count())
        .select_from(KnowledgeChunk)
        .where(KnowledgeChunk.tenant_id == tenant_id)
    )
    recall = 1 - len(misses) / len(business.questions)
    print(
        f"\n{business.name}: recall@{K} = {recall:.2f}, recall@1 = "
        f"{first / len(business.questions):.2f}, {len(business.questions)} preguntas, "
        f"{chunks} fragmentos ({llm.embedding_model})"
    )
    for miss in misses:
        print(miss)
    assert recall >= RECALL_TARGET, f"recall@{K} {recall:.2f} < {RECALL_TARGET}\n" + "\n".join(
        misses
    )
    assert first / len(business.questions) >= RECALL_AT_1_TARGET
