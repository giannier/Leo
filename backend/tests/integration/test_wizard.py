"""Business profile and agent configuration (TASK-010).

The two cases worth having are the partial save - the wizard writes step by
step and must not erase earlier steps - and the last-write-wins warning, which
is the only visible trace that somebody else was editing at the same time.
"""

from __future__ import annotations

import re
import uuid
from typing import Any
from urllib.parse import parse_qs, urlparse

import pytest
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


class TestBusinessProfile:
    async def test_defaults_before_the_first_save(self, api: AsyncClient, mail: Any) -> None:
        """No 404: the defaults carry everything a 404 would, and leave one
        less state for every client to handle."""
        headers = await _owner(api, mail)
        body = (await api.get(f"{TENANT}/business-profile", headers=headers)).json()
        assert body["currency"] == "PEN"
        assert body["timezone"] == "America/Lima"
        assert body["updated_at"] is None

    async def test_a_save_round_trips(self, api: AsyncClient, mail: Any) -> None:
        headers = await _owner(api, mail)
        saved = await api.put(
            f"{TENANT}/business-profile",
            headers=headers,
            json={"business_name": "Panaderia Leo", "industry": "restaurante", "currency": "USD"},
        )
        assert saved.status_code == 200
        assert saved.json()["business_name"] == "Panaderia Leo"
        assert saved.json()["updated_at"] is not None

        again = (await api.get(f"{TENANT}/business-profile", headers=headers)).json()
        assert again["industry"] == "restaurante"

    async def test_a_partial_save_does_not_erase_earlier_steps(
        self, api: AsyncClient, mail: Any
    ) -> None:
        """The wizard saves step by step; step two must not null step one."""
        headers = await _owner(api, mail)
        await api.put(
            f"{TENANT}/business-profile",
            headers=headers,
            json={"business_name": "Panaderia Leo", "industry": "restaurante"},
        )
        await api.put(f"{TENANT}/business-profile", headers=headers, json={"currency": "USD"})

        body = (await api.get(f"{TENANT}/business-profile", headers=headers)).json()
        assert body["business_name"] == "Panaderia Leo"
        assert body["industry"] == "restaurante"
        assert body["currency"] == "USD"

    @pytest.mark.parametrize(
        "payload",
        [{"industry": "mineria"}, {"currency": "peso"}, {"business_name": ""}],
    )
    async def test_invalid_values_are_rejected(
        self, api: AsyncClient, mail: Any, payload: dict[str, str]
    ) -> None:
        headers = await _owner(api, mail)
        response = await api.put(f"{TENANT}/business-profile", headers=headers, json=payload)
        assert response.status_code == 400
        assert response.json()["error"] == "validation_error"

    async def test_anonymous_callers_are_refused(self, api: AsyncClient) -> None:
        assert (await api.get(f"{TENANT}/business-profile")).status_code == 401


class TestLastWriteWins:
    async def test_a_stale_save_still_wins_but_warns(self, api: AsyncClient, mail: Any) -> None:
        """Clarification 008: no pessimistic lock, but no silent loss either.

        Two people edit. The second saves against a version the first already
        replaced. The write lands - that is what last-write-wins means - and
        the response says so, which is the only chance the panel gets to warn
        that a colleague's change was overwritten.
        """
        headers = await _owner(api, mail)
        first = await api.put(
            f"{TENANT}/business-profile", headers=headers, json={"business_name": "Version 1"}
        )
        stale_timestamp = first.json()["updated_at"]

        await api.put(
            f"{TENANT}/business-profile", headers=headers, json={"business_name": "Version 2"}
        )

        late = await api.put(
            f"{TENANT}/business-profile",
            headers=headers,
            json={"business_name": "Version 3", "expected_updated_at": stale_timestamp},
        )
        assert late.status_code == 200
        assert late.json()["business_name"] == "Version 3"
        assert late.json()["overwrote_newer"] is True

    async def test_a_current_save_does_not_warn(self, api: AsyncClient, mail: Any) -> None:
        headers = await _owner(api, mail)
        first = await api.put(
            f"{TENANT}/business-profile", headers=headers, json={"business_name": "Version 1"}
        )
        second = await api.put(
            f"{TENANT}/business-profile",
            headers=headers,
            json={"business_name": "Version 2", "expected_updated_at": first.json()["updated_at"]},
        )
        assert second.json()["overwrote_newer"] is False

    async def test_omitting_the_expectation_never_warns(self, api: AsyncClient, mail: Any) -> None:
        """A client that does not participate in the check is not warned by it."""
        headers = await _owner(api, mail)
        await api.put(f"{TENANT}/business-profile", headers=headers, json={"business_name": "A"})
        second = await api.put(
            f"{TENANT}/business-profile", headers=headers, json={"business_name": "B"}
        )
        assert second.json()["overwrote_newer"] is False


class TestAgentConfig:
    async def test_defaults_before_the_first_save(self, api: AsyncClient, mail: Any) -> None:
        headers = await _owner(api, mail)
        body = (await api.get(f"{TENANT}/agent-config", headers=headers)).json()
        assert body["agent_name"] == "Leo"
        assert body["tone"] == "cercano"
        assert body["is_active"] is False

    async def test_a_save_round_trips(self, api: AsyncClient, mail: Any) -> None:
        headers = await _owner(api, mail)
        saved = await api.put(
            f"{TENANT}/agent-config",
            headers=headers,
            json={"agent_name": "Rita", "tone": "juvenil", "objective": "vender"},
        )
        assert saved.status_code == 200
        assert saved.json()["agent_name"] == "Rita"
        assert saved.json()["tone"] == "juvenil"

    async def test_is_active_cannot_be_set_here(self, api: AsyncClient, mail: Any) -> None:
        """Activation has preconditions - the checklist - that this form does
        not evaluate, so the contract routes it through /agent/activate."""
        headers = await _owner(api, mail)
        saved = await api.put(
            f"{TENANT}/agent-config",
            headers=headers,
            json={"agent_name": "Rita", "is_active": True},
        )
        assert saved.status_code == 200
        assert saved.json()["is_active"] is False

    @pytest.mark.parametrize("payload", [{"tone": "sarcastico"}, {"objective": "espiar"}])
    async def test_invalid_values_are_rejected(
        self, api: AsyncClient, mail: Any, payload: dict[str, str]
    ) -> None:
        headers = await _owner(api, mail)
        response = await api.put(f"{TENANT}/agent-config", headers=headers, json=payload)
        assert response.status_code == 400
