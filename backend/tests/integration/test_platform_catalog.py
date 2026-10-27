"""The platform catalogue: drafts, evals and publishing (017, TASK-466)."""

from __future__ import annotations

from typing import Any

import pytest
from fastapi import FastAPI
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import get_job_queue
from app.models.audit import AuditLog
from app.providers.llm import ChatResult, MockLLMProvider
from app.providers.llm.base import ToolCall
from app.repositories.catalog import CatalogRepository
from app.services.catalog_evals import EVALS_JOB, MARKER, run_evals
from app.services.catalog_knowledge import INDEX_CATALOG_JOB
from tests.integration.test_knowledge_ingestion import RecordingQueue
from tests.integration.test_platform_admin import _admin, _register

CAT = "/api/v1/platform-admin/catalog"


@pytest.fixture
def queue(app: FastAPI) -> RecordingQueue:
    recording = RecordingQueue()
    app.dependency_overrides[get_job_queue] = lambda: recording
    return recording


def _passing_llm(kind: str) -> MockLLMProvider:
    llm = MockLLMProvider()
    if kind == "proceso":
        llm.queue(
            ChatResult(
                content="",
                tool_calls=(ToolCall(id="c1", name="enviar_metodos_pago", arguments={}),),
            )
        )
    else:
        llm.queue(ChatResult(content="Sí, el envío es gratis a todo el Perú."))
        llm.queue(ChatResult(content=f"¡Hola! ¿Qué buscas? Síguenos en Instagram: {MARKER}."))
    return llm


class TestAccess:
    async def test_only_the_operator_sees_the_catalogue(
        self, api: AsyncClient, mail: Any, db: Session
    ) -> None:
        headers, _ = await _register(api, mail, db)

        assert (await api.get(CAT, headers=headers)).status_code == 404


class TestTheCycle:
    async def test_draft_edit_evals_and_publish(
        self, api: AsyncClient, mail: Any, db: Session, queue: RecordingQueue
    ) -> None:
        headers, _ = await _admin(api, mail, db)
        listed = (await api.get(CAT, headers=headers)).json()["items"]
        retail = next(e for e in listed if (e["kind"], e["key"]) == ("rubro", "retail"))

        draft = await api.post(f"{CAT}/rubro/retail/versions", headers=headers)
        second = await api.post(f"{CAT}/rubro/retail/versions", headers=headers)
        content = draft.json()
        content["reglas"].append({"rule_key": "rubro.nueva", "texto": "Regla nueva del rubro."})
        body = {k: content[k] for k in ("reglas", "faqs", "objeciones", "situaciones_sugeridas")}
        edited = await api.put(f"{CAT}/rubro/retail/versions/2", headers=headers, json=body)
        early = await api.post(f"{CAT}/rubro/retail/versions/2/publicar", headers=headers)
        queued = await api.post(f"{CAT}/rubro/retail/versions/2/evals", headers=headers)

        assert retail["publicada"] == 1
        assert draft.status_code == 201 and content["version"] == 2
        assert second.status_code == 409 and second.json()["error"] == "draft_exists"
        assert edited.status_code == 200 and edited.json()["evals_vigentes"] is False
        assert early.status_code == 409 and early.json()["error"] == "evals_not_passed"
        assert queued.status_code == 202
        assert queue.jobs[-1][0] == EVALS_JOB

        version = CatalogRepository(db).version("rubro", "retail", 2)
        assert version is not None
        run = await run_evals(db, version, _passing_llm("rubro"))
        db.commit()
        assert run.ok, run.casos

        published = await api.post(f"{CAT}/rubro/retail/versions/2/publicar", headers=headers)
        old = CatalogRepository(db).version("rubro", "retail", 1)

        assert published.status_code == 200, published.text
        assert published.json()["estado"] == "publicada"
        assert old is not None and old.estado == "retirada"
        assert queue.jobs[-1][0] == INDEX_CATALOG_JOB
        frozen = await api.put(f"{CAT}/rubro/retail/versions/2", headers=headers, json=body)
        assert frozen.status_code == 409 and frozen.json()["error"] == "version_frozen"
        actions = set(db.execute(select(AuditLog.action)).scalars())
        assert {"catalog.draft_created", "catalog.draft_edited", "catalog.published"} <= actions

    async def test_editing_after_the_evals_voids_them(
        self, api: AsyncClient, mail: Any, db: Session, queue: RecordingQueue
    ) -> None:
        headers, _ = await _admin(api, mail, db)
        content = (await api.post(f"{CAT}/rubro/retail/versions", headers=headers)).json()
        version = CatalogRepository(db).version("rubro", "retail", 2)
        assert version is not None
        await run_evals(db, version, _passing_llm("rubro"))
        db.commit()
        body = {k: content[k] for k in ("reglas", "faqs", "objeciones", "situaciones_sugeridas")}
        body["reglas"][0]["texto"] = "Otro texto."

        await api.put(f"{CAT}/rubro/retail/versions/2", headers=headers, json=body)
        refused = await api.post(f"{CAT}/rubro/retail/versions/2/publicar", headers=headers)

        assert refused.status_code == 409 and refused.json()["error"] == "evals_not_passed"

    async def test_a_process_rule_needs_its_stage(
        self, api: AsyncClient, mail: Any, db: Session, queue: RecordingQueue
    ) -> None:
        headers, _ = await _admin(api, mail, db)
        await api.post(f"{CAT}/proceso/servicios/versions", headers=headers)

        response = await api.put(
            f"{CAT}/proceso/servicios/versions/6",
            headers=headers,
            json={"reglas": [{"rule_key": "COBRO.x", "texto": "Sin etapa."}]},
        )

        assert response.status_code == 422 and response.json()["error"] == "invalid_content"


class TestTheEvals:
    async def test_a_process_version_passes_when_the_model_sends_the_methods(
        self, db: Session
    ) -> None:
        catalog = CatalogRepository(db)
        draft = catalog.new_draft("proceso", "productos")

        passing = await run_evals(db, draft, _passing_llm("proceso"))
        talking = MockLLMProvider()
        talking.queue(ChatResult(content="Te paso los datos luego."))
        failing = await run_evals(db, draft, talking)

        assert passing.ok, passing.casos
        assert not failing.ok
        assert any(c.nombre == "cobro_envia_metodos_de_pago" and not c.ok for c in failing.casos)

    async def test_no_model_means_the_run_fails(self, db: Session) -> None:
        draft = CatalogRepository(db).new_draft("rubro", "otros")

        run = await run_evals(db, draft, None)

        assert not run.ok
        assert any(c.nombre == "modelo_disponible" for c in run.casos)

    async def test_a_stage_without_rules_fails_the_structure(self, db: Session) -> None:
        from app.models.catalog import CatalogRule

        catalog = CatalogRepository(db)
        draft = catalog.new_draft("proceso", "servicios")
        for rule in catalog.rules(draft.id):
            if rule.etapa == "DATOS":
                db.delete(rule)
        db.flush()
        assert all(r.etapa != "DATOS" for r in db.query(CatalogRule).filter_by(version_id=draft.id))

        run = await run_evals(db, draft, _passing_llm("proceso"))

        case = next(c for c in run.casos if c.nombre == "cada_etapa_tiene_reglas")
        assert not case.ok and "DATOS" in case.detalle
