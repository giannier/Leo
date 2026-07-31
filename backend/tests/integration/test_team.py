"""Team membership: invitations, roles and deactivation (TASK-009).

The cases that matter here are the ones a happy path never reaches: the seat
limit counting a *pending* invitation, an old link dying when a new one is
sent, the last owner being undemotable, and a disabled member losing access on
the very next request rather than in fifteen minutes.
"""

from __future__ import annotations

import re
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import parse_qs, urlparse

import pytest
from httpx import AsyncClient
from sqlalchemy.orm import Session

from app.models.plan import Plan
from app.models.tenant import Invitation, Tenant

AUTH = "/api/v1/auth"
TEAM = "/api/v1/tenant/team"
PASSWORD = "una-clave-suficientemente-larga"
_HREF = re.compile(r'href="([^"]+)"')


def _unique_email(prefix: str = "ana") -> str:
    return f"{prefix}-{uuid.uuid4().hex[:12]}@example.com"


def _unique_phone() -> str:
    return f"+519{uuid.uuid4().int % 10**8:08d}"


def _link_token(message: Any) -> str:
    for href in _HREF.findall(message.html):
        query = parse_qs(urlparse(href).query)
        if "token" in query:
            return str(query["token"][0])
    raise AssertionError("no tokenised link in the email body")


async def _owner(api: AsyncClient, mail: Any) -> tuple[dict[str, Any], dict[str, str]]:
    """A registered, verified, signed-in owner of a fresh tenant."""
    body = {
        "full_name": "Ana Quispe",
        "email": _unique_email(),
        "password": PASSWORD,
        "phone_e164": _unique_phone(),
        "business_name": "Panaderia Quispe",
    }
    created = await api.post(f"{AUTH}/register", json=body)
    assert created.status_code == 201, created.text
    await api.post(f"{AUTH}/verify-email", json={"token": _link_token(mail.last)})

    session = await api.post(f"{AUTH}/login", json={"email": body["email"], "password": PASSWORD})
    assert session.status_code == 200, session.text
    return (
        {**body, **created.json()},
        {"Authorization": f"Bearer {session.json()['access_token']}"},
    )


def _set_seats(db: Session, tenant_id: str, seats: int) -> None:
    """Give this tenant its own plan row, so limits can be varied per test.

    Editing the seeded `trial` row would change the ceiling for every other
    test sharing the database.
    """
    tenant = db.get(Tenant, uuid.UUID(tenant_id))
    assert tenant is not None
    plan_id = f"test-{uuid.uuid4().hex[:8]}"
    db.add(
        Plan(
            id=plan_id,
            name="Test",
            max_seats=seats,
            max_ai_messages=100,
            max_rag_chars=1000,
            max_knowledge_files=1,
        )
    )
    db.flush()
    tenant.plan_id = plan_id
    db.flush()
    db.commit()


class TestListing:
    async def test_a_new_tenant_has_one_owner_and_no_invitations(
        self, api: AsyncClient, mail: Any
    ) -> None:
        _, headers = await _owner(api, mail)
        body = (await api.get(TEAM, headers=headers)).json()
        assert len(body["members"]) == 1
        assert body["members"][0]["role"] == "owner"
        assert body["invitations"] == []

    async def test_anonymous_callers_are_refused(self, api: AsyncClient) -> None:
        assert (await api.get(TEAM)).status_code == 401


class TestInviting:
    async def test_an_invitation_is_created_and_emailed(self, api: AsyncClient, mail: Any) -> None:
        _, headers = await _owner(api, mail)
        response = await api.post(
            f"{TEAM}/invitations",
            headers=headers,
            json={"email": _unique_email("bruno"), "role": "operator"},
        )
        assert response.status_code == 201
        assert _link_token(mail.last)

    async def test_the_token_is_never_stored_in_clear(
        self, api: AsyncClient, mail: Any, db: Session
    ) -> None:
        _, headers = await _owner(api, mail)
        await api.post(
            f"{TEAM}/invitations",
            headers=headers,
            json={"email": _unique_email("bruno"), "role": "operator"},
        )
        token = _link_token(mail.last)
        stored = db.query(Invitation.token_hash).all()
        assert all(token != row[0] for row in stored)

    async def test_a_pending_invitation_occupies_a_seat(
        self, api: AsyncClient, mail: Any, db: Session
    ) -> None:
        """Two seats, one owner: exactly one invitation fits."""
        account, headers = await _owner(api, mail)
        _set_seats(db, account["tenant_id"], 2)

        first = await api.post(
            f"{TEAM}/invitations",
            headers=headers,
            json={"email": _unique_email("bruno"), "role": "operator"},
        )
        assert first.status_code == 201

        second = await api.post(
            f"{TEAM}/invitations",
            headers=headers,
            json={"email": _unique_email("carla"), "role": "operator"},
        )
        assert second.status_code == 409
        assert second.json()["error"] == "seat_limit_reached"

    async def test_inviting_an_existing_member_is_refused(
        self, api: AsyncClient, mail: Any
    ) -> None:
        account, headers = await _owner(api, mail)
        response = await api.post(
            f"{TEAM}/invitations",
            headers=headers,
            json={"email": account["email"], "role": "operator"},
        )
        assert response.status_code == 409
        assert response.json()["error"] == "already_member"

    async def test_inviting_the_same_address_twice_is_refused(
        self, api: AsyncClient, mail: Any, db: Session
    ) -> None:
        account, headers = await _owner(api, mail)
        _set_seats(db, account["tenant_id"], 10)
        email = _unique_email("bruno")

        assert (
            await api.post(
                f"{TEAM}/invitations",
                headers=headers,
                json={"email": email, "role": "operator"},
            )
        ).status_code == 201
        again = await api.post(
            f"{TEAM}/invitations", headers=headers, json={"email": email, "role": "operator"}
        )
        assert again.status_code == 409

    async def test_owner_cannot_be_invited_as_a_role(self, api: AsyncClient, mail: Any) -> None:
        """Ownership transfer is a role change, never an invitation."""
        _, headers = await _owner(api, mail)
        response = await api.post(
            f"{TEAM}/invitations", headers=headers, json={"email": _unique_email(), "role": "owner"}
        )
        assert response.status_code == 400


class TestResendAndRevoke:
    async def test_resending_kills_the_previous_link(
        self, api: AsyncClient, mail: Any, db: Session
    ) -> None:
        """A resend usually means the first message went astray."""
        account, headers = await _owner(api, mail)
        _set_seats(db, account["tenant_id"], 10)
        created = await api.post(
            f"{TEAM}/invitations",
            headers=headers,
            json={"email": _unique_email("bruno"), "role": "operator"},
        )
        first_token = _link_token(mail.last)

        resent = await api.post(
            f"{TEAM}/invitations/{created.json()['id']}/resend", headers=headers
        )
        assert resent.status_code == 204
        assert _link_token(mail.last) != first_token

        stale = await api.post(
            f"{AUTH}/invitations/accept",
            json={"token": first_token, "full_name": "Bruno", "password": PASSWORD},
        )
        assert stale.status_code == 410

    async def test_revoking_kills_the_link(self, api: AsyncClient, mail: Any, db: Session) -> None:
        account, headers = await _owner(api, mail)
        _set_seats(db, account["tenant_id"], 10)
        created = await api.post(
            f"{TEAM}/invitations",
            headers=headers,
            json={"email": _unique_email("bruno"), "role": "operator"},
        )
        token = _link_token(mail.last)

        assert (
            await api.delete(f"{TEAM}/invitations/{created.json()['id']}", headers=headers)
        ).status_code == 204

        used = await api.post(
            f"{AUTH}/invitations/accept",
            json={"token": token, "full_name": "Bruno", "password": PASSWORD},
        )
        assert used.status_code == 410

    async def test_an_unknown_invitation_is_a_404(self, api: AsyncClient, mail: Any) -> None:
        _, headers = await _owner(api, mail)
        response = await api.delete(f"{TEAM}/invitations/{uuid.uuid4()}", headers=headers)
        assert response.status_code == 404


class TestAccepting:
    async def test_a_new_user_joins_and_is_signed_in(
        self, api: AsyncClient, mail: Any, db: Session
    ) -> None:
        account, headers = await _owner(api, mail)
        _set_seats(db, account["tenant_id"], 10)
        await api.post(
            f"{TEAM}/invitations",
            headers=headers,
            json={"email": _unique_email("bruno"), "role": "operator"},
        )

        accepted = await api.post(
            f"{AUTH}/invitations/accept",
            json={"token": _link_token(mail.last), "full_name": "Bruno", "password": PASSWORD},
        )
        assert accepted.status_code == 200
        body = accepted.json()
        assert body["access_token"]
        assert body["active_tenant_id"] == account["tenant_id"]

    async def test_a_new_user_must_choose_a_password(
        self, api: AsyncClient, mail: Any, db: Session
    ) -> None:
        account, headers = await _owner(api, mail)
        _set_seats(db, account["tenant_id"], 10)
        await api.post(
            f"{TEAM}/invitations",
            headers=headers,
            json={"email": _unique_email("bruno"), "role": "operator"},
        )
        response = await api.post(
            f"{AUTH}/invitations/accept", json={"token": _link_token(mail.last)}
        )
        assert response.status_code == 400
        assert response.json()["error"] == "password_required"

    async def test_an_existing_user_joins_without_a_password(
        self, api: AsyncClient, mail: Any, db: Session
    ) -> None:
        """The token in their inbox is the credential; asking again adds nothing."""
        other, _ = await _owner(api, mail)
        account, headers = await _owner(api, mail)
        _set_seats(db, account["tenant_id"], 10)

        await api.post(
            f"{TEAM}/invitations",
            headers=headers,
            json={"email": other["email"], "role": "tenant_admin"},
        )
        accepted = await api.post(
            f"{AUTH}/invitations/accept", json={"token": _link_token(mail.last)}
        )
        assert accepted.status_code == 200
        assert len(accepted.json()["memberships"]) == 2

    async def test_an_expired_invitation_is_gone(
        self, api: AsyncClient, mail: Any, db: Session
    ) -> None:
        account, headers = await _owner(api, mail)
        _set_seats(db, account["tenant_id"], 10)
        created = await api.post(
            f"{TEAM}/invitations",
            headers=headers,
            json={"email": _unique_email("bruno"), "role": "operator"},
        )
        token = _link_token(mail.last)

        invitation = db.get(Invitation, uuid.UUID(created.json()["id"]))
        assert invitation is not None
        invitation.expires_at = datetime.now(UTC) - timedelta(seconds=1)
        db.commit()

        response = await api.post(
            f"{AUTH}/invitations/accept",
            json={"token": token, "full_name": "Bruno", "password": PASSWORD},
        )
        assert response.status_code == 410
        assert response.json()["error"] == "invitation_expired"

    async def test_a_forged_token_is_gone_not_found(self, api: AsyncClient) -> None:
        """410, not 404: a different answer would confirm which tokens exist."""
        response = await api.post(
            f"{AUTH}/invitations/accept",
            json={"token": "not-a-real-token", "full_name": "X", "password": PASSWORD},
        )
        assert response.status_code == 410


class TestMemberChanges:
    async def _invited_member(
        self, api: AsyncClient, mail: Any, db: Session, role: str = "operator"
    ) -> tuple[dict[str, Any], dict[str, str], str, dict[str, str]]:
        account, headers = await _owner(api, mail)
        _set_seats(db, account["tenant_id"], 10)
        await api.post(
            f"{TEAM}/invitations",
            headers=headers,
            json={"email": _unique_email("bruno"), "role": role},
        )
        joined = await api.post(
            f"{AUTH}/invitations/accept",
            json={"token": _link_token(mail.last), "full_name": "Bruno", "password": PASSWORD},
        )
        assert joined.status_code == 200
        member_headers = {"Authorization": f"Bearer {joined.json()['access_token']}"}

        team = (await api.get(TEAM, headers=headers)).json()
        membership_id = next(m["id"] for m in team["members"] if m["role"] == role)
        return account, headers, membership_id, member_headers

    async def test_role_can_be_changed(self, api: AsyncClient, mail: Any, db: Session) -> None:
        _, headers, membership_id, _ = await self._invited_member(api, mail, db)
        response = await api.put(
            f"{TEAM}/members/{membership_id}", headers=headers, json={"role": "tenant_admin"}
        )
        assert response.status_code == 200
        assert response.json()["role"] == "tenant_admin"

    async def test_disabling_ends_access_on_the_next_request(
        self, api: AsyncClient, mail: Any, db: Session
    ) -> None:
        """Not in fifteen minutes - now.

        This is what TASK-007 bought by keeping the role out of the token and
        re-reading membership on every request.
        """
        _, headers, membership_id, member_headers = await self._invited_member(api, mail, db)
        assert (await api.get(TEAM, headers=member_headers)).status_code == 200

        disabled = await api.put(
            f"{TEAM}/members/{membership_id}", headers=headers, json={"status": "disabled"}
        )
        assert disabled.status_code == 200

        # Same still-unexpired token, now refused.
        assert (await api.get(TEAM, headers=member_headers)).status_code == 403

    async def test_the_last_owner_cannot_be_demoted(self, api: AsyncClient, mail: Any) -> None:
        """Otherwise the tenant loses billing and deletion with no way back."""
        _, headers = await _owner(api, mail)
        team = (await api.get(TEAM, headers=headers)).json()
        owner_membership = team["members"][0]["id"]

        response = await api.put(
            f"{TEAM}/members/{owner_membership}", headers=headers, json={"role": "operator"}
        )
        assert response.status_code == 409
        assert response.json()["error"] == "last_owner"

    async def test_the_last_owner_cannot_be_disabled(self, api: AsyncClient, mail: Any) -> None:
        _, headers = await _owner(api, mail)
        team = (await api.get(TEAM, headers=headers)).json()
        response = await api.put(
            f"{TEAM}/members/{team['members'][0]['id']}",
            headers=headers,
            json={"status": "disabled"},
        )
        assert response.status_code == 409

    async def test_an_empty_body_is_rejected(self, api: AsyncClient, mail: Any) -> None:
        _, headers = await _owner(api, mail)
        team = (await api.get(TEAM, headers=headers)).json()
        response = await api.put(
            f"{TEAM}/members/{team['members'][0]['id']}", headers=headers, json={}
        )
        assert response.status_code == 400


class TestRolesFollowClarification008:
    @pytest.mark.parametrize("role", ["operator", "tenant_admin"])
    async def test_only_owner_may_change_members(
        self, api: AsyncClient, mail: Any, db: Session, role: str
    ) -> None:
        """An admin who could disable the owner could take the account."""
        helper = TestMemberChanges()
        account, owner_headers, membership_id, member_headers = await helper._invited_member(
            api, mail, db, role=role
        )
        response = await api.put(
            f"{TEAM}/members/{membership_id}", headers=member_headers, json={"role": "owner"}
        )
        assert response.status_code == 403

    async def test_an_operator_cannot_invite(
        self, api: AsyncClient, mail: Any, db: Session
    ) -> None:
        helper = TestMemberChanges()
        _, _, _, operator_headers = await helper._invited_member(api, mail, db, role="operator")
        response = await api.post(
            f"{TEAM}/invitations",
            headers=operator_headers,
            json={"email": _unique_email(), "role": "operator"},
        )
        assert response.status_code == 403

    async def test_an_operator_may_still_read_the_team(
        self, api: AsyncClient, mail: Any, db: Session
    ) -> None:
        helper = TestMemberChanges()
        _, _, _, operator_headers = await helper._invited_member(api, mail, db, role="operator")
        assert (await api.get(TEAM, headers=operator_headers)).status_code == 200
