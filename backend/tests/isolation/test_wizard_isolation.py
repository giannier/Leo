"""One business never reads or writes another's configuration (TASK-010).

Business profile and agent config are keyed by `tenant_id` rather than by a
row id, so there is no foreign id for a caller to guess. The leak these cases
guard against is subtler: a repository built for one tenant returning, or
overwriting, the singleton row of another.
"""

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


async def _owner(api: AsyncClient, mail: Any, label: str) -> dict[str, str]:
    body = {
        "full_name": f"Owner {label}",
        "email": f"{label}-{uuid.uuid4().hex[:12]}@example.com",
        "password": PASSWORD,
        "business_name": f"Negocio {label}",
    }
    assert (await api.post(f"{AUTH}/register", json=body)).status_code == 201
    await api.post(f"{AUTH}/verify-email", json={"token": _link_token(mail.last)})
    session = await api.post(f"{AUTH}/login", json={"email": body["email"], "password": PASSWORD})
    return {"Authorization": f"Bearer {session.json()['access_token']}"}


class TestConfigurationIsPerTenant:
    async def test_a_profile_saved_by_one_is_invisible_to_the_other(
        self, api: AsyncClient, mail: Any
    ) -> None:
        headers_a = await _owner(api, mail, "a")
        headers_b = await _owner(api, mail, "b")

        await api.put(
            f"{TENANT}/business-profile",
            headers=headers_a,
            json={"business_name": "Secreto de A", "industry": "restaurante"},
        )

        seen_by_b = (await api.get(f"{TENANT}/business-profile", headers=headers_b)).json()
        assert seen_by_b["business_name"] is None
        assert seen_by_b["industry"] is None

    async def test_writing_does_not_touch_the_other_tenants_row(
        self, api: AsyncClient, mail: Any
    ) -> None:
        """The upsert path is the dangerous one: an unscoped `current()` would
        find the wrong singleton and quietly edit somebody else's business."""
        headers_a = await _owner(api, mail, "a")
        headers_b = await _owner(api, mail, "b")

        await api.put(
            f"{TENANT}/business-profile", headers=headers_a, json={"business_name": "Nombre de A"}
        )
        await api.put(
            f"{TENANT}/business-profile", headers=headers_b, json={"business_name": "Nombre de B"}
        )

        assert (await api.get(f"{TENANT}/business-profile", headers=headers_a)).json()[
            "business_name"
        ] == "Nombre de A"
        assert (await api.get(f"{TENANT}/business-profile", headers=headers_b)).json()[
            "business_name"
        ] == "Nombre de B"

    async def test_agent_config_is_scoped_too(self, api: AsyncClient, mail: Any) -> None:
        headers_a = await _owner(api, mail, "a")
        headers_b = await _owner(api, mail, "b")

        await api.put(
            f"{TENANT}/agent-config",
            headers=headers_a,
            json={"agent_name": "Rita", "tone": "juvenil"},
        )

        seen_by_b = (await api.get(f"{TENANT}/agent-config", headers=headers_b)).json()
        assert seen_by_b["agent_name"] == "Leo"
        assert seen_by_b["tone"] == "cercano"

    async def test_the_concurrency_check_does_not_cross_tenants(
        self, api: AsyncClient, mail: Any
    ) -> None:
        """B saving after A must not be told it overwrote A's newer version.

        The staleness comparison reads "the current row"; if that read were
        unscoped it would compare against another business's timestamp and
        warn about a collision that never happened.
        """
        headers_a = await _owner(api, mail, "a")
        headers_b = await _owner(api, mail, "b")

        first_b = await api.put(
            f"{TENANT}/business-profile", headers=headers_b, json={"business_name": "B1"}
        )
        await api.put(f"{TENANT}/business-profile", headers=headers_a, json={"business_name": "A1"})

        second_b = await api.put(
            f"{TENANT}/business-profile",
            headers=headers_b,
            json={
                "business_name": "B2",
                "expected_updated_at": first_b.json()["updated_at"],
            },
        )
        assert second_b.json()["overwrote_newer"] is False
