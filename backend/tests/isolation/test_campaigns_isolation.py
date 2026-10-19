"""Templates, contacts, tags, settings and campaigns never cross businesses (Feature 011)."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

import pytest
from httpx import AsyncClient
from sqlalchemy.orm import Session

from app.models.campaigns import MessageTemplate
from app.models.tenant import Tenant
from app.repositories.campaigns import (
    CampaignRecipientRepository,
    CampaignRepository,
    ContactOptinRepository,
    ContactTagRepository,
    MessageTemplateRepository,
)
from app.services.campaigns.segments import Segment, count
from tests.integration.campaign_fixtures import business, customer, template
from tests.integration.conftest import TEST_ENCRYPTION_KEY
from tests.integration.test_orders_api import operator_for

T = "/api/v1/tenant/templates"
C = "/api/v1/tenant/contacts"
K = "/api/v1/tenant/campaigns"


async def test_another_business_sees_and_touches_nothing(
    api: AsyncClient, mail: Any, db: Session
) -> None:
    account_a, owner_a, _ = await operator_for(api, mail, db)
    account_b, owner_b, _ = await operator_for(api, mail, db)
    tenant_b = db.get(Tenant, uuid.UUID(str(account_b["tenant_id"])))
    assert tenant_b is not None
    secret = customer(db, tenant_b, name="Secreto", tags=["vip"])
    db.commit()
    template_b = (await api.post(f"{T}/library/aviso_promocion/clone", headers=owner_b)).json()
    utility_b = (await api.post(f"{T}/library/pago_pendiente/clone", headers=owner_b)).json()
    row = db.get(MessageTemplate, uuid.UUID(template_b["id"]))
    assert row is not None
    row.status = "approved"
    db.commit()
    campaign_b = (
        await api.post(K, headers=owner_b, json={"name": "B", "template_id": template_b["id"]})
    ).json()

    # Lists
    assert (await api.get(T, headers=owner_a)).json() == []
    assert (await api.get(C, headers=owner_a)).json()["total"] == 0
    assert (await api.get(C, headers=owner_a, params={"q": "Secreto"})).json()["total"] == 0
    assert (await api.get(f"{C}/tags", headers=owner_a)).json() == []
    assert (await api.get(K, headers=owner_a)).json() == []

    # Templates
    tid = template_b["id"]
    body = {"label": "x", "category": "marketing", "body": "Hola {{nombre}}, promo."}
    assert (await api.put(f"{T}/{tid}", headers=owner_a, json=body)).status_code == 404
    assert (await api.post(f"{T}/{tid}/submit", headers=owner_a)).status_code == 404
    assert (await api.post(f"{T}/{tid}/duplicate", headers=owner_a)).status_code == 404
    assert (await api.delete(f"{T}/{tid}", headers=owner_a)).status_code == 404

    # Contacts
    assert (
        await api.put(f"{C}/{secret.id}/tags", headers=owner_a, json={"tags": ["x"]})
    ).status_code == 404

    # A's settings and campaigns cannot point at B's templates
    assert (
        await api.put(
            f"{K}/settings",
            headers=owner_a,
            json={"optin_preguntar": True, "seguimiento_plantilla_id": utility_b["id"]},
        )
    ).status_code == 422
    assert (
        await api.post(K, headers=owner_a, json={"name": "A", "template_id": tid})
    ).status_code == 422
    assert (
        await api.post(f"{K}/estimate", headers=owner_a, json={"template_id": tid})
    ).status_code == 422

    # Campaigns
    cid = campaign_b["id"]
    assert (await api.get(f"{K}/{cid}", headers=owner_a)).status_code == 404
    for action in ("schedule", "pause", "resume", "cancel"):
        assert (await api.post(f"{K}/{cid}/{action}", headers=owner_a)).status_code == 404
    assert (await api.delete(f"{K}/{cid}", headers=owner_a)).status_code == 404
    assert (await api.get(f"{K}/{cid}", headers=owner_b)).status_code == 200
    _ = account_a


def test_repositories_and_segments_are_scoped(db: Session, monkeypatch: pytest.MonkeyPatch) -> None:
    from app.core.config import get_settings

    monkeypatch.setattr(get_settings(), "credentials_encryption_key", TEST_ENCRYPTION_KEY)
    tenant_a, _ = business(db)
    tenant_b, _ = business(db)
    contact_b = customer(db, tenant_b, tags=["vip"])
    template(db, tenant_b)

    assert MessageTemplateRepository(db, tenant_a.id).ordered() == []
    assert ContactOptinRepository(db, tenant_a.id).for_contact(contact_b.id) is None
    assert ContactTagRepository(db, tenant_a.id).for_contacts([contact_b.id]) == {}
    assert ContactTagRepository(db, tenant_a.id).all_tags() == []
    assert CampaignRepository(db, tenant_a.id).ordered() == []
    assert (
        CampaignRecipientRepository(db, tenant_a.id).sent_since(datetime(2000, 1, 1, tzinfo=UTC))
        == 0
    )
    assert count(db, tenant_a.id, Segment(), now=datetime.now(UTC)) == 0
    assert count(db, tenant_a.id, Segment(etiquetas=["vip"]), now=datetime.now(UTC)) == 0
    assert count(db, tenant_b.id, Segment(etiquetas=["vip"]), now=datetime.now(UTC)) == 1
