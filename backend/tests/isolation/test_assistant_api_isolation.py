"""The assistant's configuration API never crosses businesses (017 and 004 §1–§3).

Two owners. Everything B creates must be invisible to A, and every id of B's
must answer 404 to A: FAQs, promotions, situations, media and the rule switches.
"""

from __future__ import annotations

from typing import Any

import pytest
from fastapi import FastAPI
from httpx import AsyncClient
from sqlalchemy.orm import Session

from app.api.deps import get_job_queue, get_storage_provider
from app.providers.storage.mock import MockStorageProvider
from app.repositories.layers import TenantRuleSwitchRepository
from tests.integration.test_knowledge_ingestion import RecordingQueue
from tests.isolation.test_payment_isolation import _owner

A = "/api/v1/assistant"
PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 100


@pytest.fixture(autouse=True)
def _doubles(app: FastAPI) -> None:
    queue, store = RecordingQueue(), MockStorageProvider()
    app.dependency_overrides[get_job_queue] = lambda: queue
    app.dependency_overrides[get_storage_provider] = lambda: store


class TestOneBusinessNeverReachesAnothersConfiguration:
    async def test_lists_and_ids_are_scoped(self, api: AsyncClient, mail: Any) -> None:
        a = await _owner(api, mail, "a")
        b = await _owner(api, mail, "b")
        faq = (
            await api.post(f"{A}/faqs", headers=b, json={"pregunta": "¿Envíos?", "respuesta": "Sí"})
        ).json()
        promo = (
            await api.post(
                f"{A}/promotions",
                headers=b,
                json={"nombre": "10 %", "tipo": "porcentaje", "valor": "10.00"},
            )
        ).json()
        situation = (
            await api.post(
                f"{A}/situations",
                headers=b,
                json={"titulo": "Factura", "cuando_aplica": "Pide factura", "que_hace": "RUC"},
            )
        ).json()
        media = (
            await api.post(
                f"{A}/media",
                headers=b,
                data={"clave": "qr", "titulo": "QR", "cuando_usar": "Al pagar"},
                files={"file": ("qr.png", PNG)},
            )
        ).json()

        for path in ("faqs", "promotions", "situations", "media"):
            assert (await api.get(f"{A}/{path}", headers=a)).json()["items"] == [], path
        assert (
            await api.patch(f"{A}/faqs/{faq['id']}", headers=a, json={"respuesta": "x"})
        ).status_code == 404
        assert (await api.delete(f"{A}/promotions/{promo['id']}", headers=a)).status_code == 404
        assert (await api.get(f"{A}/situations/{situation['id']}", headers=a)).status_code == 404
        assert (
            await api.post(f"{A}/situations/{situation['id']}/activar", headers=a)
        ).status_code == 404
        assert (await api.delete(f"{A}/media/{media['id']}", headers=a)).status_code == 404
        assert (await api.get(f"{A}/situations/{situation['id']}", headers=b)).status_code == 200

    async def test_a_rule_switch_stays_in_its_business(
        self, api: AsyncClient, mail: Any, db: Session
    ) -> None:
        a = await _owner(api, mail, "a")
        b = await _owner(api, mail, "b")

        await api.put(
            f"{A}/rules/proceso/COBRO.pedido_datos_completos", headers=b, json={"apagada": True}
        )
        mine = (await api.get(f"{A}/process", headers=a)).json()

        cobro = next(s for s in mine["etapas"] if s["key"] == "COBRO")
        assert not any(r["apagada"] for r in cobro["reglas"])
        tenant_a = (await api.get("/api/v1/tenant", headers=a)).json()["id"]
        import uuid

        assert TenantRuleSwitchRepository(db, uuid.UUID(tenant_a)).off_keys("proceso") == set()
