"""Multi-tenant isolation for every knowledge path TASK-012 adds.

Mandated by the Constitution and never skipped or disabled. The module
attacks the boundary from both ends, the same way `test_session_isolation.py`
does:

* the repository level - one case per new repository method plus the
  retrieval service, each driving the real code against two real tenants;
* the HTTP level - two real signed-in owners, and one of them trying to read,
  fill and empty the other's knowledge base through the real endpoints.

The HTTP cases exist because the endpoints take no tenant parameter at all:
the only thing that decides whose documents a request touches is the access
token, so a hole in that resolution is a knowledge-base leak however well the
repositories are written.

The teeth of these cases are proved in `test_isolation_suite_has_teeth.py`:
removing the predicate from `BaseRepository._scoped()` must make them fail.
The two methods that carry their own predicate instead of inheriting
`_scoped()` - `claim_for_indexing`, which is an atomic UPDATE, and
`total_chars`, which is an aggregate - are sabotaged separately there, since
a `_scoped()` change cannot reach them.
"""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from urllib.parse import parse_qs, urlparse

import pytest
from fastapi import FastAPI
from httpx import AsyncClient
from sqlalchemy.orm import Session

from app.api.deps import get_job_queue, get_storage_provider
from app.core.config import get_settings
from app.core.tokens import issue_access_token
from app.models import KnowledgeChunk, KnowledgeSource
from app.providers.email.mock import MockEmailProvider
from app.providers.llm import MockLLMProvider
from app.providers.storage import MockStorageProvider
from app.repositories import (
    KnowledgeChunkRepository,
    KnowledgeSourceRepository,
    TenantRepository,
    TenantScopeError,
)
from app.services.knowledge import search_knowledge
from tests.isolation.conftest import EMBEDDING_DIM, TenantFixture

SETTINGS = get_settings()
AUTH = f"{SETTINGS.api_v1_prefix}/auth"
KNOWLEDGE = f"{SETTINGS.api_v1_prefix}/tenant/knowledge"
PASSWORD = "una-clave-suficientemente-larga"

_HREF = re.compile(r'href="([^"]+)"')


class TestSourceReadsAreScoped:
    def test_list_active_excludes_the_other_tenant(
        self, db: Session, tenant_a: TenantFixture, tenant_b: TenantFixture
    ) -> None:
        repo = KnowledgeSourceRepository(db, tenant_a.tenant_id)
        ids = {source.id for source in repo.list_active()}
        assert ids == {tenant_a.source_id}

    def test_list_indexed_excludes_the_other_tenant(
        self, db: Session, tenant_a: TenantFixture, tenant_b: TenantFixture
    ) -> None:
        repo = KnowledgeSourceRepository(db, tenant_a.tenant_id)
        ids = {source.id for source in repo.list_indexed()}
        assert tenant_b.source_id not in ids

    def test_total_chars_ignores_the_other_tenants_documents(
        self, db: Session, tenant_a: TenantFixture, tenant_b: TenantFixture
    ) -> None:
        """The plan budget must be charged to the tenant who spent it."""
        db.get(KnowledgeSource, tenant_a.source_id).char_count = 100  # type: ignore[union-attr]
        db.get(KnowledgeSource, tenant_b.source_id).char_count = 900  # type: ignore[union-attr]
        db.flush()

        assert KnowledgeSourceRepository(db, tenant_a.tenant_id).total_chars() == 100

    def test_excluding_a_foreign_source_cannot_change_the_total(
        self, db: Session, tenant_a: TenantFixture, tenant_b: TenantFixture
    ) -> None:
        """`exclude_source_id` narrows within the tenant, never across it."""
        db.get(KnowledgeSource, tenant_a.source_id).char_count = 100  # type: ignore[union-attr]
        db.flush()
        repo = KnowledgeSourceRepository(db, tenant_a.tenant_id)

        assert repo.total_chars(exclude_source_id=tenant_b.source_id) == 100
        assert repo.total_chars(exclude_source_id=tenant_a.source_id) == 0

    def test_active_file_count_only_counts_own_files(
        self, db: Session, tenant_a: TenantFixture, tenant_b: TenantFixture
    ) -> None:
        assert KnowledgeSourceRepository(db, tenant_a.tenant_id).active_file_count() == 1


class TestSourceStateChangesAreScoped:
    def test_a_foreign_source_cannot_be_claimed_for_indexing(
        self, db: Session, tenant_a: TenantFixture, tenant_b: TenantFixture
    ) -> None:
        """A job payload is a bare UUID; ownership has to be proved here."""
        db.get(KnowledgeSource, tenant_b.source_id).status = "pending"  # type: ignore[union-attr]
        db.flush()

        claimed = KnowledgeSourceRepository(db, tenant_a.tenant_id).claim_for_indexing(
            tenant_b.source_id
        )

        assert claimed is False
        victim = db.get(KnowledgeSource, tenant_b.source_id)
        assert victim is not None
        assert victim.status == "pending"

    def test_a_foreign_source_cannot_be_marked_indexed(
        self, db: Session, tenant_a: TenantFixture, tenant_b: TenantFixture
    ) -> None:
        db.get(KnowledgeSource, tenant_b.source_id).status = "failed"  # type: ignore[union-attr]
        db.flush()

        assert not KnowledgeSourceRepository(db, tenant_a.tenant_id).mark_indexed(
            tenant_b.source_id
        )
        victim = db.get(KnowledgeSource, tenant_b.source_id)
        assert victim is not None
        assert victim.status == "failed"

    def test_a_foreign_source_cannot_be_marked_failed(
        self, db: Session, tenant_a: TenantFixture, tenant_b: TenantFixture
    ) -> None:
        assert not KnowledgeSourceRepository(db, tenant_a.tenant_id).mark_failed(
            tenant_b.source_id, "sabotage"
        )
        victim = db.get(KnowledgeSource, tenant_b.source_id)
        assert victim is not None
        assert victim.error_msg is None
        assert victim.status == "indexed"

    def test_a_foreign_sources_size_cannot_be_rewritten(
        self, db: Session, tenant_a: TenantFixture, tenant_b: TenantFixture
    ) -> None:
        """Inflating another tenant's `char_count` would exhaust their plan."""
        assert not KnowledgeSourceRepository(db, tenant_a.tenant_id).set_char_count(
            tenant_b.source_id, 999_999
        )
        victim = db.get(KnowledgeSource, tenant_b.source_id)
        assert victim is not None
        assert victim.char_count == 0

    def test_a_foreign_source_cannot_be_soft_deleted(
        self, db: Session, tenant_a: TenantFixture, tenant_b: TenantFixture
    ) -> None:
        assert not KnowledgeSourceRepository(db, tenant_a.tenant_id).soft_delete(tenant_b.source_id)
        victim = db.get(KnowledgeSource, tenant_b.source_id)
        assert victim is not None
        assert victim.deleted_at is None

    def test_none_of_it_is_distinguishable_from_a_missing_row(
        self, db: Session, tenant_a: TenantFixture, tenant_b: TenantFixture
    ) -> None:
        """A different answer for "foreign" would leak that the id exists."""
        repo = KnowledgeSourceRepository(db, tenant_a.tenant_id)
        unknown = uuid.uuid4()
        assert repo.soft_delete(tenant_b.source_id) == repo.soft_delete(unknown)
        assert repo.mark_indexed(tenant_b.source_id) == repo.mark_indexed(unknown)
        assert repo.claim_for_indexing(tenant_b.source_id) == repo.claim_for_indexing(unknown)


class TestChunkAccessIsScoped:
    def test_chunks_of_a_foreign_source_are_invisible(
        self, db: Session, tenant_a: TenantFixture, tenant_b: TenantFixture
    ) -> None:
        """Knowing the source id must not be enough to read its chunks."""
        repo = KnowledgeChunkRepository(db, tenant_a.tenant_id)
        assert repo.list_for_source(tenant_b.source_id) == []
        assert repo.count_for_source(tenant_b.source_id) == 0

    def test_chunks_of_a_foreign_source_cannot_be_deleted(
        self, db: Session, tenant_a: TenantFixture, tenant_b: TenantFixture
    ) -> None:
        repo = KnowledgeChunkRepository(db, tenant_a.tenant_id)
        assert repo.delete_for_source(tenant_b.source_id) == 0
        assert db.get(KnowledgeChunk, tenant_b.chunk_id) is not None

    def test_writing_chunks_into_a_foreign_source_is_refused(
        self, db: Session, tenant_a: TenantFixture, tenant_b: TenantFixture
    ) -> None:
        """Poisoning another tenant's knowledge base is the worst case here."""
        repo = KnowledgeChunkRepository(db, tenant_a.tenant_id)
        with pytest.raises(TenantScopeError):
            repo.replace_for_source(tenant_b.source_id, [("Inyectado", [0.5] * EMBEDDING_DIM)])

    def test_a_refused_write_destroys_nothing(
        self, db: Session, tenant_a: TenantFixture, tenant_b: TenantFixture
    ) -> None:
        """`replace_for_source` deletes before it inserts; it must not get that far."""
        repo = KnowledgeChunkRepository(db, tenant_a.tenant_id)
        with pytest.raises(TenantScopeError):
            repo.replace_for_source(tenant_b.source_id, [])

        assert db.get(KnowledgeChunk, tenant_b.chunk_id) is not None

    def test_replacing_own_chunks_stamps_the_bound_tenant(
        self, db: Session, tenant_a: TenantFixture
    ) -> None:
        repo = KnowledgeChunkRepository(db, tenant_a.tenant_id)
        repo.replace_for_source(tenant_a.source_id, [("Nuevo", [0.1] * EMBEDDING_DIM)])

        chunks = repo.list_for_source(tenant_a.source_id)
        assert [chunk.content for chunk in chunks] == ["Nuevo"]
        assert all(chunk.tenant_id == tenant_a.tenant_id for chunk in chunks)


class TestRetrievalIsScoped:
    async def test_search_never_returns_another_tenants_chunk(
        self, db: Session, tenant_a: TenantFixture, tenant_b: TenantFixture
    ) -> None:
        """The whole point of RAG isolation: A must not read B's documents."""
        hits = await search_knowledge(db, tenant_a.tenant_id, "secret", k=10, llm=MockLLMProvider())

        assert tenant_b.chunk_id not in {hit.chunk_id for hit in hits}

    async def test_a_tenant_with_no_knowledge_gets_nothing(
        self, db: Session, tenant_b: TenantFixture
    ) -> None:
        """Not "someone else's answer" - nothing."""
        empty_tenant = uuid.uuid4()
        hits = await search_knowledge(db, empty_tenant, "secret", k=10, llm=MockLLMProvider())

        assert hits == []

    async def test_the_tenants_own_chunk_still_wins_when_a_foreign_one_is_nearer(
        self, db: Session, tenant_a: TenantFixture, tenant_b: TenantFixture
    ) -> None:
        """Late filtering would return an empty list here, not a leak.

        B's chunk is the nearest overall. Ranking first and filtering after
        fills the limit with it and then discards it, so A silently gets
        nothing - the failure this ordering exists to prevent.
        """
        chunk_a = db.get(KnowledgeChunk, tenant_a.chunk_id)
        chunk_b = db.get(KnowledgeChunk, tenant_b.chunk_id)
        assert chunk_a is not None and chunk_b is not None
        query_text = "consulta dirigida"
        # Point the query straight at B's vector.
        chunk_b.embedding = (await MockLLMProvider().embed([query_text], input_type="query"))[0]
        chunk_a.embedding = [0.001] * EMBEDDING_DIM
        db.flush()

        hits = await search_knowledge(
            db, tenant_a.tenant_id, query_text, k=1, llm=MockLLMProvider()
        )

        assert [hit.chunk_id for hit in hits] == [tenant_a.chunk_id]


class TestTheTenantLock:
    def test_it_only_ever_locks_the_tenant_it_was_asked_for(
        self, db: Session, tenant_a: TenantFixture, tenant_b: TenantFixture
    ) -> None:
        """The budget mutex must not be shared between customers."""
        locked = TenantRepository(db).lock(tenant_a.tenant_id)

        assert locked is not None
        assert locked.id == tenant_a.tenant_id

    def test_locking_an_unknown_tenant_returns_nothing(self, db: Session) -> None:
        assert TenantRepository(db).lock(uuid.uuid4()) is None


# ------------------------------------------------------------- the HTTP edge


@dataclass(frozen=True)
class Owner:
    """A real, verified, signed-in owner of their own trial tenant."""

    user_id: uuid.UUID
    tenant_id: uuid.UUID
    access_token: str


class _RecordingQueue:
    """A `JobQueue` that records instead of talking to Redis."""

    def __init__(self) -> None:
        self.jobs: list[tuple[str, tuple[object, ...]]] = []

    async def enqueue(self, function: str, *args: object) -> str | None:
        self.jobs.append((function, args))
        return f"job-{len(self.jobs)}"


@pytest.fixture
def ingestion(app: FastAPI) -> FastAPI:
    """The app with the object store and the queue replaced by mocks.

    Nothing about authentication is overridden: what these cases are about is
    exactly the part that decides whose tenant a request lands in.
    """
    app.dependency_overrides[get_storage_provider] = MockStorageProvider
    app.dependency_overrides[get_job_queue] = _RecordingQueue
    return app


def _bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _token_from(message: Any) -> str:
    for href in _HREF.findall(message.html):
        query = parse_qs(urlparse(href).query)
        if "token" in query:
            return str(query["token"][0])
    raise AssertionError("no verification link in the email body")


async def _signed_in_owner(api: AsyncClient, mail: MockEmailProvider, business: str) -> Owner:
    """A whole account, through the real registration and login endpoints."""
    email = f"owner-{uuid.uuid4().hex[:12]}@example.com"
    created = await api.post(
        f"{AUTH}/register",
        json={
            "full_name": f"Owner of {business}",
            "email": email,
            "password": PASSWORD,
            "business_name": business,
        },
    )
    assert created.status_code == 201, created.text
    assert (
        await api.post(f"{AUTH}/verify-email", json={"token": _token_from(mail.last)})
    ).status_code == 204

    session = await api.post(f"{AUTH}/login", json={"email": email, "password": PASSWORD})
    assert session.status_code == 200, session.text
    return Owner(
        user_id=uuid.UUID(created.json()["user_id"]),
        tenant_id=uuid.UUID(created.json()["tenant_id"]),
        access_token=session.json()["access_token"],
    )


async def _add_source(api: AsyncClient, owner: Owner, title: str) -> uuid.UUID:
    response = await api.post(
        KNOWLEDGE,
        json={"type": "faq", "title": title, "content": "Delivery a Miraflores por S/ 5."},
        headers=_bearer(owner.access_token),
    )
    assert response.status_code == 202, response.text
    return uuid.UUID(response.json()["id"])


class TestOneBusinessNeverReachesAnothersKnowledgeBase:
    """The endpoints accept no tenant parameter, so every case here is a real
    attempt to cross the boundary with the only handle that exists: a token."""

    async def test_the_listing_only_ever_shows_the_callers_own_sources(
        self, ingestion: FastAPI, api: AsyncClient, mail: MockEmailProvider
    ) -> None:
        victim = await _signed_in_owner(api, mail, "Panaderia Quispe")
        attacker = await _signed_in_owner(api, mail, "Bodega Rival")
        secret = await _add_source(api, victim, "Recetas de la casa")

        body = (await api.get(KNOWLEDGE, headers=_bearer(attacker.access_token))).json()

        assert [source["id"] for source in body["sources"]] == []
        assert "Recetas de la casa" not in str(body)
        assert str(secret) not in str(body)

    async def test_the_plan_usage_is_not_charged_across_businesses(
        self, ingestion: FastAPI, api: AsyncClient, mail: MockEmailProvider
    ) -> None:
        """A leak here would exhaust a neighbour's budget as well as leak it."""
        victim = await _signed_in_owner(api, mail, "Panaderia Quispe")
        attacker = await _signed_in_owner(api, mail, "Bodega Rival")
        await _add_source(api, victim, "Recetas de la casa")

        body = (await api.get(KNOWLEDGE, headers=_bearer(attacker.access_token))).json()

        assert body["usage"]["chars_used"] == 0
        assert body["usage"]["files_used"] == 0

    async def test_a_token_naming_a_foreign_tenant_cannot_read(
        self, ingestion: FastAPI, api: AsyncClient, mail: MockEmailProvider
    ) -> None:
        """Signing a token for somebody else's tenant is the direct attack."""
        victim = await _signed_in_owner(api, mail, "Panaderia Quispe")
        attacker = await _signed_in_owner(api, mail, "Bodega Rival")
        await _add_source(api, victim, "Recetas de la casa")

        crafted, _ = issue_access_token(
            user_id=attacker.user_id, tenant_id=victim.tenant_id, now=datetime.now(UTC)
        )
        response = await api.get(KNOWLEDGE, headers=_bearer(crafted))

        assert response.status_code == 403
        assert response.json()["error"] == "tenant_access_denied"

    async def test_a_token_naming_a_foreign_tenant_cannot_create(
        self, ingestion: FastAPI, api: AsyncClient, db: Session, mail: MockEmailProvider
    ) -> None:
        """Writing is the worse half: a planted document changes what the
        neighbour's agent tells their customers."""
        victim = await _signed_in_owner(api, mail, "Panaderia Quispe")
        attacker = await _signed_in_owner(api, mail, "Bodega Rival")

        crafted, _ = issue_access_token(
            user_id=attacker.user_id, tenant_id=victim.tenant_id, now=datetime.now(UTC)
        )
        response = await api.post(
            KNOWLEDGE,
            json={"type": "faq", "title": "Inyectado", "content": "Cerramos para siempre."},
            headers=_bearer(crafted),
        )

        assert response.status_code == 403
        assert response.json()["error"] == "tenant_access_denied"
        assert KnowledgeSourceRepository(db, victim.tenant_id).list_active() == []

    async def test_a_foreign_source_cannot_be_deleted_over_http(
        self, ingestion: FastAPI, api: AsyncClient, db: Session, mail: MockEmailProvider
    ) -> None:
        """The attacker uses their own valid token and the victim's id - the
        realistic shape of this bug, since ids travel in URLs."""
        victim = await _signed_in_owner(api, mail, "Panaderia Quispe")
        attacker = await _signed_in_owner(api, mail, "Bodega Rival")
        secret = await _add_source(api, victim, "Recetas de la casa")

        response = await api.delete(f"{KNOWLEDGE}/{secret}", headers=_bearer(attacker.access_token))

        assert response.status_code == 404
        survivor = db.get(KnowledgeSource, secret)
        assert survivor is not None
        assert survivor.deleted_at is None

    async def test_the_refusal_looks_the_same_as_an_invented_id(
        self, ingestion: FastAPI, api: AsyncClient, mail: MockEmailProvider
    ) -> None:
        """A distinguishable answer would turn the endpoint into a probe for
        another business's document ids."""
        victim = await _signed_in_owner(api, mail, "Panaderia Quispe")
        attacker = await _signed_in_owner(api, mail, "Bodega Rival")
        secret = await _add_source(api, victim, "Recetas de la casa")

        foreign = await api.delete(f"{KNOWLEDGE}/{secret}", headers=_bearer(attacker.access_token))
        invented = await api.delete(
            f"{KNOWLEDGE}/{uuid.uuid4()}", headers=_bearer(attacker.access_token)
        )

        assert foreign.status_code == invented.status_code == 404
        assert foreign.json() == invented.json()

    async def test_the_owner_still_reaches_their_own_knowledge_base(
        self, ingestion: FastAPI, api: AsyncClient, mail: MockEmailProvider
    ) -> None:
        """The other half of every assertion above: a router that refused
        everything would pass them all and serve nobody."""
        victim = await _signed_in_owner(api, mail, "Panaderia Quispe")
        await _signed_in_owner(api, mail, "Bodega Rival")
        secret = await _add_source(api, victim, "Recetas de la casa")

        body = (await api.get(KNOWLEDGE, headers=_bearer(victim.access_token))).json()

        assert [source["id"] for source in body["sources"]] == [str(secret)]
        assert (
            await api.delete(f"{KNOWLEDGE}/{secret}", headers=_bearer(victim.access_token))
        ).status_code == 204

    async def test_the_two_businesses_are_actually_distinct(
        self, ingestion: FastAPI, api: AsyncClient, mail: MockEmailProvider
    ) -> None:
        """Guards against both helpers returning the same account, which would
        make every case above vacuous."""
        victim = await _signed_in_owner(api, mail, "Panaderia Quispe")
        attacker = await _signed_in_owner(api, mail, "Bodega Rival")

        assert victim.tenant_id != attacker.tenant_id
        assert victim.user_id != attacker.user_id


class TestTheFeature006EndpointsStayInTheirBusiness:
    """Detail, edit, re-process and the chunk view (Feature 006) answer 404 with
    another business's source id, and change nothing."""

    async def test_every_new_endpoint_answers_404(
        self, ingestion: FastAPI, api: AsyncClient, db: Session, mail: MockEmailProvider
    ) -> None:
        victim = await _signed_in_owner(api, mail, "Panaderia Quispe")
        attacker = await _signed_in_owner(api, mail, "Bodega Rival")
        secret = await _add_source(api, victim, "Recetas de la casa")
        headers = _bearer(attacker.access_token)

        answers = [
            await api.get(f"{KNOWLEDGE}/{secret}", headers=headers),
            await api.patch(
                f"{KNOWLEDGE}/{secret}",
                json={"content": "Cerramos para siempre.", "priority": 0},
                headers=headers,
            ),
            await api.post(f"{KNOWLEDGE}/{secret}/resync", headers=headers),
            await api.get(f"{KNOWLEDGE}/{secret}/chunks", headers=headers),
        ]

        assert [a.status_code for a in answers] == [404, 404, 404, 404]
        db.expire_all()
        untouched = db.get(KnowledgeSource, secret)
        assert untouched is not None
        assert untouched.content == "Delivery a Miraflores por S/ 5."
        assert untouched.priority == 100 and untouched.status == "pending"
