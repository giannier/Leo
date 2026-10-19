"""The 011 API: templates, contacts with permission, settings and campaigns."""

from __future__ import annotations

import json
import uuid
from typing import Any

import pytest
from httpx import AsyncClient
from sqlalchemy.orm import Session

from app.api.deps import get_whatsapp_provider
from app.core.crypto import encrypt_secret
from app.models.campaigns import MessageTemplate
from app.models.tenant import Tenant
from app.models.whatsapp import WhatsAppNumber
from tests.integration.campaign_fixtures import FakeWhatsApp, customer
from tests.integration.test_orders_api import operator_for

T = "/api/v1/tenant/templates"
C = "/api/v1/tenant/contacts"
K = "/api/v1/tenant/campaigns"


@pytest.fixture
def whatsapp(app: Any) -> FakeWhatsApp:
    fake = FakeWhatsApp()
    app.dependency_overrides[get_whatsapp_provider] = lambda: fake
    return fake


def _number(db: Session, tenant_id: uuid.UUID) -> None:
    db.add(
        WhatsAppNumber(
            tenant_id=tenant_id,
            phone_e164=f"+5191{uuid.uuid4().int % 10**7:07d}",
            display_name="Tienda",
            provider="ycloud",
            status="connected",
            credentials_enc=encrypt_secret(
                json.dumps(
                    {
                        "waba_id": "w1",
                        "phone_number_id": "112233445566",
                        "api_key": "yc_live_abc123",
                    }
                )
            ),
        )
    )
    db.commit()


async def _setup(
    api: AsyncClient, mail: Any, db: Session
) -> tuple[uuid.UUID, dict[str, str], dict[str, str]]:
    account, owner, operator = await operator_for(api, mail, db)
    return uuid.UUID(str(account["tenant_id"])), owner, operator


class TestTemplates:
    async def test_from_the_library_to_meta(
        self, api: AsyncClient, mail: Any, db: Session, whatsapp: FakeWhatsApp
    ) -> None:
        tenant_id, owner, operator = await _setup(api, mail, db)
        library = (await api.get(f"{T}/library", headers=owner)).json()
        assert {"aviso_promocion", "pago_pendiente"} <= {e["key"] for e in library}

        cloned = await api.post(f"{T}/library/aviso_promocion/clone", headers=owner)
        assert cloned.status_code == 201, cloned.text
        template = cloned.json()
        assert template["status"] == "draft" and template["variables"] == ["nombre", "negocio"]

        no_number = await api.post(f"{T}/{template['id']}/submit", headers=owner)
        assert no_number.status_code == 409 and no_number.json()["error"] == "sin_numero"

        _number(db, tenant_id)
        submitted = await api.post(f"{T}/{template['id']}/submit", headers=owner)
        assert submitted.status_code == 200 and submitted.json()["status"] == "pending"
        assert whatsapp.submitted[0].body.startswith("Hola {{1}}")

        edit = await api.put(
            f"{T}/{template['id']}",
            headers=owner,
            json={"label": "x", "category": "marketing", "body": "Hola {{nombre}}, promo."},
        )
        assert edit.status_code == 409

        # An operator reads, but does not write.
        assert (await api.get(T, headers=operator)).status_code == 200
        assert (
            await api.post(
                T, headers=operator, json={"label": "x", "category": "utility", "body": "Hola."}
            )
        ).status_code == 403

    async def test_a_bad_body_says_why(self, api: AsyncClient, mail: Any, db: Session) -> None:
        _, owner, _ = await _setup(api, mail, db)
        response = await api.post(
            T,
            headers=owner,
            json={"label": "Promo", "category": "marketing", "body": "Hola {{apellido}}, promo."},
        )
        assert response.status_code == 422
        assert response.json()["error"] == "variable_desconocida"


class TestContacts:
    async def test_list_filter_tag_and_import(
        self, api: AsyncClient, mail: Any, db: Session
    ) -> None:
        tenant_id, owner, operator = await _setup(api, mail, db)
        tenant = db.get(Tenant, tenant_id)
        assert tenant is not None
        yes = customer(db, tenant, name="Ana")
        customer(db, tenant, name="Luis", permission=None)
        db.commit()

        page = (await api.get(C, headers=owner)).json()
        assert page["total"] == 2 and page["con_permiso"] == 1
        granted = (await api.get(C, headers=owner, params={"permiso": "granted"})).json()
        assert [c["id"] for c in granted["items"]] == [str(yes.id)]
        assert (await api.get(C, headers=owner, params={"permiso": "none"})).json()["total"] == 1

        tagged = await api.put(
            f"{C}/{yes.id}/tags", headers=operator, json={"tags": ["VIP", "vip", "feria"]}
        )
        assert tagged.json() == ["feria", "vip"]
        assert (await api.get(f"{C}/tags", headers=owner)).json() == [
            {"tag": "feria", "total": 1},
            {"tag": "vip", "total": 1},
        ]
        assert (await api.get(C, headers=owner, params={"tag": "vip"})).json()["total"] == 1

        info = (await api.get(f"{C}/import", headers=owner)).json()
        assert info["disponibles"] == 200 and "Ley 29733" in info["declaracion"]
        refused = await api.post(
            f"{C}/import", headers=owner, files={"file": ("l.csv", b"telefono\n987654300\n")}
        )
        assert refused.status_code == 422 and refused.json()["error"] == "sin_declaracion"
        done = await api.post(
            f"{C}/import",
            headers=owner,
            files={"file": ("l.csv", b"telefono;nombre\n987654300;Rosa\n")},
            data={"acepto": "true", "etiquetas": "feria"},
        )
        assert done.status_code == 200, done.text
        assert done.json()["importados"] == 1
        assert (await api.get(f"{C}/import", headers=owner)).json()["disponibles"] == 199
        assert (await api.get(C, headers=owner, params={"tag": "feria"})).json()["total"] == 2
        assert (
            await api.post(f"{C}/import", headers=operator, files={"file": ("l.csv", b"x")})
        ).status_code == 403


class TestSettingsAndCampaigns:
    async def test_a_whole_campaign(
        self, api: AsyncClient, mail: Any, db: Session, whatsapp: FakeWhatsApp
    ) -> None:
        tenant_id, owner, operator = await _setup(api, mail, db)
        _number(db, tenant_id)
        tenant = db.get(Tenant, tenant_id)
        assert tenant is not None
        customer(db, tenant, name="Ana", tags=["vip"])
        customer(db, tenant, name="Luis")
        db.commit()
        marketing = (await api.post(f"{T}/library/aviso_promocion/clone", headers=owner)).json()
        utility = (await api.post(f"{T}/library/pago_pendiente/clone", headers=owner)).json()
        for row in (marketing, utility):
            template = db.get(MessageTemplate, uuid.UUID(row["id"]))
            assert template is not None
            template.status = "approved"
        db.commit()

        wrong = await api.put(
            f"{K}/settings",
            headers=owner,
            json={"optin_preguntar": False, "seguimiento_plantilla_id": marketing["id"]},
        )
        assert wrong.status_code == 422
        saved = await api.put(
            f"{K}/settings",
            headers=owner,
            json={"optin_preguntar": False, "seguimiento_plantilla_id": utility["id"]},
        )
        assert saved.json()["optin_preguntar"] is False
        assert saved.json()["seguimiento_plantilla_id"] == utility["id"]

        estimate = await api.post(
            f"{K}/estimate",
            headers=owner,
            json={"template_id": marketing["id"], "segment": {"etiquetas": ["vip"]}},
        )
        assert estimate.json()["destinatarios"] == 1 and estimate.json()["tarifa_usd"] == "0.07030"

        created = await api.post(
            K,
            headers=owner,
            json={
                "name": "VIP octubre",
                "template_id": marketing["id"],
                "segment": {"etiquetas": ["vip"]},
            },
        )
        assert created.status_code == 201, created.text
        campaign = created.json()
        assert campaign["status"] == "draft"

        scheduled = await api.post(f"{K}/{campaign['id']}/schedule", headers=owner)
        assert scheduled.status_code == 200, scheduled.text
        assert scheduled.json()["status"] == "scheduled"
        assert scheduled.json()["estimated_cost_usd"] == "0.0700"
        assert (await api.post(f"{K}/{campaign['id']}/schedule", headers=owner)).status_code == 409

        paused = await api.post(f"{K}/{campaign['id']}/pause", headers=owner)
        assert paused.json()["status"] == "paused" and paused.json()["pause_reason"] == "persona"
        resumed = await api.post(f"{K}/{campaign['id']}/resume", headers=owner)
        assert resumed.json()["status"] == "scheduled"

        seen = await api.get(f"{K}/{campaign['id']}", headers=operator)
        assert seen.status_code == 200
        assert seen.json()["estimated_cost_usd"] is None  # an operator sees no money
        assert "monto_vendido" not in seen.json()["report"]
        assert (await api.post(f"{K}/{campaign['id']}/cancel", headers=operator)).status_code == 403

        canceled = await api.post(f"{K}/{campaign['id']}/cancel", headers=owner)
        assert canceled.json()["status"] == "canceled"
        listed = (await api.get(K, headers=owner)).json()
        assert [c["id"] for c in listed] == [campaign["id"]]

    async def test_the_meta_rates_are_the_platforms(
        self, api: AsyncClient, mail: Any, db: Session
    ) -> None:
        _, owner, _ = await _setup(api, mail, db)
        assert (
            await api.get("/api/v1/platform-admin/meta-rates", headers=owner)
        ).status_code == 404  # 404-shaped, like every platform-admin route
