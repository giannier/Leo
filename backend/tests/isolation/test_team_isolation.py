"""One business never reaches another's team (TASK-009).

Driven over HTTP with two real signed-in owners, because that is the shape the
leak would take: not a repository called wrongly in a unit test, but a request
carrying a valid token and somebody else's id.
"""

from __future__ import annotations

import re
import uuid
from typing import Any
from urllib.parse import parse_qs, urlparse

from httpx import AsyncClient
from sqlalchemy.orm import Session

from app.models.tenant import Invitation, TenantUser

AUTH = "/api/v1/auth"
TEAM = "/api/v1/tenant/team"
PASSWORD = "una-clave-suficientemente-larga"
_HREF = re.compile(r'href="([^"]+)"')


def _unique_email(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:12]}@example.com"


def _unique_phone() -> str:
    return f"+519{uuid.uuid4().int % 10**8:08d}"


def _link_token(message: Any) -> str:
    for href in _HREF.findall(message.html):
        query = parse_qs(urlparse(href).query)
        if "token" in query:
            return str(query["token"][0])
    raise AssertionError("no tokenised link in the email body")


async def _owner(api: AsyncClient, mail: Any, label: str) -> tuple[dict[str, Any], dict[str, str]]:
    body = {
        "full_name": f"Owner {label}",
        "email": _unique_email(label),
        "password": PASSWORD,
        "phone_e164": _unique_phone(),
        "business_name": f"Negocio {label}",
    }
    created = await api.post(f"{AUTH}/register", json=body)
    assert created.status_code == 201, created.text
    await api.post(f"{AUTH}/verify-email", json={"token": _link_token(mail.last)})
    session = await api.post(f"{AUTH}/login", json={"email": body["email"], "password": PASSWORD})
    assert session.status_code == 200
    return (
        {**body, **created.json()},
        {"Authorization": f"Bearer {session.json()['access_token']}"},
    )


class TestOneBusinessNeverReachesAnothersTeam:
    async def test_the_two_owners_are_distinct(self, api: AsyncClient, mail: Any) -> None:
        """Guards the fixture itself: identical tenants would pass everything."""
        a, _ = await _owner(api, mail, "a")
        b, _ = await _owner(api, mail, "b")
        assert a["tenant_id"] != b["tenant_id"]
        assert a["user_id"] != b["user_id"]

    async def test_the_member_list_is_scoped(self, api: AsyncClient, mail: Any) -> None:
        a, headers_a = await _owner(api, mail, "a")
        b, _ = await _owner(api, mail, "b")

        members = (await api.get(TEAM, headers=headers_a)).json()["members"]
        user_ids = {m["user_id"] for m in members}
        assert a["user_id"] in user_ids
        assert b["user_id"] not in user_ids

    async def test_a_foreign_invitation_cannot_be_revoked(
        self, api: AsyncClient, mail: Any, db: Session
    ) -> None:
        _, headers_a = await _owner(api, mail, "a")
        _, headers_b = await _owner(api, mail, "b")

        created = await api.post(
            f"{TEAM}/invitations",
            headers=headers_b,
            json={"email": _unique_email("guest"), "role": "operator"},
        )
        assert created.status_code == 201
        invitation_id = uuid.UUID(created.json()["id"])

        refused = await api.delete(f"{TEAM}/invitations/{invitation_id}", headers=headers_a)
        assert refused.status_code == 404

        survivor = db.get(Invitation, invitation_id)
        assert survivor is not None
        db.refresh(survivor)
        assert survivor.revoked_at is None

    async def test_a_foreign_invitation_cannot_be_resent(self, api: AsyncClient, mail: Any) -> None:
        """Resending would mail a fresh, working link for another business."""
        _, headers_a = await _owner(api, mail, "a")
        _, headers_b = await _owner(api, mail, "b")

        created = await api.post(
            f"{TEAM}/invitations",
            headers=headers_b,
            json={"email": _unique_email("guest"), "role": "operator"},
        )
        refused = await api.post(
            f"{TEAM}/invitations/{created.json()['id']}/resend", headers=headers_a
        )
        assert refused.status_code == 404

    async def test_a_foreign_member_cannot_be_modified(
        self, api: AsyncClient, mail: Any, db: Session
    ) -> None:
        _, headers_a = await _owner(api, mail, "a")
        _, headers_b = await _owner(api, mail, "b")

        team_b = (await api.get(TEAM, headers=headers_b)).json()
        membership_id = uuid.UUID(team_b["members"][0]["id"])

        refused = await api.put(
            f"{TEAM}/members/{membership_id}", headers=headers_a, json={"status": "disabled"}
        )
        assert refused.status_code == 404

        victim = db.get(TenantUser, membership_id)
        assert victim is not None
        db.refresh(victim)
        assert victim.status == "active"

    async def test_a_foreign_id_is_indistinguishable_from_a_missing_one(
        self, api: AsyncClient, mail: Any
    ) -> None:
        """A different answer would confirm the id exists somewhere."""
        _, headers_a = await _owner(api, mail, "a")
        _, headers_b = await _owner(api, mail, "b")

        created = await api.post(
            f"{TEAM}/invitations",
            headers=headers_b,
            json={"email": _unique_email("guest"), "role": "operator"},
        )
        foreign = await api.delete(f"{TEAM}/invitations/{created.json()['id']}", headers=headers_a)
        invented = await api.delete(f"{TEAM}/invitations/{uuid.uuid4()}", headers=headers_a)

        assert foreign.status_code == invented.status_code == 404
        assert foreign.json() == invented.json()

    async def test_seat_usage_counts_only_the_callers_tenant(
        self, api: AsyncClient, mail: Any
    ) -> None:
        _, headers_a = await _owner(api, mail, "a")
        _, headers_b = await _owner(api, mail, "b")

        await api.post(
            f"{TEAM}/invitations",
            headers=headers_b,
            json={"email": _unique_email("guest"), "role": "operator"},
        )
        seats_a = (await api.get(TEAM, headers=headers_a)).json()["seats"]
        assert seats_a["used"] == 1  # only its own owner
