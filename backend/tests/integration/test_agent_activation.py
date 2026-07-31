"""Turning the agent on, and everything that must be true first (TASK-019).

Activation is the moment a business goes live: from here the agent answers
real customers on a real number, in the business's name. The guards exist to
prevent one specific embarrassment - an agent that greets a paying customer
and then cannot say what the business sells or how to pay for it.

Deactivation is tested for the opposite property. It is the stop button
somebody presses because the agent is saying something wrong to real customers
right now, so it must work on the first press, from any state.
"""

from __future__ import annotations

import re
import uuid
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any
from urllib.parse import parse_qs, urlparse

from httpx import AsyncClient
from sqlalchemy.orm import Session

from app.models.business import AgentConfig, BusinessProfile, PaymentMethodBusiness, Product
from app.models.tenant import Tenant
from app.models.user import User
from app.models.whatsapp import WhatsAppNumber

AUTH = "/api/v1/auth"
TENANT = "/api/v1/tenant"
AGENT = "/api/v1/tenant/agent"
PASSWORD = "una-clave-suficientemente-larga"
_HREF = re.compile(r'href="([^"]+)"')


def _link_token(message: Any) -> str:
    for href in _HREF.findall(message.html):
        query = parse_qs(urlparse(href).query)
        if "token" in query:
            return str(query["token"][0])
    raise AssertionError("no tokenised link in the email body")


def _unique_number() -> str:
    return f"+519{uuid.uuid4().int % 10**8:08d}"


async def _owner(api: AsyncClient, mail: Any, db: Session) -> tuple[dict[str, str], Tenant, User]:
    body = {
        "full_name": "Ana Quispe",
        "email": f"ana-{uuid.uuid4().hex[:12]}@example.com",
        "password": PASSWORD,
        "phone_e164": _unique_number(),
        "business_name": "Panaderia Quispe",
    }
    created = await api.post(f"{AUTH}/register", json=body)
    assert created.status_code == 201, created.text
    await api.post(f"{AUTH}/verify-email", json={"token": _link_token(mail.last)})

    user = db.get(User, uuid.UUID(created.json()["user_id"]))
    assert user is not None

    session = await api.post(f"{AUTH}/login", json={"email": body["email"], "password": PASSWORD})
    headers = {"Authorization": f"Bearer {session.json()['access_token']}"}
    tenant = db.get(Tenant, uuid.UUID((await api.get(TENANT, headers=headers)).json()["id"]))
    assert tenant is not None
    return headers, tenant, user


def _complete_checklist(db: Session, tenant: Tenant, user: User, *, number: bool = True) -> None:
    """Everything `build_checklist` asks for. Written directly rather than
    driven through the wizard: the wizard has its own suite, and driving it
    here would couple two files that fail for different reasons."""
    user.phone_verified_at = datetime.now(UTC)
    db.add(
        BusinessProfile(
            tenant_id=tenant.id,
            business_name="Panaderia Quispe",
            industry="retail",
            description="Panaderia artesanal en Miraflores",
        )
    )
    db.add(
        AgentConfig(
            tenant_id=tenant.id,
            agent_name="Leo",
            tone="cercano",
            objective="ambos",
            is_active=False,
        )
    )
    db.add(
        Product(
            tenant_id=tenant.id,
            name="Pan ciabatta",
            price=Decimal("8.50"),
            is_active=True,
        )
    )
    db.add(
        PaymentMethodBusiness(
            tenant_id=tenant.id,
            method="yape",
            instructions="Yape al 999888777 a nombre de Panaderia Quispe",
        )
    )
    if number:
        db.add(
            WhatsAppNumber(
                tenant_id=tenant.id,
                phone_e164=_unique_number(),
                display_name="Panaderia Leo",
                status="connected",
            )
        )
    db.commit()


class TestTheGuards:
    async def test_an_empty_account_cannot_activate(
        self, api: AsyncClient, mail: Any, db: Session
    ) -> None:
        headers, _, _ = await _owner(api, mail, db)
        response = await api.post(f"{AGENT}/activate", headers=headers)
        assert response.status_code == 409
        assert response.json()["error"] == "checklist_incomplete"

    async def test_a_complete_checklist_without_a_number_cannot_activate(
        self, api: AsyncClient, mail: Any, db: Session
    ) -> None:
        """The distinct error matters: "finish the checklist" sent to somebody
        whose checklist is finished is a support ticket."""
        headers, tenant, user = await _owner(api, mail, db)
        _complete_checklist(db, tenant, user, number=False)

        response = await api.post(f"{AGENT}/activate", headers=headers)
        assert response.status_code == 409
        assert response.json()["error"] == "number_not_connected"

    async def test_a_paused_tenant_cannot_activate(
        self, api: AsyncClient, mail: Any, db: Session
    ) -> None:
        """Accepting this would leave the panel showing an active agent that
        answers nobody, because the pause is what stops it."""
        headers, tenant, user = await _owner(api, mail, db)
        _complete_checklist(db, tenant, user)
        tenant.status = "paused"
        db.commit()

        response = await api.post(f"{AGENT}/activate", headers=headers)
        assert response.status_code == 409
        assert response.json()["error"] == "tenant_not_active"

    async def test_no_token_is_401(self, api: AsyncClient) -> None:
        assert (await api.post(f"{AGENT}/activate")).status_code == 401


class TestActivation:
    async def test_a_ready_account_activates(
        self, api: AsyncClient, mail: Any, db: Session
    ) -> None:
        headers, tenant, user = await _owner(api, mail, db)
        _complete_checklist(db, tenant, user)

        response = await api.post(f"{AGENT}/activate", headers=headers)
        assert response.status_code == 204

        config = db.get(AgentConfig, tenant.id)
        assert config is not None
        db.refresh(config)
        assert config.is_active is True
        assert config.activated_first_at is not None

    async def test_the_first_activation_timestamp_never_moves(
        self, api: AsyncClient, mail: Any, db: Session
    ) -> None:
        """It answers "how long from registration to a working agent?", one of
        this feature's success metrics. A value that moved on every
        re-activation would answer nothing."""
        headers, tenant, user = await _owner(api, mail, db)
        _complete_checklist(db, tenant, user)

        await api.post(f"{AGENT}/activate", headers=headers)
        config = db.get(AgentConfig, tenant.id)
        assert config is not None
        db.refresh(config)
        first = config.activated_first_at

        await api.post(f"{AGENT}/deactivate", headers=headers)
        await api.post(f"{AGENT}/activate", headers=headers)
        db.refresh(config)
        assert config.activated_first_at == first

    async def test_activating_twice_is_harmless(
        self, api: AsyncClient, mail: Any, db: Session
    ) -> None:
        headers, tenant, user = await _owner(api, mail, db)
        _complete_checklist(db, tenant, user)

        assert (await api.post(f"{AGENT}/activate", headers=headers)).status_code == 204
        assert (await api.post(f"{AGENT}/activate", headers=headers)).status_code == 204


class TestDeactivation:
    async def test_it_stops_the_agent(self, api: AsyncClient, mail: Any, db: Session) -> None:
        headers, tenant, user = await _owner(api, mail, db)
        _complete_checklist(db, tenant, user)
        await api.post(f"{AGENT}/activate", headers=headers)

        assert (await api.post(f"{AGENT}/deactivate", headers=headers)).status_code == 204
        config = db.get(AgentConfig, tenant.id)
        assert config is not None
        db.refresh(config)
        assert config.is_active is False

    async def test_it_works_on_an_account_that_never_activated(
        self, api: AsyncClient, mail: Any, db: Session
    ) -> None:
        """The stop button must not ask questions. Somebody pressing it in a
        panic should not get a 409 explaining that it was already off."""
        headers, _, _ = await _owner(api, mail, db)
        assert (await api.post(f"{AGENT}/deactivate", headers=headers)).status_code == 204

    async def test_a_paused_tenant_can_still_deactivate(
        self, api: AsyncClient, mail: Any, db: Session
    ) -> None:
        """The guard is on activation only. Being unable to turn something off
        is never the safe direction."""
        headers, tenant, user = await _owner(api, mail, db)
        _complete_checklist(db, tenant, user)
        await api.post(f"{AGENT}/activate", headers=headers)
        tenant.status = "paused"
        db.commit()

        assert (await api.post(f"{AGENT}/deactivate", headers=headers)).status_code == 204
