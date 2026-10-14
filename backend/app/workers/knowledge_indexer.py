"""`knowledge_indexer`: turns an accepted source into searchable chunks.

    pending -> indexing -> indexed
                        -> failed (error_msg says why)

Every exit from this pipeline writes one of those two terminal states. There
is no path that leaves a source stuck in `indexing`, and no path that reaches
`indexed` with zero chunks - "the upload worked, the agent knows nothing" is
the failure mode this module exists to prevent (Constitution: never fail in
silence).

The ARQ entry point is a thin shell. The real work is `index_source`, which
takes its session and its providers as arguments, so an integration test can
drive the exact production pipeline inside a rolled-back transaction with
`MockLLMProvider` and `MockStorageProvider`, and without Redis.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from typing import Any

from sqlalchemy.orm import Session

from app.core.config import Settings, get_settings
from app.models.knowledge import KnowledgeSource
from app.providers.llm.base import LLMError, LLMProvider
from app.providers.pdf.base import PdfError, PdfTextExtractor
from app.providers.storage.base import StorageError, StorageProvider
from app.repositories.knowledge import (
    STATUS_FAILED,
    STATUS_INDEXED,
    KnowledgeChunkRepository,
    KnowledgeSourceRepository,
)
from app.repositories.tenant import TenantRepository
from app.services.chunking import chunk_text, dedupe
from app.services.knowledge import PDF_TYPE, KnowledgeError, KnowledgeService
from app.services.trial import billing_enforced

logger = logging.getLogger(__name__)

# Outcomes that are not a state transition, reported for observability only.
OUTCOME_MISSING = "missing"
OUTCOME_DELETED = "deleted"
OUTCOME_NOT_CLAIMABLE = "not_claimable"

EMPTY_TEXT_MESSAGE = "This source contains no text to index. Add content and upload it again."
NO_CHUNKS_MESSAGE = (
    "The text could not be split into anything indexable. Check the document's content."
)
KEPT_PREVIOUS = "El asistente sigue usando la versión anterior."
UNEXPECTED_MESSAGE = (
    "Indexing failed unexpectedly. The platform has been notified; try uploading again."
)


@dataclass(frozen=True)
class IndexingOutcome:
    """What one run of the pipeline did. Returned for logs and tests."""

    source_id: uuid.UUID
    status: str
    chunks: int = 0
    error_msg: str | None = None

    @property
    def succeeded(self) -> bool:
        return self.status == STATUS_INDEXED


@dataclass(frozen=True)
class _ExtractionFailure:
    """No usable text, and the sentence explaining that to the tenant."""

    reason: str


async def index_source(
    session: Session,
    tenant_id: uuid.UUID,
    source_id: uuid.UUID,
    *,
    llm: LLMProvider,
    storage: StorageProvider,
    pdf_extractor: PdfTextExtractor,
    settings: Settings | None = None,
) -> IndexingOutcome:
    """Extract, chunk, embed and store one knowledge source.

    Never raises for an unusable document: a scanned PDF, a source over the
    plan budget or a provider outage all end as `failed` with a message the
    tenant can act on. Raising would only hand the job back to ARQ to retry
    something that will fail identically forever.
    """
    settings = settings or get_settings()
    sources = KnowledgeSourceRepository(session, tenant_id)

    source = sources.get(source_id)
    if source is None:
        # Tenant-scoped read, so this also covers "the job named another
        # tenant's source". Nothing is written either way.
        logger.warning(
            "indexing job for an unknown source",
            extra={"tenant_id": str(tenant_id), "source_id": str(source_id)},
        )
        return IndexingOutcome(source_id, OUTCOME_MISSING)

    if source.deleted_at is not None:
        return IndexingOutcome(source_id, OUTCOME_DELETED)

    if not sources.claim_for_indexing(source_id):
        # Already `indexing` or `indexed`: another worker owns it. Doing the
        # work anyway would double the tenant's embedding bill.
        return IndexingOutcome(source_id, OUTCOME_NOT_CLAIMABLE)
    session.commit()

    try:
        extracted = _extract(source, storage=storage, pdf_extractor=pdf_extractor)
        if isinstance(extracted, _ExtractionFailure):
            return _fail(session, sources, source_id, extracted.reason)
        return await _index_extracted_text(
            session, tenant_id, source_id, extracted, llm=llm, settings=settings
        )
    except Exception:
        # The source has already been moved to `indexing`. Letting anything
        # escape here would strand it in that state with no error message and
        # no way for the tenant to retry.
        logger.exception(
            "unexpected failure while indexing a knowledge source",
            extra={"tenant_id": str(tenant_id), "source_id": str(source_id)},
        )
        session.rollback()
        return _fail(session, sources, source_id, UNEXPECTED_MESSAGE)


def _extract(
    source: KnowledgeSource,
    *,
    storage: StorageProvider,
    pdf_extractor: PdfTextExtractor,
) -> str | _ExtractionFailure:
    """Get the source's text, or the reason there is none."""
    if source.type != PDF_TYPE:
        content = (source.content or "").strip()
        return content if content else _ExtractionFailure(EMPTY_TEXT_MESSAGE)

    if not source.file_key:
        return _ExtractionFailure("The uploaded file is missing from storage.")

    try:
        data = storage.get(source.file_key)
    except StorageError as exc:
        logger.exception("could not read a knowledge PDF from storage")
        return _ExtractionFailure(f"The uploaded file could not be read from storage: {exc}")

    try:
        text = pdf_extractor.extract_text(data)
    except PdfError as exc:
        # The scanned-menu case, and the reason `PdfWithoutTextError` is a
        # distinct type: the message tells the tenant what to do instead.
        return _ExtractionFailure(str(exc))

    stripped = text.strip()
    return stripped if stripped else _ExtractionFailure(EMPTY_TEXT_MESSAGE)


async def _index_extracted_text(
    session: Session,
    tenant_id: uuid.UUID,
    source_id: uuid.UUID,
    text: str,
    *,
    llm: LLMProvider,
    settings: Settings,
) -> IndexingOutcome:
    """Budget-check, chunk, embed and persist. The half that can be slow."""
    sources = KnowledgeSourceRepository(session, tenant_id)

    refusal = _reserve_characters(session, tenant_id, source_id, len(text))
    if refusal is not None:
        return _fail(session, sources, source_id, refusal)

    # A repeated block (a footer on every page, the same FAQ pasted twice) is
    # embedded once (Feature 006, RF-003).
    chunks = dedupe(
        chunk_text(
            text,
            max_chars=settings.knowledge_chunk_chars,
            overlap=settings.knowledge_chunk_overlap_chars,
        )
    )
    if not chunks:  # pragma: no cover - non-empty text always yields a chunk
        return _fail(session, sources, source_id, NO_CHUNKS_MESSAGE)

    try:
        embeddings = await _embed_all(llm, chunks, batch_size=settings.knowledge_embed_batch_size)
    except LLMError as exc:
        logger.exception("embedding failed while indexing a knowledge source")
        session.rollback()
        return _fail(
            session,
            sources,
            source_id,
            f"The embedding provider could not process this document: {exc}",
        )

    # The tenant may have deleted the source while it was being embedded.
    # Writing the chunks now would resurrect a document they removed.
    current = sources.get(source_id)
    if current is None or current.deleted_at is not None:
        session.rollback()
        return IndexingOutcome(source_id, OUTCOME_DELETED)

    KnowledgeChunkRepository(session, tenant_id).replace_for_source(
        source_id,
        list(zip(chunks, embeddings, strict=True)),
        # Stamped so a later profile change can find these rows again. Read
        # from the provider that just embedded them, never from settings: the
        # two can disagree, and the vector is what has to be described.
        embedding_model=llm.embedding_model,
    )
    sources.mark_indexed(source_id)
    session.commit()

    logger.info(
        "knowledge source indexed",
        extra={
            "tenant_id": str(tenant_id),
            "source_id": str(source_id),
            "chunks": len(chunks),
        },
    )
    return IndexingOutcome(source_id, STATUS_INDEXED, chunks=len(chunks))


def _reserve_characters(
    session: Session,
    tenant_id: uuid.UUID,
    source_id: uuid.UUID,
    char_count: int,
) -> str | None:
    """Commit the real size of the source, or explain why it does not fit.

    The second half of the plan-limit enforcement, using the same tenant row
    lock the upload endpoint uses. Only here is a PDF's character count
    known, so only here can the budget be applied exactly - and it happens
    *before* a single embedding is requested, so an over-budget document
    costs nothing.

    `exclude_source_id` keeps this source's own stored `char_count` out of
    the "already used" figure; otherwise a re-index would count the document
    against itself and a large document could never be re-indexed.
    """
    sources = KnowledgeSourceRepository(session, tenant_id)

    try:
        TenantRepository(session).lock(tenant_id)
        chars_limit = KnowledgeService(session, tenant_id).usage().chars_limit
    except KnowledgeError as exc:
        session.rollback()
        return f"The plan's knowledge limits could not be read: {exc}"

    used_by_others = sources.total_chars(exclude_source_id=source_id)
    # Billing off: the budget is shown, not enforced.
    if billing_enforced() and used_by_others + char_count > chars_limit:
        session.rollback()
        return (
            f"This document holds {char_count} characters and the plan allows "
            f"{chars_limit} in total, of which {used_by_others} are already used. "
            "Delete a source or upgrade the plan and upload it again."
        )

    sources.set_char_count(source_id, char_count)
    session.commit()
    return None


async def _embed_all(llm: LLMProvider, chunks: list[str], *, batch_size: int) -> list[list[float]]:
    """Embed every chunk as a `passage`, in bounded batches.

    `input_type="passage"` is the stored side of the asymmetric pair;
    `search_knowledge` embeds the question as a `query`. Getting these the
    same way is not an error anyone sees - retrieval just gets worse.
    """
    vectors: list[list[float]] = []
    size = max(batch_size, 1)
    for start in range(0, len(chunks), size):
        batch = chunks[start : start + size]
        vectors.extend(await llm.embed(batch, input_type="passage"))
    if len(vectors) != len(chunks):  # pragma: no cover - provider contract violation
        raise LLMError(
            f"embedding provider returned {len(vectors)} vectors for {len(chunks)} chunks"
        )
    return vectors


def _fail(
    session: Session,
    sources: KnowledgeSourceRepository,
    source_id: uuid.UUID,
    reason: str,
) -> IndexingOutcome:
    """Record a terminal failure with its reason, and commit it.

    A source that was already indexed (an edit, a re-read website) keeps its old
    chunks: the agent goes on with the previous version, and the reason says so.
    """
    if KnowledgeChunkRepository(session, sources.tenant_id).count_for_source(source_id):
        reason = f"{reason} {KEPT_PREVIOUS}"
    sources.mark_failed(source_id, reason)
    session.commit()
    logger.warning(
        "knowledge source failed to index",
        extra={"source_id": str(source_id), "reason": reason},
    )
    return IndexingOutcome(source_id, STATUS_FAILED, error_msg=reason)


async def index_knowledge_source(ctx: dict[str, Any], tenant_id: str, source_id: str) -> str:
    """ARQ entry point. Registered in `app/workers/settings.py`.

    Arguments are strings because job payloads are serialised; parsing them
    here means a malformed job fails loudly at the boundary rather than
    somewhere inside a query.
    """
    tenant = uuid.UUID(tenant_id)
    source = uuid.UUID(source_id)

    session_factory = ctx["session_factory"]
    with session_factory() as session:
        outcome = await index_source(
            session,
            tenant,
            source,
            llm=ctx["llm"],
            storage=ctx["storage"],
            pdf_extractor=ctx["pdf_extractor"],
            settings=ctx.get("settings"),
        )
    return outcome.status
