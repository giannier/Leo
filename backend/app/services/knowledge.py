"""Knowledge ingestion and retrieval (TASK-012).

Three responsibilities, and the reasons they are shaped the way they are:

**Ingestion is accepted, not performed.** The endpoint answers `202` and a
worker does the reading, chunking and embedding. Extracting a 10 MB PDF and
calling an embedding API inside an HTTP request would hold a connection for
tens of seconds and lose the whole upload to any client timeout.

**Plan limits are checked under a lock, before anything is indexed.** The
budgets live in the `plans` row (`max_rag_chars`, `max_knowledge_files`),
never in code - Clarification 011 sets trial to 500 K characters and 20
files, and the other tiers differ. The check is a read followed by a write,
so it is serialised per tenant on the tenant row; see `_reserve`.

**A PDF's size is not known when it is accepted.** Character count only
exists after extraction, which happens in the worker. So the upload path
enforces what it can prove (file count, per-file bytes, that the tenant has
any character budget left at all) and the worker enforces the exact
character budget under the same lock before it embeds anything. A document
that would blow the budget ends `failed` with the numbers in `error_msg`,
never silently truncated.
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from urllib.parse import urlsplit

from sqlalchemy.orm import Session

from app.core.config import Settings, get_settings
from app.core.queue import JobQueue, QueueError
from app.models.knowledge import KnowledgeSource
from app.providers.llm.base import LLMProvider
from app.providers.storage.base import StorageError, StorageProvider
from app.repositories.knowledge import (
    STATUS_INDEXING,
    STATUS_PENDING,
    KnowledgeChunkRepository,
    KnowledgeSourceRepository,
)
from app.repositories.plan import PlanRepository
from app.repositories.tenant import TenantRepository
from app.services.chunking import content_hash
from app.services.trial import billing_enforced
from app.services.web_crawl import normalise_start

logger = logging.getLogger(__name__)

# Source types whose content arrives inline as JSON.
INLINE_TYPES = ("faq", "policy", "text")
PDF_TYPE = "pdf"
WEB_TYPE = "web"

# Which source the agent trusts when two disagree (006 C-003): what the business
# wrote for the agent first, then its documents, then its website. 0-100.
DEFAULT_PRIORITY = {"faq": 100, "policy": 100, "text": 100, "pdf": 90, WEB_TYPE: 50}

PDF_CONTENT_TYPE = "application/pdf"
# Every PDF starts with this signature. Checking it costs nothing and stops
# an obviously wrong upload before it reaches object storage or the queue.
PDF_MAGIC = b"%PDF-"

# Name the worker is registered under in `app/workers/settings.py`. A string
# rather than an import so the API process never loads the worker module.
INDEX_JOB = "index_knowledge_source"

MAX_TITLE_LENGTH = 200


@dataclass(frozen=True)
class KnowledgeUsage:
    """What the tenant has consumed of their plan's RAG budget."""

    chars_used: int
    chars_limit: int
    files_used: int
    files_limit: int

    @property
    def chars_remaining(self) -> int:
        return max(self.chars_limit - self.chars_used, 0)

    @property
    def files_remaining(self) -> int:
        return max(self.files_limit - self.files_used, 0)

    def as_fields(self) -> dict[str, str]:
        """Usage rendered for the error envelope's `fields`.

        The contract requires the current usage to travel with the `413`, and
        `fields` is the only free-form slot the shared envelope offers, so the
        numbers go there as strings rather than by widening the error shape
        every endpoint depends on.
        """
        return {
            "chars_used": str(self.chars_used),
            "chars_limit": str(self.chars_limit),
            "files_used": str(self.files_used),
            "files_limit": str(self.files_limit),
        }


@dataclass(frozen=True)
class IngestResult:
    """What the endpoint answers with, captured before the commit."""

    id: uuid.UUID
    status: str


@dataclass(frozen=True)
class KnowledgeHit:
    """One retrieved chunk, ready for the agent's prompt."""

    chunk_id: uuid.UUID
    source_id: uuid.UUID
    content: str


@dataclass(frozen=True)
class Passage:
    """A retrieved chunk with the source it comes from (Feature 006)."""

    content: str
    source_id: uuid.UUID
    title: str
    source_type: str
    priority: int
    distance: float


class KnowledgeError(Exception):
    """Base class for everything that can stop an ingestion."""


class UnsupportedSourceTypeError(KnowledgeError):
    """The requested `type` is not one this endpoint accepts."""


class EmptyContentError(KnowledgeError):
    """Nothing to index."""


class InvalidUploadError(KnowledgeError):
    """The uploaded bytes are not a PDF."""


class PdfTooLargeError(KnowledgeError):
    """The file is over the per-file ceiling (Clarification 011: 10 MB)."""

    def __init__(self, size: int, limit: int) -> None:
        super().__init__(f"{size} bytes exceeds the {limit} byte limit")
        self.size = size
        self.limit = limit


class RagLimitExceededError(KnowledgeError):
    """The plan's aggregate budget is exhausted (`413 rag_limit_exceeded`)."""

    def __init__(self, usage: KnowledgeUsage, *, reason: str, message: str) -> None:
        super().__init__(message)
        self.usage = usage
        # 'chars' or 'files' - which budget ran out.
        self.reason = reason
        self.message = message


class PlanUnavailableError(KnowledgeError):
    """The tenant's plan row is missing, so no limit can be read.

    A deployment problem, not a client one: refusing to guess a limit is the
    only safe answer, because guessing high spends money that was never
    budgeted.
    """


class SourceNotFoundError(KnowledgeError):
    """No such source for this tenant. Never distinguished from 'foreign'."""


class SourceBusyError(KnowledgeError):
    """The source is being indexed right now; an edit would race the worker."""


class IndexingUnavailableError(KnowledgeError):
    """The job could not be queued, so nothing will ever index it."""


class KnowledgeService:
    """Knowledge base operations for one tenant.

    Owns its transaction, like `AuthService`: each public method commits the
    unit of work it completes or leaves the session rolled back.
    """

    def __init__(
        self,
        session: Session,
        tenant_id: uuid.UUID,
        *,
        storage: StorageProvider | None = None,
        queue: JobQueue | None = None,
        settings: Settings | None = None,
    ) -> None:
        self._session = session
        self._tenant_id = tenant_id
        self._storage = storage
        self._queue = queue
        self._settings = settings or get_settings()
        self._sources = KnowledgeSourceRepository(session, tenant_id)
        self._chunks = KnowledgeChunkRepository(session, tenant_id)

    # -------------------------------------------------------------- reading

    def usage(self) -> KnowledgeUsage:
        """Current consumption against the tenant's plan."""
        chars_limit, files_limit = self._plan_limits()
        return KnowledgeUsage(
            chars_used=self._sources.total_chars(),
            chars_limit=chars_limit,
            files_used=self._sources.active_file_count(),
            files_limit=files_limit,
        )

    def list_sources(self) -> Sequence[KnowledgeSource]:
        """Everything the tenant still owns, newest first."""
        return self._sources.list_active()

    # ------------------------------------------------------------ ingestion

    async def ingest_text(self, *, source_type: str, title: str, content: str) -> IngestResult:
        """Accept an inline faq/policy/text source for indexing."""
        if source_type not in INLINE_TYPES:
            raise UnsupportedSourceTypeError(
                f"{source_type!r} is not an inline source type; expected one of {INLINE_TYPES}"
            )
        body = content.strip()
        if not body:
            raise EmptyContentError("the source has no content to index")

        chars = len(body)
        # Exact reservation: the size is known here, so the budget can be
        # enforced completely before anything is queued.
        self._reserve(chars=chars)

        source = self._sources.add(
            type=source_type,
            title=_clean_title(title),
            content=body,
            char_count=chars,
            status=STATUS_PENDING,
            priority=DEFAULT_PRIORITY[source_type],
        )
        source_id = source.id
        self._session.commit()

        await self._enqueue(source_id)
        return IngestResult(id=source_id, status=STATUS_PENDING)

    async def ingest_pdf(self, *, title: str, data: bytes) -> IngestResult:
        """Accept a PDF upload for extraction and indexing."""
        if self._storage is None:  # pragma: no cover - wiring error, not a request
            raise RuntimeError("KnowledgeService needs a StorageProvider to accept PDFs")

        max_bytes = self._settings.knowledge_max_pdf_bytes
        if len(data) > max_bytes:
            raise PdfTooLargeError(len(data), max_bytes)
        if not data.startswith(PDF_MAGIC):
            raise InvalidUploadError("the uploaded file is not a PDF")

        # Stored before the reservation so the tenant row is not locked
        # across an object-store round trip. The key is a fresh UUID, so an
        # object orphaned by a later failure is inert rather than a collision
        # waiting to happen.
        key = self._object_key()
        self._storage.put(key, data, content_type=PDF_CONTENT_TYPE)

        try:
            # `chars=0`: how many characters this PDF holds is unknowable
            # until the worker extracts it. What can be enforced now is the
            # file count and that some character budget remains at all; the
            # worker enforces the exact figure before embedding.
            self._reserve(chars=0)
        except KnowledgeError:
            self._discard_object(key)
            raise

        source = self._sources.add(
            type=PDF_TYPE,
            title=_clean_title(title),
            file_key=key,
            char_count=0,
            status=STATUS_PENDING,
            priority=DEFAULT_PRIORITY[PDF_TYPE],
        )
        source_id = source.id
        self._session.commit()

        await self._enqueue(source_id)
        return IngestResult(id=source_id, status=STATUS_PENDING)

    # -------------------------------------------------------------- editing

    async def update(
        self,
        source_id: uuid.UUID,
        *,
        title: str | None = None,
        content: str | None = None,
        priority: int | None = None,
    ) -> KnowledgeSource:
        """Change a source; new text is re-indexed (Feature 006, HU-03).

        The old chunks keep answering until the worker swaps them for the new ones
        in one transaction (`replace_for_source`), so the agent never reads half a
        document. If re-indexing fails, the old version stays in use and the panel
        says so. A source being indexed right now is refused rather than raced.
        """
        source = self._sources.get(source_id)
        if source is None or source.deleted_at is not None:
            raise SourceNotFoundError(str(source_id))
        if title is not None:
            source.title = _clean_title(title)
        if priority is not None:
            source.priority = priority
        reindex = False
        if content is not None:
            if source.type not in INLINE_TYPES:
                raise UnsupportedSourceTypeError(
                    "only faq, policy and text sources are edited as text"
                )
            body = content.strip()
            if not body:
                raise EmptyContentError("the source has no content to index")
            if body != source.content:
                if source.status == STATUS_INDEXING:
                    self._session.rollback()
                    raise SourceBusyError(str(source_id))
                # Only the difference counts against the plan.
                self._reserve(chars=len(body) - source.char_count)
                source.content = body
                source.char_count = len(body)
                source.status = STATUS_PENDING
                source.error_msg = None
                reindex = True
        self._session.commit()
        if reindex:
            await self._enqueue(source_id, keep_on_failure=True)
        return source

    async def reindex(self, source_id: uuid.UUID) -> KnowledgeSource:
        """Process a source again: re-read a website, retry a failed document."""
        source = self._sources.get(source_id)
        if source is None or source.deleted_at is not None:
            raise SourceNotFoundError(str(source_id))
        if source.status in (STATUS_PENDING, STATUS_INDEXING):
            raise SourceBusyError(str(source_id))
        source.status = STATUS_PENDING
        source.error_msg = None
        self._session.commit()
        await self._enqueue(source_id, keep_on_failure=True)
        return source

    def chunks(self, source_id: uuid.UUID) -> Sequence[str]:
        """What the agent can retrieve from one source, in order (debug view)."""
        source = self._sources.get(source_id)
        if source is None or source.deleted_at is not None:
            raise SourceNotFoundError(str(source_id))
        return [chunk.content for chunk in self._chunks.list_for_source(source_id)]

    async def ingest_web(self, *, url: str, title: str | None = None) -> IngestResult:
        """Accept the business's website for reading (Feature 006, HU-02).

        Like a PDF, its size is unknown until the worker has read it: the file
        count and "some budget left" are enforced here, the characters there.
        """
        start = normalise_start(url)
        self._reserve(chars=0)
        source = self._sources.add(
            type=WEB_TYPE,
            title=_clean_title(title or (urlsplit(start).hostname or start)),
            source_url=start,
            char_count=0,
            status=STATUS_PENDING,
            priority=DEFAULT_PRIORITY[WEB_TYPE],
        )
        source_id = source.id
        self._session.commit()
        await self._enqueue(source_id)
        return IngestResult(id=source_id, status=STATUS_PENDING)

    # ------------------------------------------------------------- deletion

    def delete(self, source_id: uuid.UUID) -> None:
        """Retire a source and remove its embeddings.

        The chunks are hard-deleted, not soft-deleted: a vector left behind
        keeps being retrieved, so the agent would go on quoting a document
        the tenant believes they removed.

        The stored PDF object is deliberately left in place. The row is only
        soft-deleted, `file_key` still points at it, and purging tenant
        objects belongs to the retention job (TASK-019), not to a request.
        """
        if not self._sources.soft_delete(source_id):
            raise SourceNotFoundError(str(source_id))
        self._chunks.delete_for_source(source_id)
        self._session.commit()

    # ------------------------------------------------------------ internals

    def _plan_limits(self) -> tuple[int, int]:
        """`(max_rag_chars, max_knowledge_files)` from the tenant's plan."""
        tenant = TenantRepository(self._session).get(self._tenant_id)
        if tenant is None:
            raise PlanUnavailableError(f"tenant {self._tenant_id!s} does not exist")
        plan = PlanRepository(self._session).get(tenant.plan_id)
        if plan is None:
            raise PlanUnavailableError(
                f"tenant {self._tenant_id!s} is on plan {tenant.plan_id!r}, which has no row"
            )
        return plan.max_rag_chars, plan.max_knowledge_files

    def _reserve(self, *, chars: int) -> KnowledgeUsage:
        """Check the plan budget and hold it until the caller commits.

        The lock is the whole point. Reading "490 K used" and then inserting
        20 K is two statements; two uploads landing in the gap both see room
        and the tenant ends up at 510 K. `TenantRepository.lock` takes
        `SELECT ... FOR UPDATE` on the tenant row first, so the second
        transaction waits and then reads a total that already includes the
        first. The reservation *is* the inserted row - `total_chars()` sums
        `char_count` over live sources - so it becomes visible in the same
        commit that releases the lock, with no window in between.

        Rolls back before raising, so a refused upload never leaves the
        tenant row locked while the response is being written.
        """
        try:
            TenantRepository(self._session).lock(self._tenant_id)
            usage = self.usage()
            # Billing off: the budget is shown, not enforced.
            enforced = billing_enforced()

            if enforced and usage.files_used >= usage.files_limit:
                raise RagLimitExceededError(
                    usage,
                    reason="files",
                    message=(
                        f"This plan allows {usage.files_limit} knowledge files and "
                        f"{usage.files_used} are already in use. Delete one before "
                        "uploading another."
                    ),
                )

            projected = usage.chars_used + chars
            if enforced and projected > usage.chars_limit:
                raise RagLimitExceededError(
                    usage,
                    reason="chars",
                    message=(
                        f"This plan allows {usage.chars_limit} indexed characters, "
                        f"{usage.chars_used} are already used and this source adds "
                        f"{chars}. Delete a source or upgrade the plan."
                    ),
                )
            # A PDF reserves nothing up front, so "already at the limit" has
            # to be its own check: `0 > 0` is false and it would slip past.
            if enforced and chars == 0 and usage.chars_remaining == 0:
                raise RagLimitExceededError(
                    usage,
                    reason="chars",
                    message=(
                        f"This plan's {usage.chars_limit} indexed characters are fully "
                        "used. Delete a source or upgrade the plan before uploading."
                    ),
                )
        except KnowledgeError:
            self._session.rollback()
            raise
        return usage

    async def _enqueue(self, source_id: uuid.UUID, *, keep_on_failure: bool = False) -> None:
        """Hand the source to the indexing worker.

        A queue that cannot be reached is not something to swallow: the row
        is already committed and nothing would ever pick it up, so it would
        sit `pending` forever while occupying one of the plan's file slots.
        The row is retired and the caller is told (Constitution: never fail
        in silence).
        """
        if self._queue is None:  # pragma: no cover - wiring error, not a request
            raise RuntimeError("KnowledgeService needs a JobQueue to accept sources")
        try:
            await self._queue.enqueue(INDEX_JOB, str(self._tenant_id), str(source_id))
        except QueueError as exc:
            logger.exception(
                "knowledge source could not be queued for indexing",
                extra={"tenant_id": str(self._tenant_id), "source_id": str(source_id)},
            )
            if keep_on_failure:
                # An existing source keeps its old chunks; it is only marked.
                self._sources.mark_failed(
                    source_id, "No se pudo poner en cola. Vuelve a intentarlo en un momento."
                )
            else:
                self._sources.soft_delete(source_id)
            self._session.commit()
            raise IndexingUnavailableError(
                "The indexing queue is unavailable, so the source was not accepted."
            ) from exc

    def _object_key(self) -> str:
        """Where a tenant's PDF lives in object storage."""
        return f"tenants/{self._tenant_id!s}/knowledge/{uuid.uuid4().hex}.pdf"

    def _discard_object(self, key: str) -> None:
        """Best-effort cleanup of an object whose row was never written."""
        if self._storage is None:  # pragma: no cover - guarded by the caller
            return
        try:
            self._storage.delete(key)
        except StorageError:
            # Storage garbage is not worth failing the request over; the
            # tenant already has a real error to read.
            logger.warning("could not remove orphaned knowledge object %s", key)


def _clean_title(title: str) -> str:
    stripped = title.strip()
    if not stripped:
        raise EmptyContentError("the source needs a title")
    return stripped[:MAX_TITLE_LENGTH]


async def search_knowledge(
    session: Session,
    tenant_id: uuid.UUID,
    query: str,
    *,
    k: int | None = None,
    llm: LLMProvider,
) -> list[KnowledgeHit]:
    """Retrieve the chunks most similar to `query` for one tenant.

    Two details that are easy to get wrong and expensive to debug:

    **`input_type="query"`.** These embedding models are trained
    asymmetrically - a question and the passage that answers it are pushed
    into different regions on purpose. Embedding the query as a `passage`
    still returns results, ranked slightly wrong, which is the worst kind of
    bug: nothing breaks, retrieval just quietly gets worse.

    **The tenant filter runs before the ORDER BY.** That is enforced inside
    `KnowledgeChunkRepository.search`, and this function goes through it
    rather than building its own statement. Filtering afterwards would still
    return only this tenant's rows, so it looks correct, but a foreign chunk
    would consume the LIMIT first and the tenant would get nothing back.
    """
    text = query.strip()
    if not text:
        return []

    limit = k if k is not None else get_settings().knowledge_search_default_k
    if limit <= 0:
        return []

    vectors = await llm.embed([text], input_type="query")
    chunks = KnowledgeChunkRepository(session, tenant_id).search(vectors[0], limit=limit)
    return [
        KnowledgeHit(chunk_id=chunk.id, source_id=chunk.source_id, content=chunk.content)
        for chunk in chunks
    ]


def retrieve(
    session: Session, tenant_id: uuid.UUID, vector: Sequence[float], *, k: int
) -> list[Passage]:
    """The `k` passages closest to a question, one copy each, most trusted source first.

    Feature 006: the same paragraph in two sources (the FAQ and the website) is
    shown once, from the higher-priority source (C-101). The `k` closest are then
    ordered by the source's priority, so when two passages disagree the one the
    business trusts more comes first and the tool says that the first one wins
    (C-003). Distance breaks ties.
    """
    if k <= 0:
        return []
    rows = KnowledgeChunkRepository(session, tenant_id).search_with_sources(vector, limit=k * 3)
    best: dict[str, Passage] = {}
    order: list[str] = []
    for chunk, source, distance in rows:
        digest = chunk.content_hash or content_hash(chunk.content)
        passage = Passage(
            content=chunk.content,
            source_id=source.id,
            title=source.title,
            source_type=source.type,
            priority=source.priority,
            distance=distance,
        )
        kept = best.get(digest)
        if kept is None:
            best[digest] = passage
            order.append(digest)
        elif passage.priority > kept.priority:
            best[digest] = passage
    closest = [best[digest] for digest in order][:k]
    return sorted(closest, key=lambda p: (-p.priority, p.distance))
