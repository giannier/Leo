"""Cross-tenant isolation for what TASK-007 added.

Sessions move the tenant boundary out of the repositories and into the
request path: an access token *names* a tenant, and the authentication
dependencies decide whether that name is honoured. A hole there leaks a whole
business account no matter how well `BaseRepository` is written, so this
module attacks it from both ends:

* the repository level - `UserMembershipRepository`, the one query in the
  system scoped by `user_id` instead of `tenant_id`, and
  `TenantUserRepository.active_membership_for`, which is what every
  authenticated request runs;
* the HTTP level - a real token naming somebody else's tenant, presented to
  a real guarded endpoint.

The HTTP cases are the ones with teeth against a broken filter. Resolving the
active tenant goes through `BaseRepository._scoped()`; remove that predicate
and a token naming any tenant at all starts being honoured, which these tests
then fail on.
"""

from __future__ import annotations

import re
import uuid
from datetime import UTC, datetime
from typing import Any
from urllib.parse import parse_qs, urlparse

import pytest
from fastapi import APIRouter, FastAPI
from httpx import AsyncClient
from sqlalchemy.orm import Session

from app.api.deps import CurrentPrincipalDep
from app.core.config import get_settings
from app.core.tokens import issue_access_token
from app.models import TenantUser, User
from app.providers.email.mock import MockEmailProvider
from app.repositories import (
    TenantUserRepository,
    UserMembershipRepository,
)
from tests.isolation.conftest import TenantFixture

SETTINGS = get_settings()
AUTH = f"{SETTINGS.api_v1_prefix}/auth"
PROBE = "/probe"
PASSWORD = "una-clave-suficientemente-larga"

_HREF = re.compile(r'href="([^"]+)"')

probe_router = APIRouter(prefix=PROBE)


@probe_router.get("/tenant")
async def probe_tenant(principal: CurrentPrincipalDep) -> dict[str, str]:
    """Stands in for every `/tenant/*` endpoint TASK-009 onward will add.

    They all resolve the tenant the same way, through the same dependency, so
    guarding this one proves the property for all of them.
    """
    return {"tenant_id": str(principal.tenant_id), "role": principal.role}


@pytest.fixture
def probe(app: FastAPI) -> FastAPI:
    app.include_router(probe_router)
    return app


def _user(db: Session, label: str) -> User:
    user = User(
        email=f"{label}-{uuid.uuid4().hex[:8]}@example.com",
        password_hash="not-a-real-hash",
        full_name=f"Owner {label}",
        email_verified_at=datetime.now(UTC),
    )
    db.add(user)
    db.flush()
    return user


def _token_from(message: Any) -> str:
    for href in _HREF.findall(message.html):
        query = parse_qs(urlparse(href).query)
        if "token" in query:
            return str(query["token"][0])
    raise AssertionError("no verification link in the email body")


async def _register_verified(
    api: AsyncClient, mail: MockEmailProvider, name: str
) -> dict[str, Any]:
    """A real account with its own trial tenant, through the real endpoints."""
    body = {
        "full_name": f"Owner of {name}",
        "email": f"owner-{uuid.uuid4().hex[:12]}@example.com",
        "password": PASSWORD,
        "business_name": name,
    }
    created = await api.post(f"{AUTH}/register", json=body)
    assert created.status_code == 201, created.text
    await api.post(f"{AUTH}/verify-email", json={"token": _token_from(mail.last)})
    return {**body, **created.json()}


def _bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


class TestMembershipListingIsUserScoped:
    """`UserMembershipRepository` filters on `user_id` rather than
    `tenant_id`. That is the only such query in the system, so it carries its
    own proof that the substitute predicate really is enforced."""

    def test_a_user_sees_only_their_own_memberships(
        self, db: Session, tenant_a: TenantFixture, tenant_b: TenantFixture
    ) -> None:
        alice, bob = _user(db, "alice"), _user(db, "bob")
        TenantUserRepository(db, tenant_a.tenant_id).add_member(user_id=alice.id, role="owner")
        TenantUserRepository(db, tenant_b.tenant_id).add_member(user_id=bob.id, role="owner")

        found = UserMembershipRepository(db, alice.id).active_memberships()

        assert [membership.tenant_id for membership in found] == [tenant_a.tenant_id]

    def test_another_users_tenant_name_is_never_disclosed(
        self, db: Session, tenant_a: TenantFixture, tenant_b: TenantFixture
    ) -> None:
        """The projection carries the business name, which is the customer's
        own data and not something a stranger may enumerate."""
        alice, bob = _user(db, "alice"), _user(db, "bob")
        TenantUserRepository(db, tenant_a.tenant_id).add_member(user_id=alice.id, role="owner")
        TenantUserRepository(db, tenant_b.tenant_id).add_member(user_id=bob.id, role="owner")

        names = {m.tenant_name for m in UserMembershipRepository(db, alice.id).active_memberships()}

        assert names == {"Tenant A"}

    def test_a_user_with_no_memberships_sees_nothing(
        self, db: Session, tenant_a: TenantFixture, tenant_b: TenantFixture
    ) -> None:
        """An empty answer, not everyone's tenants."""
        outsider = _user(db, "outsider")
        TenantUserRepository(db, tenant_a.tenant_id).add_member(
            user_id=_user(db, "alice").id, role="owner"
        )

        assert UserMembershipRepository(db, outsider.id).active_memberships() == []

    def test_the_repository_is_bound_to_a_user_at_construction(self, db: Session) -> None:
        """No query method is reachable without having supplied one - the
        same structural guarantee `BaseRepository` gives for the tenant."""
        with pytest.raises(TypeError):
            UserMembershipRepository(db)  # type: ignore[call-arg]

    def test_it_reports_the_user_it_is_bound_to(self, db: Session) -> None:
        alice = _user(db, "alice")
        assert UserMembershipRepository(db, alice.id).user_id == alice.id

    def test_a_disabled_membership_grants_nothing(
        self, db: Session, tenant_a: TenantFixture
    ) -> None:
        alice = _user(db, "alice")
        membership = TenantUserRepository(db, tenant_a.tenant_id).add_member(
            user_id=alice.id, role="operator"
        )
        membership.status = "disabled"
        db.flush()

        assert UserMembershipRepository(db, alice.id).active_memberships() == []


class TestActiveMembershipResolutionIsTenantScoped:
    """The query behind every authenticated request. It goes through
    `BaseRepository._scoped()`, so removing that predicate breaks these."""

    def test_a_foreign_tenant_resolves_to_no_membership(
        self, db: Session, tenant_a: TenantFixture, tenant_b: TenantFixture
    ) -> None:
        bob = _user(db, "bob")
        TenantUserRepository(db, tenant_b.tenant_id).add_member(user_id=bob.id, role="owner")

        # Bob's own tenant grants him a role...
        assert TenantUserRepository(db, tenant_b.tenant_id).active_membership_for(bob.id)
        # ...and the neighbouring one grants him nothing.
        assert TenantUserRepository(db, tenant_a.tenant_id).active_membership_for(bob.id) is None

    def test_a_disabled_member_resolves_to_no_membership(
        self, db: Session, tenant_a: TenantFixture
    ) -> None:
        """TASK-009 revokes access by disabling the row; it has to bite here."""
        alice = _user(db, "alice")
        membership = TenantUserRepository(db, tenant_a.tenant_id).add_member(
            user_id=alice.id, role="owner"
        )
        membership.status = "disabled"
        db.flush()

        assert TenantUserRepository(db, tenant_a.tenant_id).active_membership_for(alice.id) is None

    def test_the_role_returned_is_the_one_in_this_tenant(
        self, db: Session, tenant_a: TenantFixture, tenant_b: TenantFixture
    ) -> None:
        """The same person can be an owner in one business and an operator in
        another; taking the wrong row would grant the wrong authority."""
        alice = _user(db, "alice")
        TenantUserRepository(db, tenant_a.tenant_id).add_member(user_id=alice.id, role="owner")
        TenantUserRepository(db, tenant_b.tenant_id).add_member(user_id=alice.id, role="operator")

        in_a = TenantUserRepository(db, tenant_a.tenant_id).active_membership_for(alice.id)
        in_b = TenantUserRepository(db, tenant_b.tenant_id).active_membership_for(alice.id)

        assert in_a is not None and in_a.role == "owner"
        assert in_b is not None and in_b.role == "operator"


class TestTokensCannotCrossTheTenantBoundary:
    """End to end: a real signed token naming a tenant the caller does not
    belong to, presented to a real guarded endpoint."""

    async def test_a_token_naming_a_foreign_tenant_is_refused(
        self, probe: FastAPI, api: AsyncClient, mail: MockEmailProvider
    ) -> None:
        victim = await _register_verified(api, mail, "Panaderia Quispe")
        attacker = await _register_verified(api, mail, "Bodega Rival")

        crafted, _ = issue_access_token(
            user_id=uuid.UUID(attacker["user_id"]),
            tenant_id=uuid.UUID(victim["tenant_id"]),
            now=datetime.now(UTC),
        )
        response = await api.get(f"{PROBE}/tenant", headers=_bearer(crafted))

        assert response.status_code == 403
        assert response.json()["error"] == "tenant_access_denied"

    async def test_the_caller_still_reaches_their_own_tenant(
        self, probe: FastAPI, api: AsyncClient, mail: MockEmailProvider
    ) -> None:
        """The other half of the assertion: the guard must not simply refuse
        everything, which would also pass the test above."""
        attacker = await _register_verified(api, mail, "Bodega Rival")

        own, _ = issue_access_token(
            user_id=uuid.UUID(attacker["user_id"]),
            tenant_id=uuid.UUID(attacker["tenant_id"]),
            now=datetime.now(UTC),
        )
        response = await api.get(f"{PROBE}/tenant", headers=_bearer(own))

        assert response.status_code == 200
        assert response.json()["tenant_id"] == attacker["tenant_id"]

    async def test_login_never_lists_another_businesss_membership(
        self, probe: FastAPI, api: AsyncClient, mail: MockEmailProvider
    ) -> None:
        victim = await _register_verified(api, mail, "Panaderia Quispe")
        attacker = await _register_verified(api, mail, "Bodega Rival")

        body = (
            await api.post(f"{AUTH}/login", json={"email": attacker["email"], "password": PASSWORD})
        ).json()

        listed = {m["tenant_id"] for m in body["memberships"]}
        assert listed == {attacker["tenant_id"]}
        assert victim["tenant_id"] not in listed
        assert "Panaderia Quispe" not in str(body)

    async def test_login_cannot_activate_a_foreign_tenant(
        self, probe: FastAPI, api: AsyncClient, mail: MockEmailProvider
    ) -> None:
        """Asking for a tenant by id is a request, never a grant."""
        victim = await _register_verified(api, mail, "Panaderia Quispe")
        attacker = await _register_verified(api, mail, "Bodega Rival")

        response = await api.post(
            f"{AUTH}/login",
            json={
                "email": attacker["email"],
                "password": PASSWORD,
                "tenant_id": victim["tenant_id"],
            },
        )

        assert response.status_code == 403
        assert response.json()["error"] == "tenant_access_denied"

    async def test_refresh_cannot_switch_into_a_foreign_tenant(
        self, probe: FastAPI, api: AsyncClient, mail: MockEmailProvider
    ) -> None:
        """The rotation path re-resolves the tenant, so it needs the same
        guard as login - and gets it from the same code."""
        victim = await _register_verified(api, mail, "Panaderia Quispe")
        attacker = await _register_verified(api, mail, "Bodega Rival")
        await api.post(f"{AUTH}/login", json={"email": attacker["email"], "password": PASSWORD})

        response = await api.post(f"{AUTH}/refresh", json={"tenant_id": victim["tenant_id"]})

        assert response.status_code == 403
        assert response.json()["error"] == "tenant_access_denied"

    async def test_a_foreign_refresh_token_does_not_transfer_a_session(
        self, probe: FastAPI, api: AsyncClient, mail: MockEmailProvider
    ) -> None:
        """Stealing the victim's cookie yields the victim's session, not the
        attacker's own account with the victim's tenant attached."""
        victim = await _register_verified(api, mail, "Panaderia Quispe")
        await api.post(f"{AUTH}/login", json={"email": victim["email"], "password": PASSWORD})
        victim_cookie = api.cookies.get("refresh_token")
        assert victim_cookie

        attacker = await _register_verified(api, mail, "Bodega Rival")
        api.cookies.set("refresh_token", victim_cookie, path=f"{SETTINGS.api_v1_prefix}/auth")
        body = (await api.post(f"{AUTH}/refresh")).json()

        assert body["user"]["id"] == victim["user_id"]
        assert body["user"]["id"] != attacker["user_id"]
        assert body["active_tenant_id"] == victim["tenant_id"]


class TestTheFixtureItselfIsSound:
    async def test_the_two_accounts_are_actually_distinct(
        self, probe: FastAPI, api: AsyncClient, mail: MockEmailProvider
    ) -> None:
        """Guards against these tests passing because both helpers returned
        the same account, which would make every assertion vacuous."""
        first = await _register_verified(api, mail, "Panaderia Quispe")
        second = await _register_verified(api, mail, "Bodega Rival")

        assert first["user_id"] != second["user_id"]
        assert first["tenant_id"] != second["tenant_id"]

    def test_memberships_are_really_written(self, db: Session, tenant_a: TenantFixture) -> None:
        """An empty table would satisfy every scoping assertion above."""
        alice = _user(db, "alice")
        TenantUserRepository(db, tenant_a.tenant_id).add_member(user_id=alice.id, role="owner")

        found = UserMembershipRepository(db, alice.id).active_memberships()

        assert len(found) == 1
        assert found[0].role == "owner"

    def test_the_disabled_status_used_above_is_a_real_one(self) -> None:
        """A typo would make the "disabled member" tests pass for the wrong
        reason - the check constraint would have rejected the value."""
        from app.models.tenant import MEMBERSHIP_STATUSES

        assert "disabled" in MEMBERSHIP_STATUSES
        assert TenantUser.__table__.name == "tenant_users"
