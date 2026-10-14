"""Situations: the API, the selector and the simulator's drafts (017, TASK-462 to 465)."""

from __future__ import annotations

import uuid
from typing import Any

import pytest
from fastapi import FastAPI
from httpx import AsyncClient
from sqlalchemy import update
from sqlalchemy.orm import Session

from app.agent.situations import select_situations
from app.api.deps import get_job_queue
from app.models.situation import Situation
from app.models.tenant import TenantUser
from app.providers.llm.base import LLMError
from app.providers.llm.offline import OfflineLLMProvider
from app.repositories.catalog import CatalogRepository
from app.services.situations import EMBED_JOB, SituationInput, SituationService, embed_pending
from tests.integration.test_knowledge_ingestion import RecordingQueue
from tests.integration.test_team import _owner

API = "/api/v1/assistant/situations"
TENANT = "/api/v1/tenant"

BODY = {
    "titulo": "Si piden factura",
    "cuando_aplica": "El cliente pide factura o boleta con RUC",
    "que_hace": "Pide su RUC y razón social con registrar_datos_cliente.",
}


@pytest.fixture
def queue(app: FastAPI) -> RecordingQueue:
    recording = RecordingQueue()
    app.dependency_overrides[get_job_queue] = lambda: recording
    return recording


async def _account(api: AsyncClient, mail: Any) -> tuple[uuid.UUID, dict[str, str]]:
    account, headers = await _owner(api, mail)
    return uuid.UUID(account["tenant_id"]), headers


class TestTheApi:
    async def test_create_starts_as_a_draft_and_asks_for_the_embedding(
        self, api: AsyncClient, mail: Any, queue: RecordingQueue
    ) -> None:
        tenant_id, headers = await _account(api, mail)

        created = await api.post(API, headers=headers, json={**BODY, "etapas": ["DATOS", "COBRO"]})

        assert created.status_code == 201, created.text
        body = created.json()
        assert (body["estado"], body["origen"]) == ("borrador", "manual")
        assert body["etapas"] == ["COBRO", "DATOS"]
        assert body["lista_para_coincidir"] is False
        assert queue.jobs == [(EMBED_JOB, (str(tenant_id),))]

    async def test_a_lost_job_does_not_fail_the_save(
        self, api: AsyncClient, mail: Any, queue: RecordingQueue
    ) -> None:
        _, headers = await _account(api, mail)
        queue.broken = True

        assert (await api.post(API, headers=headers, json=BODY)).status_code == 201

    @pytest.mark.parametrize(
        ("change", "error"),
        [
            ({"que_hace": "x" * 1001}, None),
            ({"etapas": ["INVENTADA"]}, "unknown_stage"),
            ({"media_asset_ids": [str(uuid.uuid4())]}, "unknown_media"),
        ],
    )
    async def test_what_cannot_be_saved(
        self,
        api: AsyncClient,
        mail: Any,
        queue: RecordingQueue,
        change: dict[str, Any],
        error: str | None,
    ) -> None:
        _, headers = await _account(api, mail)

        response = await api.post(API, headers=headers, json={**BODY, **change})

        assert response.status_code in (400, 422)
        if error:
            assert response.json()["error"] == error

    async def test_activate_edit_filter_and_delete(
        self, api: AsyncClient, mail: Any, queue: RecordingQueue
    ) -> None:
        _, headers = await _account(api, mail)
        sid = (await api.post(API, headers=headers, json=BODY)).json()["id"]

        assert (await api.post(f"{API}/{sid}/activar", headers=headers)).json()[
            "estado"
        ] == "activa"
        edited = await api.put(
            f"{API}/{sid}", headers=headers, json={**BODY, "siempre": True, "derivar": True}
        )
        assert edited.json()["siempre"] is True and edited.json()["estado"] == "activa"
        assert [
            s["id"]
            for s in (await api.get(API, headers=headers, params={"estado": "activa"})).json()[
                "items"
            ]
        ] == [sid]
        assert (await api.get(API, headers=headers, params={"estado": "borrador"})).json()[
            "items"
        ] == []
        assert (await api.post(f"{API}/{sid}/desactivar", headers=headers)).json()[
            "estado"
        ] == "inactiva"
        assert (await api.delete(f"{API}/{sid}", headers=headers)).status_code == 204
        assert (await api.get(f"{API}/{sid}", headers=headers)).status_code == 404

    async def test_an_operator_cannot_configure(
        self, api: AsyncClient, mail: Any, db: Session, queue: RecordingQueue
    ) -> None:
        tenant_id, headers = await _account(api, mail)
        db.execute(
            update(TenantUser).where(TenantUser.tenant_id == tenant_id).values(role="operator")
        )
        db.flush()

        assert (await api.post(API, headers=headers, json=BODY)).status_code == 403

    async def test_from_a_rubro_suggestion_with_its_fields(
        self, api: AsyncClient, mail: Any, db: Session, queue: RecordingQueue
    ) -> None:
        _, headers = await _account(api, mail)
        await api.put(
            f"{TENANT}/business-profile",
            headers=headers,
            json={"business_name": "Capacita", "industry": "instituto"},
        )
        catalog = CatalogRepository(db)
        version = catalog.published("rubro", "instituto")
        assert version is not None
        group = next(s for s in catalog.suggested_situations(version.id) if s.campos)
        url = f"{API}/desde-sugerida/{group.id}"

        missing = await api.post(url, headers=headers, json={"valores": {}})
        made = await api.post(
            url, headers=headers, json={"valores": dict.fromkeys(group.campos, "5")}
        )
        other = catalog.published("rubro", "retail")
        assert other is not None
        foreign = catalog.suggested_situations(other.id)[0]

        assert missing.status_code == 422 and missing.json()["error"] == "missing_fields"
        assert made.status_code == 201
        assert made.json()["origen"] == "sugerida_rubro"
        assert ": 5)" in made.json()["que_hace"]
        assert (
            await api.post(f"{API}/desde-sugerida/{foreign.id}", headers=headers, json={})
        ).json()["error"] == "unknown_suggestion"

    async def test_the_wizard_free_text_becomes_the_migrated_situation(
        self, api: AsyncClient, mail: Any, queue: RecordingQueue
    ) -> None:
        _, headers = await _account(api, mail)

        saved = await api.put(
            f"{TENANT}/agent-config",
            headers=headers,
            json={
                "agent_name": "Leo",
                "restrictions": "No ofrecer cuotas.",
                "tratamiento": "usted",
            },
        )
        situations = (await api.get(API, headers=headers)).json()["items"]

        assert saved.status_code == 200, saved.text
        assert saved.json()["restrictions"] == "No ofrecer cuotas."
        assert saved.json()["tratamiento"] == "usted"
        assert [(s["origen"], s["siempre"], s["estado"]) for s in situations] == [
            ("migrada", True, "activa")
        ]
        cleared = await api.put(
            f"{TENANT}/agent-config", headers=headers, json={"restrictions": ""}
        )
        assert cleared.json()["restrictions"] is None


# ------------------------------------------------------------------ selector


def _add(db: Session, tenant_id: uuid.UUID, **values: Any) -> Situation:
    data = {**BODY, **values}
    estado = data.pop("estado", "activa")
    situation = SituationService(db, tenant_id).create(SituationInput(**data), by=None)
    situation.estado = estado
    db.flush()
    return situation


class _FailingEmbed(OfflineLLMProvider):
    async def embed(self, texts: Any, *, input_type: Any = "passage") -> list[list[float]]:
        raise LLMError("down")


class TestTheSelector:
    @pytest.fixture
    def tenant_id(self, db: Session) -> uuid.UUID:
        from app.models.tenant import Tenant

        row = Tenant(name=f"Sel {uuid.uuid4().hex[:6]}")
        db.add(row)
        db.flush()
        return row.id

    async def _select(
        self, db: Session, tenant_id: uuid.UUID, message: str, stage: str = "COBRO", **kw: Any
    ) -> list[str]:
        llm = kw.pop("llm", OfflineLLMProvider(environment="test"))
        await embed_pending(db, tenant_id, OfflineLLMProvider(environment="test"))
        chosen = await select_situations(db, tenant_id, stage=stage, message=message, llm=llm, **kw)
        return [s.titulo for s in chosen]

    async def test_always_plus_the_matching_ones_of_the_stage(
        self, db: Session, tenant_id: uuid.UUID
    ) -> None:
        _add(db, tenant_id, titulo="Siempre", siempre=True, cuando_aplica="todo")
        _add(db, tenant_id, titulo="Factura")
        _add(db, tenant_id, titulo="Otra etapa", etapas=["INICIO"])
        _add(db, tenant_id, titulo="Nada que ver", cuando_aplica="zzz qqq www")
        _add(db, tenant_id, titulo="Borrador", estado="borrador")

        chosen = await self._select(db, tenant_id, "El cliente pide factura o boleta con RUC")

        assert chosen == ["Siempre", "Factura"]

    async def test_the_maximum_is_kept(self, db: Session, tenant_id: uuid.UUID) -> None:
        _add(db, tenant_id, titulo="S1", siempre=True)
        _add(db, tenant_id, titulo="S2", siempre=True)
        _add(db, tenant_id, titulo="Factura")

        chosen = await self._select(
            db, tenant_id, "El cliente pide factura o boleta con RUC", max_situaciones=2
        )

        assert chosen == ["S1", "S2"]

    async def test_a_failed_embedding_leaves_only_the_always_ones(
        self, db: Session, tenant_id: uuid.UUID
    ) -> None:
        _add(db, tenant_id, titulo="Siempre", siempre=True)
        _add(db, tenant_id, titulo="Factura")

        chosen = await self._select(
            db,
            tenant_id,
            "El cliente pide factura o boleta con RUC",
            llm=_FailingEmbed(environment="test"),
        )

        assert chosen == ["Siempre"]

    async def test_a_draft_enters_only_when_the_simulator_asks(
        self, db: Session, tenant_id: uuid.UUID
    ) -> None:
        draft = _add(db, tenant_id, titulo="Borrador", estado="borrador")
        message = "El cliente pide factura o boleta con RUC"

        assert await self._select(db, tenant_id, message) == []
        assert await self._select(db, tenant_id, message, drafts=[draft.id]) == ["Borrador"]

    async def test_no_embedding_call_without_situations_to_match(
        self, db: Session, tenant_id: uuid.UUID
    ) -> None:
        _add(db, tenant_id, titulo="Siempre", siempre=True)
        llm = _FailingEmbed(environment="test")

        chosen = await select_situations(db, tenant_id, stage="COBRO", message="hola", llm=llm)

        assert [s.titulo for s in chosen] == ["Siempre"]
