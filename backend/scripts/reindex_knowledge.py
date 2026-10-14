"""Re-embed knowledge sources under the currently configured model.

    make reindex                # everything embedded by a different model
    make reindex ARGS=--all     # everything, whatever it was embedded with
    make reindex ARGS=--dry-run # say what would happen, change nothing

**Why this has to exist.** Vectors produced by two different embedding models
are not comparable, even at the same width. Nothing enforces that: a mixed
table returns results, they are simply wrong, and the failure is silent -
retrieval quietly stops finding the right passage and the agent starts
answering from nothing. Switching `LLM_PROFILE` therefore invalidates every
vector stored under the old one.

So the default is not "reindex everything". It is "reindex what the current
model did not produce", which is the set that is actually broken, and it is
computed from `knowledge_chunks.embedding_model` rather than guessed.

The work itself goes back through the ordinary worker path: reset the source
to `pending` and enqueue it. Re-implementing extraction and chunking here
would mean a second copy of that logic, drifting.
"""

from __future__ import annotations

import argparse
import asyncio
import uuid
from collections.abc import Sequence

from sqlalchemy import func, select

from app.core.config import get_settings
from app.core.db import get_session_factory
from app.core.queue import create_job_queue
from app.models.knowledge import KnowledgeChunk, KnowledgeSource
from app.providers.llm.factory import platform_provider
from app.services.knowledge import INDEX_JOB

STATUS_PENDING = "pending"


async def main(*, reindex_all: bool, dry_run: bool) -> int:
    settings = get_settings()
    # The model the *platform* embeds with. Never a tenant's own credential:
    # embeddings are always platform-funded, so one model covers the table.
    current = platform_provider(settings).embedding_model
    print(f"Modelo de embeddings actual: {current}")

    session_factory = get_session_factory()
    with session_factory() as session:
        stale = _stale_sources(session, current=current, everything=reindex_all)

        if not stale:
            print("Nada que reindexar: todo está bajo el modelo actual.")
            return 0

        by_model = _counts_by_model(session, current=current)
        print("\nFuentes por modelo de embedding:")
        for model, count in sorted(by_model.items()):
            mark = "  (actual)" if model == current else "  <- se reindexa"
            print(f"  {model:32} {count:>4}{mark}")

        print(f"\n{len(stale)} fuente(s) a reindexar.")
        if dry_run:
            print("--dry-run: no se cambió nada.")
            return 0

        # Reset first, then enqueue. `claim_for_indexing` refuses a source
        # that is already `indexed`, so enqueueing without this is a silent
        # no-op - the job runs, declines the work and reports success.
        for _tenant_id, source_id in stale:
            source = session.get(KnowledgeSource, source_id)
            if source is None:
                continue
            source.status = STATUS_PENDING
            source.error_msg = None
        session.commit()
        print("Estados reiniciados a 'pending'.")

    queue = await create_job_queue(settings)
    try:
        for tenant_id, source_id in stale:
            await queue.enqueue(INDEX_JOB, str(tenant_id), str(source_id))
    finally:
        await queue.close()

    print(f"{len(stale)} trabajo(s) encolados. El worker los va a procesar.")
    print("Sigue el avance con: docker logs -f leo-dev-worker-1")
    return 0


def _stale_sources(
    session: object, *, current: str, everything: bool
) -> Sequence[tuple[uuid.UUID, uuid.UUID]]:
    """Sources whose chunks were not produced by the current model, or predate 006.

    A source with no chunks at all counts as stale: it either failed or was
    never indexed, and in both cases running it again is the right answer.
    """
    from sqlalchemy.orm import Session

    assert isinstance(session, Session)

    statement = select(KnowledgeSource.tenant_id, KnowledgeSource.id).where(
        KnowledgeSource.deleted_at.is_(None)
    )
    if not everything:
        embedded_by_current = (
            select(KnowledgeChunk.source_id)
            .where(KnowledgeChunk.embedding_model == current)
            .distinct()
        )
        # Chunked before Feature 006 (no `content_hash`): re-chunked with the
        # dedup and the hash the search relies on.
        unhashed = (
            select(KnowledgeChunk.source_id).where(KnowledgeChunk.content_hash.is_(None)).distinct()
        )
        statement = statement.where(
            KnowledgeSource.id.not_in(embedded_by_current) | KnowledgeSource.id.in_(unhashed)
        )

    return [(row[0], row[1]) for row in session.execute(statement).all()]


def _counts_by_model(session: object, *, current: str) -> dict[str, int]:
    from sqlalchemy.orm import Session

    assert isinstance(session, Session)

    rows = session.execute(
        select(
            KnowledgeChunk.embedding_model, func.count(func.distinct(KnowledgeChunk.source_id))
        ).group_by(KnowledgeChunk.embedding_model)
    ).all()
    # `None` means the row predates the column, so nothing recorded what
    # produced it. Shown as unknown rather than as the string "None",
    # which reads like a model name.
    counts = {(str(model) if model else "(desconocido)"): int(count) for model, count in rows}

    orphans = session.execute(
        select(func.count())
        .select_from(KnowledgeSource)
        .where(
            KnowledgeSource.deleted_at.is_(None),
            KnowledgeSource.id.not_in(select(KnowledgeChunk.source_id).distinct()),
        )
    ).scalar_one()
    if orphans:
        counts["(sin chunks)"] = int(orphans)
    return counts


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--all",
        dest="reindex_all",
        action="store_true",
        help="Reindexar todo, incluso lo que ya está bajo el modelo actual.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Mostrar qué se haría, sin tocar nada.",
    )
    args = parser.parse_args()
    raise SystemExit(asyncio.run(main(reindex_all=args.reindex_all, dry_run=args.dry_run)))
