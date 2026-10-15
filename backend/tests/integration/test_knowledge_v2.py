"""Knowledge v2 (Feature 006): editing, re-indexing, dedup and the debug views.

Reuses the ingestion module's fixtures: a real signed-in owner, the real API,
and the production worker driven in-process with mock storage, queue and model.
"""

# The ingestion module's fixtures are imported and then requested by name, which
# ruff reads as a redefinition.
# ruff: noqa: F811

from __future__ import annotations

import uuid
from collections.abc import Sequence
from typing import Any

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import KnowledgeChunk
from app.providers.llm import LLMError, MockLLMProvider
from app.providers.llm.base import InputType
from app.providers.storage import MockStorageProvider
from app.services.chunking import chunk_text, content_hash, normalise
from app.services.knowledge import INDEX_JOB
from app.workers.knowledge_indexer import KEPT_PREVIOUS
from tests.integration.test_knowledge_ingestion import (  # noqa: F401 - fixtures
    KNOWLEDGE,
    Account,
    RecordingQueue,
    _run_worker,
    _set_role,
    _source,
    account,
    knowledge,
    llm,
    plan,
    queue,
    storage,
    tenant,
)

POLICY = "Hacemos envíos a todo Lima en 24 horas. El envío cuesta 10 soles."
FOOTER = "Panadería Leo · Av. Larco 123 · Miraflores · Teléfono 01 555 0000."


class _BrokenEmbeddings(MockLLMProvider):
    async def embed(
        self, texts: Sequence[str], *, input_type: InputType = "passage"
    ) -> list[list[float]]:
        raise LLMError("the embedding endpoint is down")


def _chunks(db: Session, source_id: uuid.UUID) -> list[KnowledgeChunk]:
    db.expire_all()
    return list(
        db.execute(select(KnowledgeChunk).where(KnowledgeChunk.source_id == source_id))
        .scalars()
        .all()
    )


async def _indexed(
    client: AsyncClient,
    db: Session,
    tenant_id: uuid.UUID,
    llm: MockLLMProvider,
    storage: MockStorageProvider,
    *,
    content: str = POLICY,
    kind: str = "policy",
) -> uuid.UUID:
    created = await client.post(
        KNOWLEDGE, json={"type": kind, "title": "Envíos", "content": content}
    )
    assert created.status_code == 202, created.text
    source_id = uuid.UUID(created.json()["id"])
    outcome = await _run_worker(db, tenant_id, source_id, llm=llm, storage=storage)
    assert outcome.succeeded, outcome
    return source_id


class TestDedup:
    async def test_a_repeated_block_is_embedded_once_and_hashed(
        self,
        knowledge: AsyncClient,
        db: Session,
        tenant: uuid.UUID,
        llm: MockLLMProvider,
        storage: MockStorageProvider,
    ) -> None:
        # A long block repeated (the same terms printed after every section): with
        # the overlap, every repetition after the second is the same chunk.
        block = "Términos: " + ("Pan de masa madre horneado cada mañana. " * 22)
        text = "Presentación de la panadería.\n\n" + "\n\n".join([block] * 4)

        source_id = await _indexed(knowledge, db, tenant, llm, storage, content=text, kind="text")

        rows = _chunks(db, source_id)
        hashes = [row.content_hash for row in rows]
        assert all(hashes) and len(hashes) == len(set(hashes))
        assert all(row.content_hash == content_hash(row.content) for row in rows)
        embedded = [t for call in llm.embed_calls for t in call.texts]
        assert len(embedded) == len(rows)
        # Dedup actually removed something.
        assert len(chunk_text(normalise(text))) > len(rows)


class TestEditing:
    async def test_new_text_replaces_the_old_only_once_indexed(
        self,
        knowledge: AsyncClient,
        db: Session,
        tenant: uuid.UUID,
        llm: MockLLMProvider,
        storage: MockStorageProvider,
        queue: RecordingQueue,
    ) -> None:
        source_id = await _indexed(knowledge, db, tenant, llm, storage)
        jobs_before = len(queue.jobs)

        edited = await knowledge.patch(
            f"{KNOWLEDGE}/{source_id}",
            json={"content": "Hacemos envíos a todo Lima en 48 horas. El envío es gratis."},
        )

        assert edited.status_code == 200, edited.text
        assert edited.json()["status"] == "pending"
        assert queue.jobs[-1] == (INDEX_JOB, (str(tenant), str(source_id)))
        assert len(queue.jobs) == jobs_before + 1
        # Until the worker runs, the agent still reads the old version.
        assert all("24 horas" in c.content for c in _chunks(db, source_id))

        await _run_worker(db, tenant, source_id, llm=llm, storage=storage)

        chunks = _chunks(db, source_id)
        assert chunks and all("48 horas" in c.content for c in chunks)
        source = _source(db, source_id)
        assert source.status == "indexed"
        assert source.char_count == len(
            "Hacemos envíos a todo Lima en 48 horas. El envío es gratis."
        )

    async def test_title_and_priority_change_without_reindexing(
        self,
        knowledge: AsyncClient,
        db: Session,
        tenant: uuid.UUID,
        llm: MockLLMProvider,
        storage: MockStorageProvider,
        queue: RecordingQueue,
    ) -> None:
        source_id = await _indexed(knowledge, db, tenant, llm, storage)
        jobs_before = len(queue.jobs)

        edited = await knowledge.patch(
            f"{KNOWLEDGE}/{source_id}",
            json={"title": "Política de envíos", "priority": 70, "content": POLICY},
        )

        assert edited.status_code == 200, edited.text
        assert edited.json()["status"] == "indexed"
        assert edited.json()["title"] == "Política de envíos"
        assert edited.json()["priority"] == 70
        assert len(queue.jobs) == jobs_before

    async def test_a_source_being_indexed_is_not_raced(
        self, knowledge: AsyncClient, db: Session, tenant: uuid.UUID
    ) -> None:
        created = await knowledge.post(
            KNOWLEDGE, json={"type": "faq", "title": "FAQ", "content": POLICY}
        )
        source_id = uuid.UUID(created.json()["id"])
        source = _source(db, source_id)
        source.status = "indexing"
        db.flush()

        edited = await knowledge.patch(f"{KNOWLEDGE}/{source_id}", json={"content": "Otra cosa."})
        resynced = await knowledge.post(f"{KNOWLEDGE}/{source_id}/resync")

        assert edited.status_code == 409 and edited.json()["error"] == "source_busy"
        assert resynced.status_code == 409

    async def test_a_failed_reindex_keeps_the_previous_version(
        self,
        knowledge: AsyncClient,
        db: Session,
        tenant: uuid.UUID,
        llm: MockLLMProvider,
        storage: MockStorageProvider,
    ) -> None:
        source_id = await _indexed(knowledge, db, tenant, llm, storage)
        await knowledge.patch(f"{KNOWLEDGE}/{source_id}", json={"content": "Texto nuevo."})

        outcome = await _run_worker(db, tenant, source_id, llm=_BrokenEmbeddings(), storage=storage)

        assert outcome.status == "failed"
        assert outcome.error_msg is not None and outcome.error_msg.endswith(KEPT_PREVIOUS)
        assert all("24 horas" in c.content for c in _chunks(db, source_id))

        # And a retry picks it up again.
        retried = await knowledge.post(f"{KNOWLEDGE}/{source_id}/resync")
        assert retried.status_code == 202 and retried.json()["status"] == "pending"
        await _run_worker(db, tenant, source_id, llm=llm, storage=storage)
        assert all("Texto nuevo" in c.content for c in _chunks(db, source_id))

    async def test_a_pdf_is_not_edited_as_text(
        self, knowledge: AsyncClient, db: Session, tenant: uuid.UUID
    ) -> None:
        from tests.integration.test_knowledge_ingestion import _post_pdf, _text_pdf

        created = await _post_pdf(knowledge, _text_pdf("Carta"))
        source_id = created.json()["id"]

        edited = await knowledge.patch(f"{KNOWLEDGE}/{source_id}", json={"content": "x"})

        assert edited.status_code == 400

    async def test_an_operator_reads_but_does_not_edit(
        self,
        knowledge: AsyncClient,
        db: Session,
        account: Account,
        tenant: uuid.UUID,
        llm: MockLLMProvider,
        storage: MockStorageProvider,
    ) -> None:
        source_id = await _indexed(knowledge, db, tenant, llm, storage)
        _set_role(db, account, "operator")

        detail = await knowledge.get(f"{KNOWLEDGE}/{source_id}")
        edited = await knowledge.patch(f"{KNOWLEDGE}/{source_id}", json={"priority": 1})
        chunks = await knowledge.get(f"{KNOWLEDGE}/{source_id}/chunks")

        assert detail.status_code == 200 and detail.json()["content"] == POLICY
        assert edited.status_code == 403
        assert chunks.status_code == 403


class TestDebugViews:
    async def test_detail_and_chunks(
        self,
        knowledge: AsyncClient,
        db: Session,
        tenant: uuid.UUID,
        llm: MockLLMProvider,
        storage: MockStorageProvider,
    ) -> None:
        source_id = await _indexed(knowledge, db, tenant, llm, storage)

        detail = (await knowledge.get(f"{KNOWLEDGE}/{source_id}")).json()
        chunks = (await knowledge.get(f"{KNOWLEDGE}/{source_id}/chunks")).json()
        listed = (await knowledge.get(KNOWLEDGE)).json()["sources"][0]

        assert detail["content"] == POLICY and detail["priority"] == 100
        assert chunks == {"chunks": [POLICY]}
        assert listed["priority"] == 100 and listed["report"] is None


class TestTheMaintenanceCommand:
    async def test_sources_chunked_before_006_are_selected_for_rechunking(
        self,
        knowledge: AsyncClient,
        db: Session,
        tenant: uuid.UUID,
        llm: MockLLMProvider,
        storage: MockStorageProvider,
    ) -> None:
        from scripts.reindex_knowledge import _stale_sources

        fresh = await _indexed(knowledge, db, tenant, llm, storage)
        old = await _indexed(knowledge, db, tenant, llm, storage, content="Abrimos a las 7.")
        for chunk in _chunks(db, old):
            chunk.content_hash = None
        db.flush()

        stale = {source for t, source in _stale_sources(db, current="mock-embed", everything=False)}

        assert old in stale and fresh not in stale


class TestTheBusinessWebsite:
    SITE = {
        "https://tienda.pe/robots.txt": (404, "text/plain", ""),
        "https://tienda.pe/": (
            200,
            "text/html",
            "<html><head><title>Tienda</title></head><body>"
            "<p>Vendemos mochilas escolares.</p><a href='/envios'>Envíos</a></body></html>",
        ),
        "https://tienda.pe/envios": (
            200,
            "text/html",
            "<html><head><title>Envíos</title></head><body>"
            "<p>Enviamos a todo Lima en 24 horas.</p></body></html>",
        ),
    }

    async def test_the_site_is_read_indexed_and_resynced_by_hand(
        self,
        knowledge: AsyncClient,
        db: Session,
        tenant: uuid.UUID,
        llm: MockLLMProvider,
        storage: MockStorageProvider,
        queue: RecordingQueue,
    ) -> None:
        from app.providers.pdf import PypdfTextExtractor
        from app.providers.web import MockWebFetcher
        from app.workers.knowledge_indexer import index_source

        created = await knowledge.post(f"{KNOWLEDGE}/web", json={"url": "tienda.pe"})
        assert created.status_code == 202, created.text
        source_id = uuid.UUID(created.json()["id"])
        fetcher = MockWebFetcher(dict(self.SITE))

        async def run() -> Any:
            return await index_source(
                db,
                tenant,
                source_id,
                llm=llm,
                storage=storage,
                pdf_extractor=PypdfTextExtractor(),
                web_fetcher=fetcher,
            )

        assert (await run()).succeeded
        source = _source(db, source_id)
        assert source.type == "web" and source.priority == 50
        assert source.source_url == "https://tienda.pe/" and source.crawled_at is not None
        assert source.report == {
            "web": {
                "paginas": ["https://tienda.pe/", "https://tienda.pe/envios"],
                "omitidas": [],
                "robots_txt": False,
            }
        }
        assert source.char_count == len(source.content or "")
        assert any("24 horas" in c.content for c in _chunks(db, source_id))

        # The site changes; nothing re-reads it until the owner asks.
        fetcher.pages["https://tienda.pe/envios"] = (
            200,
            "text/html",
            "<html><body><p>Enviamos a todo Lima en 48 horas.</p></body></html>",
        )
        resynced = await knowledge.post(f"{KNOWLEDGE}/{source_id}/resync")
        assert resynced.status_code == 202
        assert queue.jobs[-1] == (INDEX_JOB, (str(tenant), str(source_id)))
        assert (await run()).succeeded
        chunks = " ".join(c.content for c in _chunks(db, source_id))
        assert "48 horas" in chunks and "24 horas" not in chunks

    async def test_an_empty_site_fails_with_what_to_do(
        self,
        knowledge: AsyncClient,
        db: Session,
        tenant: uuid.UUID,
        llm: MockLLMProvider,
        storage: MockStorageProvider,
    ) -> None:
        from app.providers.pdf import PypdfTextExtractor
        from app.providers.web import MockWebFetcher
        from app.services.web_crawl import EMPTY_SITE
        from app.workers.knowledge_indexer import index_source

        created = await knowledge.post(f"{KNOWLEDGE}/web", json={"url": "https://app.pe"})
        source_id = uuid.UUID(created.json()["id"])
        fetcher = MockWebFetcher(
            {"https://app.pe/": (200, "text/html", "<div id=root></div><script>x()</script>")}
        )

        outcome = await index_source(
            db,
            tenant,
            source_id,
            llm=llm,
            storage=storage,
            pdf_extractor=PypdfTextExtractor(),
            web_fetcher=fetcher,
        )

        assert outcome.status == "failed" and outcome.error_msg == EMPTY_SITE
        assert llm.embed_calls == []

    @pytest.mark.parametrize("url", ["localhost", "http://10.0.0.5", "ftp://tienda.pe"])
    async def test_an_address_that_is_not_a_website_is_refused(
        self, knowledge: AsyncClient, url: str
    ) -> None:
        refused = await knowledge.post(f"{KNOWLEDGE}/web", json={"url": url})

        assert refused.status_code == 400 and refused.json()["error"] == "invalid_url"

    async def test_an_operator_cannot_add_a_website(
        self, knowledge: AsyncClient, db: Session, account: Account
    ) -> None:
        _set_role(db, account, "operator")

        refused = await knowledge.post(f"{KNOWLEDGE}/web", json={"url": "tienda.pe"})

        assert refused.status_code == 403


class TestRetrievalWithSources:
    """Feature 006, C-003 and HU-05: one copy per passage, trusted sources first, cited."""

    async def test_a_passage_in_two_sources_is_shown_once_from_the_trusted_one(
        self,
        knowledge: AsyncClient,
        db: Session,
        tenant: uuid.UUID,
        llm: MockLLMProvider,
        storage: MockStorageProvider,
    ) -> None:
        from app.services.knowledge import retrieve

        await _indexed(knowledge, db, tenant, llm, storage, content=POLICY, kind="faq")
        web = await _indexed(knowledge, db, tenant, llm, storage, content=f"  {POLICY.upper()} ")
        await knowledge.patch(f"{KNOWLEDGE}/{web}", json={"priority": 50, "title": "Mi web"})
        vector = (await llm.embed([POLICY]))[0]

        passages = retrieve(db, tenant, vector, k=4)

        assert [p.title for p in passages] == ["Envíos"]
        assert passages[0].priority == 100

    async def test_when_sources_disagree_the_trusted_one_comes_first(
        self,
        knowledge: AsyncClient,
        db: Session,
        tenant: uuid.UUID,
        llm: MockLLMProvider,
        storage: MockStorageProvider,
    ) -> None:
        from app.agent.tools.catalog import consultar_conocimiento
        from app.agent.tools.registry import ToolContext

        web_text = "El envío a Lima cuesta 15 soles."
        faq = await _indexed(knowledge, db, tenant, llm, storage, content=POLICY, kind="faq")
        web = await _indexed(knowledge, db, tenant, llm, storage, content=web_text)
        await knowledge.patch(f"{KNOWLEDGE}/{faq}", json={"title": "Preguntas frecuentes"})
        await knowledge.patch(f"{KNOWLEDGE}/{web}", json={"priority": 50, "title": "Mi web"})

        # The question is the web's own words: the web passage is the nearest.
        result = await consultar_conocimiento(
            ToolContext(session=db, tenant_id=tenant, llm=llm), {"consulta": web_text}
        )

        text = result.render()
        assert text.index("[Preguntas frecuentes]") < text.index("[Mi web]")
        assert "vale el primero" in text
        assert result.data["fuentes"] == ["Preguntas frecuentes", "Mi web"]
