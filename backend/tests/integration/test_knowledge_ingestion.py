"""End-to-end tests for TASK-012: knowledge ingestion, indexing and RAG.

Every test drives the real HTTP endpoints and the real worker pipeline
against the real database inside a rolled-back transaction. The LLM, the
object store and the job queue are the project's mocks, so no network is
needed - but the SQL, the pgvector column, the plan limits and the status
machine are all genuine.

Authentication is real too: the caller is registered, verified and signed in
through `/auth/*`, and the tenant every assertion is about is the one their
access token resolves to. Nothing here overrides an authentication
dependency, because the property most worth protecting - that a knowledge
base can only be reached by a member of the business that owns it - would
be the first casualty of a stubbed tenant.

The three verification criteria from `tasks.md` have a test each:

* ingestion through to `indexed`             -> `TestIngestionReachesIndexed`
* a PDF with no text -> `failed` + message   -> `TestAScannedPdfFails`
* exceeding the limit -> `413` with usage    -> `TestPlanLimits`
"""

from __future__ import annotations

import re
import uuid
from collections.abc import AsyncIterator, Iterator
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from urllib.parse import parse_qs, urlparse

import pytest
from fastapi import FastAPI
from httpx import AsyncClient
from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from app.api.deps import get_job_queue, get_storage_provider
from app.core.config import get_settings
from app.core.queue import QueueError
from app.core.tokens import issue_access_token
from app.models import KnowledgeChunk, KnowledgeSource, Plan, Tenant, TenantUser, User
from app.providers.email.mock import MockEmailProvider
from app.providers.llm import MockLLMProvider
from app.providers.pdf import MockPdfTextExtractor, PdfWithoutTextError, PypdfTextExtractor
from app.providers.storage import MockStorageProvider
from app.repositories.knowledge import KnowledgeSourceRepository
from app.services.knowledge import INDEX_JOB, search_knowledge
from app.workers.knowledge_indexer import (
    OUTCOME_DELETED,
    OUTCOME_MISSING,
    OUTCOME_NOT_CLAIMABLE,
    IndexingOutcome,
    index_source,
)

SETTINGS = get_settings()
KNOWLEDGE = f"{SETTINGS.api_v1_prefix}/tenant/knowledge"
AUTH = f"{SETTINGS.api_v1_prefix}/auth"
PASSWORD = "una-clave-suficientemente-larga"

_HREF = re.compile(r'href="([^"]+)"')

MENU = (
    "Menu del dia: entrada, segundo y refresco por S/ 18.\n\n"
    "Delivery a Miraflores y San Isidro con recargo de S/ 5.\n\n"
    "Atendemos de lunes a sabado, de 12:00 a 22:00."
)

# A page that draws a filled rectangle and nothing else: a valid PDF with no
# text layer, which is what a scanned menu looks like to a parser.
_IMAGE_ONLY_STREAM = b"0.2 0.4 0.6 rg 72 72 468 648 re f"


# ------------------------------------------------------------------ fixtures


class RecordingQueue:
    """A `JobQueue` that records instead of talking to Redis."""

    def __init__(self) -> None:
        self.jobs: list[tuple[str, tuple[object, ...]]] = []
        self.broken = False

    async def enqueue(self, function: str, *args: object) -> str | None:
        if self.broken:
            raise QueueError("redis is unreachable")
        self.jobs.append((function, args))
        return f"job-{len(self.jobs)}"

    @property
    def last_source_id(self) -> uuid.UUID:
        return uuid.UUID(str(self.jobs[-1][1][1]))


@pytest.fixture
def plan(db: Session) -> Iterator[Plan]:
    """A plan row this test owns, so limits can be tightened at will.

    Deliberately not the seeded `trial` row: a test that shrinks a shared
    row would be editing production reference data, and the limits under test
    have to come from the table rather than from a constant anyway.
    """
    row = Plan(
        id=f"test-{uuid.uuid4().hex[:12]}",
        name="Test plan",
        max_seats=2,
        max_ai_messages=250,
        max_rag_chars=500_000,
        max_knowledge_files=20,
    )
    db.add(row)
    db.flush()
    yield row


@dataclass(frozen=True)
class Account:
    """A real, verified, signed-in owner, and the tenant they own."""

    user_id: uuid.UUID
    tenant_id: uuid.UUID
    email: str
    access_token: str


@pytest.fixture
async def account(api: AsyncClient, db: Session, mail: MockEmailProvider, plan: Plan) -> Account:
    """The caller behind every request in this module."""
    return await _register_verified_owner(api, db, mail, plan, "Restaurante de prueba")


@pytest.fixture
def tenant(account: Account) -> uuid.UUID:
    """The tenant the caller's access token resolves to."""
    return account.tenant_id


@pytest.fixture
def other_tenant(db: Session, plan: Plan) -> uuid.UUID:
    """A neighbouring business, with nobody signed in to it."""
    row = Tenant(name="Otro negocio", plan_id=plan.id)
    db.add(row)
    db.flush()
    return row.id


@pytest.fixture
def storage() -> MockStorageProvider:
    return MockStorageProvider()


@pytest.fixture
def queue() -> RecordingQueue:
    return RecordingQueue()


@pytest.fixture
def llm() -> MockLLMProvider:
    return MockLLMProvider()


@pytest.fixture
async def knowledge(
    app: FastAPI,
    api: AsyncClient,
    account: Account,
    storage: MockStorageProvider,
    queue: RecordingQueue,
) -> AsyncIterator[AsyncClient]:
    """`api`, signed in as the owner, with S3 and Redis replaced by mocks.

    Only the two infrastructure dependencies are overridden. The tenant comes
    from the access token, through the same chain production uses.
    """
    app.dependency_overrides[get_storage_provider] = lambda: storage
    app.dependency_overrides[get_job_queue] = lambda: queue
    api.headers.update(_bearer(account.access_token))
    yield api


# ------------------------------------------------------------------- helpers


def _bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _token_from(message: Any) -> str:
    for href in _HREF.findall(message.html):
        query = parse_qs(urlparse(href).query)
        if "token" in query:
            return str(query["token"][0])
    raise AssertionError("no verification link in the email body")


async def _register_verified_owner(
    api: AsyncClient, db: Session, mail: MockEmailProvider, plan: Plan, business: str
) -> Account:
    """Register, verify and sign in through the real `/auth` endpoints.

    The new tenant is then repointed at the test's own plan row. Registration
    always opens a trial, and a case that tightens `max_rag_chars` must not be
    editing the seeded `trial` limits that the rest of the suite reads.
    """
    email = f"duena-{uuid.uuid4().hex[:12]}@example.com"
    created = await api.post(
        f"{AUTH}/register",
        json={
            "full_name": "Ana Quispe",
            "email": email,
            "password": PASSWORD,
            "business_name": business,
        },
    )
    assert created.status_code == 201, created.text
    verified = await api.post(f"{AUTH}/verify-email", json={"token": _token_from(mail.last)})
    assert verified.status_code == 204, verified.text

    tenant_id = uuid.UUID(created.json()["tenant_id"])
    db.execute(update(Tenant).where(Tenant.id == tenant_id).values(plan_id=plan.id))
    db.flush()

    session = await api.post(f"{AUTH}/login", json={"email": email, "password": PASSWORD})
    assert session.status_code == 200, session.text
    return Account(
        user_id=uuid.UUID(created.json()["user_id"]),
        tenant_id=tenant_id,
        email=email,
        access_token=session.json()["access_token"],
    )


def _set_role(db: Session, account: Account, role: str) -> None:
    """Change the caller's role in their own tenant.

    The role is read from the database on every request, so this takes effect
    on the next call without re-issuing the token.
    """
    db.execute(
        update(TenantUser)
        .where(TenantUser.tenant_id == account.tenant_id, TenantUser.user_id == account.user_id)
        .values(role=role)
    )
    db.flush()


def _pdf(*streams: bytes) -> bytes:
    """A minimal, valid PDF drawing the given content streams."""
    page_numbers = [3 + index * 2 for index in range(len(streams))]
    kids = " ".join(f"{number} 0 R" for number in page_numbers)
    font_number = 2 + len(streams) * 2 + 1

    objects: list[bytes] = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        f"<< /Type /Pages /Kids [{kids}] /Count {len(streams)} >>".encode(),
    ]
    for index, stream in enumerate(streams):
        objects.append(
            f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
            f"/Resources << /Font << /F1 {font_number} 0 R >> >> "
            f"/Contents {4 + index * 2} 0 R >>".encode()
        )
        objects.append(f"<< /Length {len(stream)} >>\nstream\n".encode() + stream + b"\nendstream")
    objects.append(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>")

    out = bytearray(b"%PDF-1.4\n")
    offsets: list[int] = []
    for number, body in enumerate(objects, start=1):
        offsets.append(len(out))
        out += f"{number} 0 obj\n".encode() + body + b"\nendobj\n"
    xref_at = len(out)
    size = len(objects) + 1
    out += f"xref\n0 {size}\n".encode() + b"0000000000 65535 f \n"
    for offset in offsets:
        out += f"{offset:010d} 00000 n \n".encode()
    out += f"trailer\n<< /Size {size} /Root 1 0 R >>\nstartxref\n{xref_at}\n%%EOF\n".encode()
    return bytes(out)


def _text_pdf(text: str) -> bytes:
    return _pdf(f"BT /F1 24 Tf 72 700 Td ({text}) Tj ET".encode("latin-1"))


def _scanned_pdf() -> bytes:
    return _pdf(_IMAGE_ONLY_STREAM)


async def _post_text(client: AsyncClient, **overrides: Any) -> Any:
    body: dict[str, Any] = {"type": "faq", "title": "Preguntas frecuentes", "content": MENU}
    body.update(overrides)
    return await client.post(KNOWLEDGE, json=body)


async def _post_pdf(client: AsyncClient, data: bytes, *, title: str | None = None) -> Any:
    files = {"file": ("menu.pdf", data, "application/pdf")}
    form = {"title": title} if title is not None else None
    return await client.post(KNOWLEDGE, files=files, data=form)


async def _run_worker(
    db: Session,
    tenant_id: uuid.UUID,
    source_id: uuid.UUID,
    *,
    llm: MockLLMProvider,
    storage: MockStorageProvider,
    pdf: Any = None,
) -> IndexingOutcome:
    """Drive the production pipeline exactly as the ARQ job would."""
    return await index_source(
        db,
        tenant_id,
        source_id,
        llm=llm,
        storage=storage,
        pdf_extractor=pdf or PypdfTextExtractor(),
    )


def _source(db: Session, source_id: uuid.UUID) -> KnowledgeSource:
    db.expire_all()
    row = db.get(KnowledgeSource, source_id)
    assert row is not None
    return row


def _chunk_count(db: Session, source_id: uuid.UUID) -> int:
    statement = (
        select(func.count())
        .select_from(KnowledgeChunk)
        .where(KnowledgeChunk.source_id == source_id)
    )
    return int(db.execute(statement).scalar_one())


def _source_count(db: Session, tenant_id: uuid.UUID) -> int:
    """Sources belonging to one tenant.

    Scoped on purpose. Counting the whole table asserted "nothing anywhere in
    the database", which is only true of a database nobody has touched - it
    broke as soon as the end-to-end suite, or a person clicking around the
    panel, left a row behind. The property under test is that the *rejected
    upload* created nothing, and that is about this tenant.
    """
    statement = (
        select(func.count())
        .select_from(KnowledgeSource)
        .where(KnowledgeSource.tenant_id == tenant_id)
    )
    return int(db.execute(statement).scalar_one())


# --------------------------------------------------------------------- tests


class TestIngestionReachesIndexed:
    """tasks.md verification 1: integration test of ingestion and indexing."""

    async def test_a_text_source_is_accepted_as_pending(
        self, knowledge: AsyncClient, db: Session, queue: RecordingQueue
    ) -> None:
        response = await _post_text(knowledge)

        assert response.status_code == 202
        body = response.json()
        assert body["status"] == "pending"

        source = _source(db, uuid.UUID(body["id"]))
        assert source.type == "faq"
        assert source.status == "pending"
        # Counted at acceptance: the size of an inline source is known, so
        # the plan budget can be enforced completely before anything queues.
        assert source.char_count == len(MENU)

    async def test_acceptance_queues_the_indexing_job(
        self, knowledge: AsyncClient, queue: RecordingQueue, tenant: uuid.UUID
    ) -> None:
        response = await _post_text(knowledge)

        assert len(queue.jobs) == 1
        function, args = queue.jobs[0]
        assert function == INDEX_JOB
        assert args == (str(tenant), response.json()["id"])

    async def test_the_worker_indexes_it_and_stores_chunks(
        self,
        knowledge: AsyncClient,
        db: Session,
        queue: RecordingQueue,
        tenant: uuid.UUID,
        llm: MockLLMProvider,
        storage: MockStorageProvider,
    ) -> None:
        await _post_text(knowledge)
        source_id = queue.last_source_id

        outcome = await _run_worker(db, tenant, source_id, llm=llm, storage=storage)

        assert outcome.succeeded
        assert outcome.chunks >= 1
        assert _source(db, source_id).status == "indexed"
        assert _chunk_count(db, source_id) == outcome.chunks

    async def test_chunks_are_embedded_as_passages(
        self,
        knowledge: AsyncClient,
        db: Session,
        queue: RecordingQueue,
        tenant: uuid.UUID,
        llm: MockLLMProvider,
        storage: MockStorageProvider,
    ) -> None:
        """Asymmetric retrieval: stored text is a `passage`, questions are not."""
        await _post_text(knowledge)
        await _run_worker(db, tenant, queue.last_source_id, llm=llm, storage=storage)

        assert llm.embed_calls
        assert all(call.input_type == "passage" for call in llm.embed_calls)

    async def test_an_indexed_source_is_retrievable(
        self,
        knowledge: AsyncClient,
        db: Session,
        queue: RecordingQueue,
        tenant: uuid.UUID,
        llm: MockLLMProvider,
        storage: MockStorageProvider,
    ) -> None:
        await _post_text(knowledge)
        source_id = queue.last_source_id
        await _run_worker(db, tenant, source_id, llm=llm, storage=storage)

        hits = await search_knowledge(db, tenant, "cuanto cuesta el delivery", k=3, llm=llm)

        assert hits
        assert all(hit.source_id == source_id for hit in hits)

    async def test_the_query_is_embedded_as_a_query(
        self, db: Session, tenant: uuid.UUID, llm: MockLLMProvider
    ) -> None:
        """`passage` here measurably degrades ranking and breaks nothing."""
        await search_knowledge(db, tenant, "horario de atencion", llm=llm)

        assert llm.embed_calls[-1].input_type == "query"

    async def test_an_empty_query_costs_nothing(
        self, db: Session, tenant: uuid.UUID, llm: MockLLMProvider
    ) -> None:
        assert await search_knowledge(db, tenant, "   ", llm=llm) == []
        assert llm.embed_calls == []


class TestPdfIngestion:
    async def test_an_uploaded_pdf_is_stored_and_queued(
        self,
        knowledge: AsyncClient,
        db: Session,
        storage: MockStorageProvider,
        queue: RecordingQueue,
        tenant: uuid.UUID,
    ) -> None:
        response = await _post_pdf(knowledge, _text_pdf("Carta de vinos"), title="Carta")

        assert response.status_code == 202
        source = _source(db, uuid.UUID(response.json()["id"]))
        assert source.type == "pdf"
        assert source.title == "Carta"
        assert source.file_key is not None
        assert storage.exists(source.file_key)
        # The key is namespaced by tenant, so an object cannot be confused
        # with another customer's even in a bucket listing.
        assert source.file_key.startswith(f"tenants/{tenant!s}/knowledge/")
        assert len(queue.jobs) == 1

    async def test_the_file_name_is_used_when_no_title_is_given(
        self, knowledge: AsyncClient, db: Session
    ) -> None:
        response = await _post_pdf(knowledge, _text_pdf("Sin titulo"))
        assert _source(db, uuid.UUID(response.json()["id"])).title == "menu.pdf"

    async def test_the_worker_extracts_chunks_and_sizes_it(
        self,
        knowledge: AsyncClient,
        db: Session,
        queue: RecordingQueue,
        tenant: uuid.UUID,
        llm: MockLLMProvider,
        storage: MockStorageProvider,
    ) -> None:
        await _post_pdf(knowledge, _text_pdf("Pollo a la brasa entero S/ 55"))
        source_id = queue.last_source_id

        outcome = await _run_worker(db, tenant, source_id, llm=llm, storage=storage)

        assert outcome.succeeded
        source = _source(db, source_id)
        assert source.status == "indexed"
        # A PDF is accepted at zero characters and sized by the worker; a
        # count still at zero would mean the budget was never charged.
        assert source.char_count > 0

    async def test_a_file_that_is_not_a_pdf_is_refused(self, knowledge: AsyncClient) -> None:
        response = await _post_pdf(knowledge, b"GIF89a this is an image")

        assert response.status_code == 400
        assert response.json()["error"] == "invalid_pdf"

    async def test_a_pdf_over_the_size_ceiling_is_refused(
        self, knowledge: AsyncClient, db: Session, account: Account
    ) -> None:
        """Clarification 011 caps a single PDF at 10 MB."""
        limit = get_settings().knowledge_max_pdf_bytes
        oversized = b"%PDF-1.4" + b"\x00" * limit

        response = await _post_pdf(knowledge, oversized)

        assert response.status_code == 413
        assert response.json()["error"] == "pdf_too_large"
        assert _source_count(db, account.tenant_id) == 0


class TestAScannedPdfFails:
    """tasks.md verification 2: a PDF with no text ends `failed`, with a reason."""

    async def test_it_is_marked_failed_rather_than_indexed(
        self,
        knowledge: AsyncClient,
        db: Session,
        queue: RecordingQueue,
        tenant: uuid.UUID,
        llm: MockLLMProvider,
        storage: MockStorageProvider,
    ) -> None:
        await _post_pdf(knowledge, _scanned_pdf())
        source_id = queue.last_source_id

        outcome = await _run_worker(db, tenant, source_id, llm=llm, storage=storage)

        assert outcome.status == "failed"
        assert _source(db, source_id).status == "failed"

    async def test_the_reason_is_recorded_and_actionable(
        self,
        knowledge: AsyncClient,
        db: Session,
        queue: RecordingQueue,
        tenant: uuid.UUID,
        llm: MockLLMProvider,
        storage: MockStorageProvider,
    ) -> None:
        await _post_pdf(knowledge, _scanned_pdf())
        source_id = queue.last_source_id

        await _run_worker(db, tenant, source_id, llm=llm, storage=storage)

        message = (_source(db, source_id).error_msg or "").lower()
        assert "no extractable text" in message
        assert "scan" in message

    async def test_it_produces_no_chunks_and_costs_no_embeddings(
        self,
        knowledge: AsyncClient,
        db: Session,
        queue: RecordingQueue,
        tenant: uuid.UUID,
        llm: MockLLMProvider,
        storage: MockStorageProvider,
    ) -> None:
        """The empty-success bug would be `indexed` with zero chunks."""
        await _post_pdf(knowledge, _scanned_pdf())
        source_id = queue.last_source_id

        await _run_worker(db, tenant, source_id, llm=llm, storage=storage)

        assert _chunk_count(db, source_id) == 0
        assert llm.embed_calls == []

    async def test_the_failure_is_visible_in_the_listing(
        self,
        knowledge: AsyncClient,
        db: Session,
        queue: RecordingQueue,
        tenant: uuid.UUID,
        llm: MockLLMProvider,
        storage: MockStorageProvider,
    ) -> None:
        """A failed upload that the panel cannot show is a lost file."""
        await _post_pdf(knowledge, _scanned_pdf())
        await _run_worker(db, tenant, queue.last_source_id, llm=llm, storage=storage)

        listed = (await knowledge.get(KNOWLEDGE)).json()["sources"]

        assert [item["status"] for item in listed] == ["failed"]
        assert listed[0]["error_msg"]

    async def test_an_unreadable_upload_also_fails_with_a_reason(
        self,
        knowledge: AsyncClient,
        db: Session,
        queue: RecordingQueue,
        tenant: uuid.UUID,
        llm: MockLLMProvider,
        storage: MockStorageProvider,
    ) -> None:
        """The magic-byte check passes but pypdf cannot open it."""
        await _post_pdf(knowledge, b"%PDF-1.4\nnot really a document")
        source_id = queue.last_source_id

        await _run_worker(db, tenant, source_id, llm=llm, storage=storage)

        assert _source(db, source_id).error_msg


class TestPlanLimits:
    """tasks.md verification 3: over the limit -> 413, with the usage."""

    async def test_an_inline_source_over_the_character_budget_is_refused(
        self, knowledge: AsyncClient, db: Session, plan: Plan
    ) -> None:
        plan.max_rag_chars = 50
        db.flush()

        response = await _post_text(knowledge, content="x" * 200)

        assert response.status_code == 413
        assert response.json()["error"] == "rag_limit_exceeded"

    async def test_the_413_carries_the_current_usage(
        self, knowledge: AsyncClient, db: Session, plan: Plan
    ) -> None:
        plan.max_rag_chars = 120
        db.flush()
        await _post_text(knowledge, content="a" * 100)

        response = await _post_text(knowledge, content="b" * 100)

        assert response.status_code == 413
        fields = response.json()["fields"]
        assert fields == {
            "chars_used": "100",
            "chars_limit": "120",
            "files_used": "1",
            "files_limit": "20",
        }

    async def test_nothing_is_written_when_the_budget_refuses(
        self, knowledge: AsyncClient, db: Session, plan: Plan, account: Account
    ) -> None:
        plan.max_rag_chars = 50
        db.flush()

        await _post_text(knowledge, content="x" * 200)

        assert _source_count(db, account.tenant_id) == 0

    async def test_the_file_count_budget_is_enforced(
        self, knowledge: AsyncClient, db: Session, plan: Plan
    ) -> None:
        plan.max_knowledge_files = 1
        db.flush()
        assert (await _post_text(knowledge, content="uno")).status_code == 202

        response = await _post_text(knowledge, content="dos")

        assert response.status_code == 413
        assert response.json()["fields"]["files_used"] == "1"
        assert response.json()["fields"]["files_limit"] == "1"

    async def test_a_pdf_is_refused_when_no_character_budget_remains(
        self, knowledge: AsyncClient, db: Session, plan: Plan
    ) -> None:
        """A PDF reserves nothing up front, so "already full" is its own check."""
        plan.max_rag_chars = 100
        db.flush()
        await _post_text(knowledge, content="x" * 100)

        response = await _post_pdf(knowledge, _text_pdf("Carta"))

        assert response.status_code == 413
        assert response.json()["error"] == "rag_limit_exceeded"

    async def test_the_worker_refuses_a_pdf_that_turns_out_too_big(
        self,
        knowledge: AsyncClient,
        db: Session,
        plan: Plan,
        queue: RecordingQueue,
        tenant: uuid.UUID,
        llm: MockLLMProvider,
        storage: MockStorageProvider,
    ) -> None:
        """The exact budget can only be applied once the text is extracted."""
        plan.max_rag_chars = 20
        db.flush()
        await _post_pdf(knowledge, _text_pdf("Un texto claramente mas largo que veinte"))
        source_id = queue.last_source_id

        outcome = await _run_worker(db, tenant, source_id, llm=llm, storage=storage)

        assert outcome.status == "failed"
        assert "characters" in (_source(db, source_id).error_msg or "")
        # Refused before embedding, so an over-budget document costs nothing.
        assert llm.embed_calls == []
        assert _chunk_count(db, source_id) == 0

    async def test_limits_are_read_from_the_plans_table(
        self, knowledge: AsyncClient, db: Session, plan: Plan
    ) -> None:
        """Changing the row changes the answer; nothing is hardcoded."""
        plan.max_rag_chars = 10
        plan.max_knowledge_files = 3
        db.flush()

        usage = (await knowledge.get(KNOWLEDGE)).json()["usage"]

        assert usage["chars_limit"] == 10
        assert usage["files_limit"] == 3

    def test_the_seeded_trial_plan_matches_clarification_011(self, db: Session) -> None:
        """500 K characters and 20 files, from migration 0001's seed."""
        trial = db.get(Plan, "trial")
        assert trial is not None
        assert trial.max_rag_chars == 500_000
        assert trial.max_knowledge_files == 20

    async def test_deleting_a_source_frees_its_quota(
        self, knowledge: AsyncClient, db: Session
    ) -> None:
        created = await _post_text(knowledge, content="x" * 100)
        assert (await knowledge.get(KNOWLEDGE)).json()["usage"]["chars_used"] == 100

        await knowledge.delete(f"{KNOWLEDGE}/{created.json()['id']}")

        usage = (await knowledge.get(KNOWLEDGE)).json()["usage"]
        assert usage["chars_used"] == 0
        assert usage["files_used"] == 0


class TestListing:
    async def test_it_reports_status_char_count_and_usage(self, knowledge: AsyncClient) -> None:
        await _post_text(knowledge, title="Politica de cambios", content=MENU)

        body = (await knowledge.get(KNOWLEDGE)).json()

        assert body["sources"][0]["title"] == "Politica de cambios"
        assert body["sources"][0]["status"] == "pending"
        assert body["sources"][0]["char_count"] == len(MENU)
        assert body["usage"] == {
            "chars_used": len(MENU),
            "chars_limit": 500_000,
            "files_used": 1,
            "files_limit": 20,
        }

    async def test_an_empty_knowledge_base_still_reports_the_budget(
        self, knowledge: AsyncClient
    ) -> None:
        body = (await knowledge.get(KNOWLEDGE)).json()
        assert body["sources"] == []
        assert body["usage"]["chars_limit"] == 500_000


class TestDeletion:
    async def test_it_removes_the_chunks_the_agent_would_retrieve(
        self,
        knowledge: AsyncClient,
        db: Session,
        queue: RecordingQueue,
        tenant: uuid.UUID,
        llm: MockLLMProvider,
        storage: MockStorageProvider,
    ) -> None:
        """A surviving vector keeps answering from a deleted document."""
        created = await _post_text(knowledge)
        source_id = queue.last_source_id
        await _run_worker(db, tenant, source_id, llm=llm, storage=storage)
        assert _chunk_count(db, source_id) > 0

        response = await knowledge.delete(f"{KNOWLEDGE}/{created.json()['id']}")

        assert response.status_code == 204
        assert _chunk_count(db, source_id) == 0
        assert await search_knowledge(db, tenant, "delivery", llm=llm) == []

    async def test_a_deleted_source_disappears_from_the_listing(
        self, knowledge: AsyncClient
    ) -> None:
        created = await _post_text(knowledge)
        await knowledge.delete(f"{KNOWLEDGE}/{created.json()['id']}")

        assert (await knowledge.get(KNOWLEDGE)).json()["sources"] == []

    async def test_an_unknown_id_is_a_404(self, knowledge: AsyncClient) -> None:
        response = await knowledge.delete(f"{KNOWLEDGE}/{uuid.uuid4()}")
        assert response.status_code == 404

    async def test_another_tenants_source_is_also_a_404(
        self, knowledge: AsyncClient, db: Session, other_tenant: uuid.UUID
    ) -> None:
        """Never 403: a different status would confirm the id exists."""
        foreign = KnowledgeSource(tenant_id=other_tenant, type="faq", title="Ajeno")
        db.add(foreign)
        db.flush()

        response = await knowledge.delete(f"{KNOWLEDGE}/{foreign.id}")

        assert response.status_code == 404
        assert db.get(KnowledgeSource, foreign.id) is not None


class TestFailuresAreNeverSilent:
    async def test_an_unreachable_queue_answers_503(
        self, knowledge: AsyncClient, queue: RecordingQueue
    ) -> None:
        queue.broken = True
        response = await _post_text(knowledge)

        assert response.status_code == 503
        assert response.json()["error"] == "indexing_unavailable"

    async def test_a_source_that_could_not_be_queued_is_not_kept(
        self, knowledge: AsyncClient, queue: RecordingQueue
    ) -> None:
        """Otherwise it sits `pending` forever, holding one of the file slots."""
        queue.broken = True
        await _post_text(knowledge)

        body = (await knowledge.get(KNOWLEDGE)).json()
        assert body["sources"] == []
        assert body["usage"]["files_used"] == 0

    async def test_an_embedding_outage_fails_the_source_with_a_reason(
        self,
        knowledge: AsyncClient,
        db: Session,
        queue: RecordingQueue,
        tenant: uuid.UUID,
        storage: MockStorageProvider,
    ) -> None:
        await _post_text(knowledge)
        source_id = queue.last_source_id

        outcome = await index_source(
            db,
            tenant,
            source_id,
            llm=_FailingEmbedder(),
            storage=storage,
            pdf_extractor=PypdfTextExtractor(),
        )

        assert outcome.status == "failed"
        assert "embedding provider" in (_source(db, source_id).error_msg or "")

    async def test_an_empty_inline_source_is_rejected_at_the_door(
        self, knowledge: AsyncClient
    ) -> None:
        response = await _post_text(knowledge, content="   ")
        assert response.status_code == 400

    async def test_an_unknown_source_type_is_rejected(self, knowledge: AsyncClient) -> None:
        response = await _post_text(knowledge, type="pdf")
        assert response.status_code == 400


class _FailingEmbedder(MockLLMProvider):
    """An `LLMProvider` whose embeddings always fail."""

    async def embed(self, texts: Any, *, input_type: Any = "passage") -> list[list[float]]:
        from app.providers.llm import LLMError

        raise LLMError("the embedding endpoint is down")


class TestTheWorkerStateMachine:
    async def test_a_source_is_claimed_exactly_once(
        self,
        knowledge: AsyncClient,
        db: Session,
        queue: RecordingQueue,
        tenant: uuid.UUID,
        llm: MockLLMProvider,
        storage: MockStorageProvider,
    ) -> None:
        """A duplicated job must not double the tenant's embedding bill."""
        await _post_text(knowledge)
        source_id = queue.last_source_id
        first = await _run_worker(db, tenant, source_id, llm=llm, storage=storage)
        calls_after_first = len(llm.embed_calls)

        second = await _run_worker(db, tenant, source_id, llm=llm, storage=storage)

        assert first.succeeded
        assert second.status == OUTCOME_NOT_CLAIMABLE
        assert len(llm.embed_calls) == calls_after_first

    async def test_a_failed_source_can_be_indexed_again(
        self,
        knowledge: AsyncClient,
        db: Session,
        queue: RecordingQueue,
        tenant: uuid.UUID,
        llm: MockLLMProvider,
        storage: MockStorageProvider,
    ) -> None:
        """A transient outage must not make a document permanently dead."""
        await _post_pdf(knowledge, _text_pdf("Carta de postres"))
        source_id = queue.last_source_id
        failing = MockPdfTextExtractor(fail_with=PdfWithoutTextError("no text this time"))
        await _run_worker(db, tenant, source_id, llm=llm, storage=storage, pdf=failing)
        assert _source(db, source_id).status == "failed"

        outcome = await _run_worker(db, tenant, source_id, llm=llm, storage=storage)

        assert outcome.succeeded
        source = _source(db, source_id)
        assert source.status == "indexed"
        # The stale reason must not survive a successful retry.
        assert source.error_msg is None

    async def test_re_indexing_replaces_chunks_instead_of_adding_to_them(
        self,
        knowledge: AsyncClient,
        db: Session,
        queue: RecordingQueue,
        tenant: uuid.UUID,
        llm: MockLLMProvider,
        storage: MockStorageProvider,
    ) -> None:
        await _post_text(knowledge)
        source_id = queue.last_source_id
        await _run_worker(db, tenant, source_id, llm=llm, storage=storage)
        first_count = _chunk_count(db, source_id)

        KnowledgeSourceRepository(db, tenant).mark_failed(source_id, "forced retry")
        db.commit()
        await _run_worker(db, tenant, source_id, llm=llm, storage=storage)

        assert _chunk_count(db, source_id) == first_count

    async def test_a_deleted_source_is_skipped(
        self,
        knowledge: AsyncClient,
        db: Session,
        queue: RecordingQueue,
        tenant: uuid.UUID,
        llm: MockLLMProvider,
        storage: MockStorageProvider,
    ) -> None:
        created = await _post_text(knowledge)
        source_id = queue.last_source_id
        await knowledge.delete(f"{KNOWLEDGE}/{created.json()['id']}")

        outcome = await _run_worker(db, tenant, source_id, llm=llm, storage=storage)

        assert outcome.status == OUTCOME_DELETED
        assert _chunk_count(db, source_id) == 0

    async def test_a_job_naming_an_unknown_source_writes_nothing(
        self,
        db: Session,
        tenant: uuid.UUID,
        llm: MockLLMProvider,
        storage: MockStorageProvider,
    ) -> None:
        outcome = await _run_worker(db, tenant, uuid.uuid4(), llm=llm, storage=storage)
        assert outcome.status == OUTCOME_MISSING


class TestAuthenticationIsRequired:
    """The knowledge base is reachable only through the TASK-007 chain."""

    @pytest.mark.parametrize(
        ("method", "path"),
        [
            ("get", ""),
            ("post", ""),
            ("delete", f"/{uuid.uuid4()}"),
        ],
    )
    async def test_every_endpoint_refuses_an_unauthenticated_caller(
        self, api: AsyncClient, method: str, path: str
    ) -> None:
        response = await getattr(api, method)(f"{KNOWLEDGE}{path}")

        assert response.status_code == 401
        assert response.json()["error"] == "not_authenticated"
        assert response.headers["www-authenticate"] == "Bearer"

    @pytest.mark.parametrize(
        ("method", "path"),
        [("get", ""), ("post", ""), ("delete", f"/{uuid.uuid4()}")],
    )
    async def test_every_endpoint_refuses_a_forged_token(
        self, api: AsyncClient, method: str, path: str
    ) -> None:
        forged, _ = issue_access_token(
            user_id=uuid.uuid4(),
            tenant_id=uuid.uuid4(),
            now=datetime.now(UTC),
            settings=SETTINGS.model_copy(update={"secret_key": "not-our-signing-key-at-all"}),
        )

        response = await getattr(api, method)(f"{KNOWLEDGE}{path}", headers=_bearer(forged))

        assert response.status_code == 401
        assert response.json()["error"] == "invalid_token"

    async def test_an_unverified_address_cannot_reach_the_knowledge_base(
        self, knowledge: AsyncClient, db: Session, account: Account
    ) -> None:
        """Clarification 005, on this router as on every other."""
        db.execute(update(User).where(User.id == account.user_id).values(email_verified_at=None))
        db.flush()

        response = await knowledge.get(KNOWLEDGE)

        assert response.status_code == 403
        assert response.json()["error"] == "email_not_verified"

    async def test_a_token_scoped_to_no_tenant_is_refused(
        self, api: AsyncClient, account: Account
    ) -> None:
        """A platform admin is authenticated but has no business to act in."""
        tenantless, _ = issue_access_token(
            user_id=account.user_id, tenant_id=None, now=datetime.now(UTC)
        )

        response = await api.get(KNOWLEDGE, headers=_bearer(tenantless))

        assert response.status_code == 403
        assert response.json()["error"] == "no_active_tenant"

    async def test_a_revoked_membership_loses_the_knowledge_base_at_once(
        self, knowledge: AsyncClient, db: Session, account: Account
    ) -> None:
        """The reason authority is re-read rather than carried in the token."""
        assert (await knowledge.get(KNOWLEDGE)).status_code == 200

        db.execute(
            update(TenantUser)
            .where(TenantUser.tenant_id == account.tenant_id)
            .values(status="disabled")
        )
        db.flush()

        response = await knowledge.get(KNOWLEDGE)

        assert response.status_code == 403
        assert response.json()["error"] == "tenant_access_denied"

    async def test_the_source_is_written_under_the_tenant_from_the_token(
        self, knowledge: AsyncClient, db: Session, account: Account
    ) -> None:
        """Nothing in the request names a tenant; the token is the only input."""
        created = await _post_text(knowledge)

        assert created.status_code == 202
        assert _source(db, uuid.UUID(created.json()["id"])).tenant_id == account.tenant_id


class TestRolesFollowClarification008:
    """`owner` and `tenant_admin` curate the knowledge base; the `operator`
    has the inbox and may look but not touch."""

    @pytest.mark.parametrize("role", ["owner", "tenant_admin", "operator"])
    async def test_every_member_may_read_the_listing(
        self, knowledge: AsyncClient, db: Session, account: Account, role: str
    ) -> None:
        _set_role(db, account, role)

        response = await knowledge.get(KNOWLEDGE)

        assert response.status_code == 200

    @pytest.mark.parametrize(
        ("role", "allowed"),
        [("owner", True), ("tenant_admin", True), ("operator", False)],
    )
    async def test_only_configuration_managers_may_add_a_source(
        self, knowledge: AsyncClient, db: Session, account: Account, role: str, allowed: bool
    ) -> None:
        _set_role(db, account, role)

        response = await _post_text(knowledge)

        assert (response.status_code == 202) is allowed
        if not allowed:
            assert response.status_code == 403
            assert response.json()["error"] == "insufficient_role"

    async def test_an_operator_is_refused_a_pdf_upload_too(
        self, knowledge: AsyncClient, db: Session, account: Account
    ) -> None:
        """Both bodies of `POST` go through the same guard."""
        _set_role(db, account, "operator")

        response = await _post_pdf(knowledge, _text_pdf("Carta"))

        assert response.status_code == 403
        assert response.json()["error"] == "insufficient_role"

    @pytest.mark.parametrize(
        ("role", "allowed"),
        [("owner", True), ("tenant_admin", True), ("operator", False)],
    )
    async def test_only_configuration_managers_may_delete_a_source(
        self, knowledge: AsyncClient, db: Session, account: Account, role: str, allowed: bool
    ) -> None:
        created = await _post_text(knowledge)
        source_id = uuid.UUID(created.json()["id"])
        _set_role(db, account, role)

        response = await knowledge.delete(f"{KNOWLEDGE}/{source_id}")

        assert (response.status_code == 204) is allowed
        if not allowed:
            assert response.json()["error"] == "insufficient_role"
            assert _source(db, source_id).deleted_at is None

    async def test_a_refused_operator_does_not_learn_whether_the_id_exists(
        self, knowledge: AsyncClient, db: Session, account: Account
    ) -> None:
        """The role check runs before the lookup, so a real id and an
        invented one answer identically."""
        created = await _post_text(knowledge)
        _set_role(db, account, "operator")

        real = await knowledge.delete(f"{KNOWLEDGE}/{created.json()['id']}")
        invented = await knowledge.delete(f"{KNOWLEDGE}/{uuid.uuid4()}")

        assert real.status_code == invented.status_code == 403
        assert real.json() == invented.json()

    async def test_an_operator_still_sees_what_the_agent_answers_from(
        self, knowledge: AsyncClient, db: Session, account: Account
    ) -> None:
        """Read-only is the point: the inbox is unusable without it."""
        await _post_text(knowledge, title="Politica de cambios")
        _set_role(db, account, "operator")

        body = (await knowledge.get(KNOWLEDGE)).json()

        assert [source["title"] for source in body["sources"]] == ["Politica de cambios"]
