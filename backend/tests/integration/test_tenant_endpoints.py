"""Tenant state, payment methods and the agent switch (TASK-013)."""

from __future__ import annotations

import re
import uuid
from typing import Any
from urllib.parse import parse_qs, urlparse

from httpx import AsyncClient

AUTH = "/api/v1/auth"
TENANT = "/api/v1/tenant"
PASSWORD = "una-clave-suficientemente-larga"
_HREF = re.compile(r'href="([^"]+)"')


def _link_token(message: Any) -> str:
    for href in _HREF.findall(message.html):
        query = parse_qs(urlparse(href).query)
        if "token" in query:
            return str(query["token"][0])
    raise AssertionError("no tokenised link in the email body")


async def _owner(api: AsyncClient, mail: Any) -> dict[str, str]:
    body = {
        "full_name": "Ana Quispe",
        "email": f"ana-{uuid.uuid4().hex[:12]}@example.com",
        "password": PASSWORD,
        "business_name": "Panaderia Quispe",
    }
    assert (await api.post(f"{AUTH}/register", json=body)).status_code == 201
    await api.post(f"{AUTH}/verify-email", json={"token": _link_token(mail.last)})
    session = await api.post(f"{AUTH}/login", json={"email": body["email"], "password": PASSWORD})
    return {"Authorization": f"Bearer {session.json()['access_token']}"}


class TestTenantState:
    async def test_a_new_tenant_reports_its_trial(self, api: AsyncClient, mail: Any) -> None:
        headers = await _owner(api, mail)
        body = (await api.get(TENANT, headers=headers)).json()
        assert body["status"] == "trial"
        assert body["plan"] == "trial"
        assert body["trial"]["ai_messages_limit"] == 250
        assert body["trial"]["ai_messages_used"] == 0
        assert body["trial"]["days_left"] is not None

    async def test_anonymous_callers_are_refused(self, api: AsyncClient) -> None:
        assert (await api.get(TENANT)).status_code == 401


class TestChecklistEndpoint:
    async def test_a_fresh_tenant_is_ready_for_nothing(self, api: AsyncClient, mail: Any) -> None:
        headers = await _owner(api, mail)
        body = (await api.get(f"{TENANT}/activation-checklist", headers=headers)).json()
        assert body["sandbox_ready"] is False
        assert body["activation_ready"] is False
        assert len(body["items"]) == 5
        # Email is verified during registration in this fixture.
        assert next(i for i in body["items"] if i["key"] == "email_verified")["done"] is True

    async def test_completing_the_wizard_opens_the_sandbox(
        self, api: AsyncClient, mail: Any
    ) -> None:
        """The whole path a real customer walks, end to end."""
        headers = await _owner(api, mail)

        await api.put(
            f"{TENANT}/business-profile",
            headers=headers,
            json={"business_name": "Panaderia Leo", "industry": "restaurante"},
        )
        await api.put(
            f"{TENANT}/agent-config",
            headers=headers,
            json={"agent_name": "Leo", "tone": "cercano", "objective": "vender"},
        )
        await api.post(f"{TENANT}/products", headers=headers, json={"name": "Pan", "price": "1.50"})
        await api.post(
            f"{TENANT}/payment-methods",
            headers=headers,
            json={"method": "yape", "instructions": "Yape al 999888777"},
        )

        body = (await api.get(f"{TENANT}/activation-checklist", headers=headers)).json()
        assert body["sandbox_ready"] is True
        # Still not live: no verified phone and no connected number.
        assert body["activation_ready"] is False


class TestPaymentMethods:
    async def test_create_and_list(self, api: AsyncClient, mail: Any) -> None:
        headers = await _owner(api, mail)
        created = await api.post(
            f"{TENANT}/payment-methods",
            headers=headers,
            json={"method": "yape", "instructions": "Yape al 999888777"},
        )
        assert created.status_code == 201

        listing = (await api.get(f"{TENANT}/payment-methods", headers=headers)).json()
        assert [m["method"] for m in listing] == ["yape"]

    async def test_the_same_method_twice_is_a_conflict(self, api: AsyncClient, mail: Any) -> None:
        """A business has one set of Yape instructions, not several."""
        headers = await _owner(api, mail)
        payload = {"method": "yape", "instructions": "Yape al 999"}
        assert (
            await api.post(f"{TENANT}/payment-methods", headers=headers, json=payload)
        ).status_code == 201

        again = await api.post(f"{TENANT}/payment-methods", headers=headers, json=payload)
        assert again.status_code == 409
        assert again.json()["error"] == "method_already_exists"

    async def test_a_different_method_is_fine(self, api: AsyncClient, mail: Any) -> None:
        headers = await _owner(api, mail)
        for method in ("yape", "plin"):
            response = await api.post(
                f"{TENANT}/payment-methods",
                headers=headers,
                json={"method": method, "instructions": f"{method} al 999"},
            )
            assert response.status_code == 201

    async def test_instructions_can_be_edited(self, api: AsyncClient, mail: Any) -> None:
        headers = await _owner(api, mail)
        created = await api.post(
            f"{TENANT}/payment-methods",
            headers=headers,
            json={"method": "yape", "instructions": "Yape al 999"},
        )
        updated = await api.put(
            f"{TENANT}/payment-methods/{created.json()['id']}",
            headers=headers,
            json={"instructions": "Yape al 111 a nombre de Ana"},
        )
        assert updated.status_code == 200
        assert updated.json()["instructions"] == "Yape al 111 a nombre de Ana"

    async def test_delete_removes_it(self, api: AsyncClient, mail: Any) -> None:
        headers = await _owner(api, mail)
        created = await api.post(
            f"{TENANT}/payment-methods",
            headers=headers,
            json={"method": "yape", "instructions": "Yape al 999"},
        )
        assert (
            await api.delete(f"{TENANT}/payment-methods/{created.json()['id']}", headers=headers)
        ).status_code == 204
        assert (await api.get(f"{TENANT}/payment-methods", headers=headers)).json() == []

    async def test_an_unknown_method_is_rejected(self, api: AsyncClient, mail: Any) -> None:
        headers = await _owner(api, mail)
        response = await api.post(
            f"{TENANT}/payment-methods",
            headers=headers,
            json={"method": "bitcoin", "instructions": "x"},
        )
        assert response.status_code == 400

    async def test_an_unknown_id_is_404(self, api: AsyncClient, mail: Any) -> None:
        headers = await _owner(api, mail)
        response = await api.delete(f"{TENANT}/payment-methods/{uuid.uuid4()}", headers=headers)
        assert response.status_code == 404


class TestAgentSwitch:
    async def test_activation_is_refused_while_the_checklist_is_incomplete(
        self, api: AsyncClient, mail: Any
    ) -> None:
        headers = await _owner(api, mail)
        response = await api.post(f"{TENANT}/agent/activate", headers=headers)
        assert response.status_code == 409
        assert response.json()["error"] == "checklist_incomplete"

    async def test_the_guard_and_the_checklist_agree(self, api: AsyncClient, mail: Any) -> None:
        """Both call the same function, so a green checklist next to a button
        that refuses is structurally impossible."""
        headers = await _owner(api, mail)
        checklist = (await api.get(f"{TENANT}/activation-checklist", headers=headers)).json()
        activation = await api.post(f"{TENANT}/agent/activate", headers=headers)

        assert checklist["activation_ready"] is False
        assert activation.status_code == 409

    async def test_deactivation_is_never_blocked(self, api: AsyncClient, mail: Any) -> None:
        """Turning the agent off is how a customer stops it saying something
        wrong. A checklist must never stand in the way of that."""
        headers = await _owner(api, mail)
        assert (await api.post(f"{TENANT}/agent/deactivate", headers=headers)).status_code == 204

        config = (await api.get(f"{TENANT}/agent-config", headers=headers)).json()
        assert config["is_active"] is False
