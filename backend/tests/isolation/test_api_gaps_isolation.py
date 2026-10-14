"""Every id-taking endpoint of the 004 and the 017, over HTTP (TASK-446).

The audit of TASK-446 found endpoints whose tenant boundary was tested only at the
repository, or not at all: the order summary and a note's image, editing, deleting
and deactivating a situation, a category, a media file, a FAQ, a promotion, the
simulator reset and the team's invitations. Here tenant A uses tenant B's ids over
the real API and gets a 404, and what A reads never carries B's data.
"""

from __future__ import annotations

import uuid
from decimal import Decimal
from typing import Any

import pytest
from fastapi import FastAPI
from httpx import AsyncClient
from sqlalchemy.orm import Session

from app.api.deps import get_job_queue, get_storage_provider
from app.models.business import Product
from app.models.conversation import Conversation
from app.providers.storage.mock import MockStorageProvider
from tests.integration.conftest import encryption_key  # noqa: F401 - autouse: orders seed a number
from tests.integration.test_knowledge_ingestion import RecordingQueue
from tests.integration.test_orders_api import seed_order
from tests.isolation.test_payment_isolation import _owner

A = "/api/v1/assistant"
ORDERS = "/api/v1/tenant/orders"
PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 100


@pytest.fixture(autouse=True)
def _doubles(app: FastAPI) -> None:
    queue, store = RecordingQueue(), MockStorageProvider()
    app.dependency_overrides[get_job_queue] = lambda: queue
    app.dependency_overrides[get_storage_provider] = lambda: store


async def _tenant_id(api: AsyncClient, headers: dict[str, str]) -> uuid.UUID:
    return uuid.UUID((await api.get("/api/v1/tenant", headers=headers)).json()["id"])


async def _two(api: AsyncClient, mail: Any) -> tuple[dict[str, str], dict[str, str], uuid.UUID]:
    a = await _owner(api, mail, "a")
    b = await _owner(api, mail, "b")
    return a, b, await _tenant_id(api, b)


class TestOrders:
    async def test_the_summary_counts_only_your_sales(
        self, api: AsyncClient, mail: Any, db: Session
    ) -> None:
        a, _, tenant_b = await _two(api, mail)
        seed_order(db, str(tenant_b))

        summary = (await api.get(f"{ORDERS}/summary", headers=a)).json()

        assert summary["pendientes_revision"] == 0
        assert summary["conversiones"] == 0
        assert all(count == 0 for count in summary["por_estado"].values())

    async def test_a_note_image_of_a_foreign_order_is_a_404(
        self, api: AsyncClient, mail: Any, db: Session
    ) -> None:
        a, _, tenant_b = await _two(api, mail)
        order, _ = seed_order(db, str(tenant_b))
        order.notas = [{"texto": "imagen", "imagen_id": "img-1", "origen": "cliente"}]
        db.flush()

        response = await api.get(f"{ORDERS}/{order.id}/notes/img-1/image", headers=a)

        assert response.status_code == 404


class TestAssistantIds:
    async def test_situations_cannot_be_edited_deleted_or_switched_off(
        self, api: AsyncClient, mail: Any
    ) -> None:
        a, b, _ = await _two(api, mail)
        body = {"titulo": "Factura", "cuando_aplica": "Pide factura", "que_hace": "Pide el RUC"}
        situation = (await api.post(f"{A}/situations", headers=b, json=body)).json()
        await api.post(f"{A}/situations/{situation['id']}/activar", headers=b)
        url = f"{A}/situations/{situation['id']}"

        assert (await api.put(url, headers=a, json={**body, "titulo": "Mía"})).status_code == 404
        assert (await api.post(f"{url}/desactivar", headers=a)).status_code == 404
        assert (await api.delete(url, headers=a)).status_code == 404
        mine = (await api.get(url, headers=b)).json()
        assert (mine["titulo"], mine["estado"]) == ("Factura", "activa")

    async def test_a_category_faq_and_promotion_stay_with_their_business(
        self, api: AsyncClient, mail: Any, db: Session
    ) -> None:
        a, b, tenant_b = await _two(api, mail)
        db.add(Product(tenant_id=tenant_b, name="Polo", price=Decimal("30.00"), category="Ropa"))
        db.flush()
        from app.repositories.product import ProductCategoryRepository

        category = ProductCategoryRepository(db, tenant_b).ensure("Ropa")
        db.flush()
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

        renamed = await api.put(
            f"{A}/categories/{category.id}", headers=a, json={"nombre": "Hackeada"}
        )
        deleted = await api.delete(f"{A}/faqs/{faq['id']}", headers=a)
        edited = await api.put(
            f"{A}/promotions/{promo['id']}",
            headers=a,
            json={"nombre": "100 %", "tipo": "porcentaje", "valor": "100.00"},
        )

        assert (renamed.status_code, deleted.status_code, edited.status_code) == (404, 404, 404)
        assert (await api.get(f"{A}/categories", headers=a)).json()["items"] == []
        assert [
            c["nombre"] for c in (await api.get(f"{A}/categories", headers=b)).json()["items"]
        ] == ["Ropa"]
        assert [f["id"] for f in (await api.get(f"{A}/faqs", headers=b)).json()["items"]] == [
            faq["id"]
        ]
        promos = (await api.get(f"{A}/promotions", headers=b)).json()["items"]
        assert [(p["id"], p["nombre"]) for p in promos] == [(promo["id"], "10 %")]

    async def test_a_media_file_cannot_be_replaced_or_edited(
        self, api: AsyncClient, mail: Any
    ) -> None:
        a, b, _ = await _two(api, mail)
        media = (
            await api.post(
                f"{A}/media",
                headers=b,
                data={"clave": "qr", "titulo": "QR", "cuando_usar": "Al pagar"},
                files={"file": ("qr.png", PNG)},
            )
        ).json()

        replaced = await api.post(
            f"{A}/media/{media['id']}/file", headers=a, files={"file": ("x.png", PNG)}
        )
        edited = await api.patch(f"{A}/media/{media['id']}", headers=a, json={"titulo": "Mío"})

        assert (replaced.status_code, edited.status_code) == (404, 404)

    async def test_what_a_reads_about_its_assistant_never_names_b(
        self, api: AsyncClient, mail: Any
    ) -> None:
        a, b, _ = await _two(api, mail)
        await api.put(
            f"{A}/business-info/contacto",
            headers=b,
            json={"telefono": "+51911111111", "correo": "secreto-b@example.com"},
        )
        await api.post(
            f"{A}/faqs", headers=b, json={"pregunta": "¿Clave?", "respuesta": "Secreto de B"}
        )

        preview = (await api.get(f"{A}/prompt-preview", headers=a, params={"stage": "COBRO"})).text
        overview = (await api.get(f"{A}/overview", headers=a)).text
        rubro = (await api.get(f"{A}/rubro", headers=a)).text

        for text in (preview, overview, rubro):
            assert "Negocio b" not in text
            assert "Secreto de B" not in text
            assert "secreto-b@example.com" not in text


class TestSandboxAndTeam:
    async def test_a_foreign_simulator_conversation_cannot_be_reset(
        self, api: AsyncClient, mail: Any, db: Session
    ) -> None:
        a, _, tenant_b = await _two(api, mail)
        conversation = Conversation(tenant_id=tenant_b, channel="sandbox", status="open")
        db.add(conversation)
        db.flush()

        response = await api.post(
            f"/api/v1/tenant/sandbox/conversations/{conversation.id}/reset", headers=a
        )

        assert response.status_code == 404
        assert db.get(Conversation, conversation.id) is not None

    async def test_the_team_list_never_shows_another_business_invitations(
        self, api: AsyncClient, mail: Any
    ) -> None:
        a, b, _ = await _two(api, mail)
        invited = await api.post(
            "/api/v1/tenant/team/invitations",
            headers=b,
            json={"email": "invitada-de-b@example.com", "role": "operator"},
        )
        assert invited.status_code in (200, 201), invited.text

        team = (await api.get("/api/v1/tenant/team", headers=a)).text

        assert "invitada-de-b@example.com" not in team
