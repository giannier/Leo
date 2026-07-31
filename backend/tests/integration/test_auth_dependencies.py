"""Tests for the authentication dependencies in `app/api/deps.py`.

These are the reusable guards every protected endpoint from TASK-009 onward
is built on, so they are tested directly rather than through whichever
endpoint happens to use them first. TASK-007 ships no protected endpoint of
its own - the contract does not define one - so the routes here are declared
by the test and mounted on the app under test. Nothing in this module reaches
production; what it exercises is entirely `deps.py`.

The properties that matter are the ones a later task could break without
noticing: identity comes from the token, **authority does not**. A role is
read from the database on every request, so removing or demoting a member
takes effect on their next call rather than fifteen minutes later.
"""

from __future__ import annotations

import re
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import parse_qs, urlparse

import pytest
from fastapi import APIRouter, FastAPI
from httpx import AsyncClient
from sqlalchemy import update
from sqlalchemy.orm import Session

from app.api.deps import (
    ANY_MEMBER,
    MANAGES_CONFIGURATION,
    OWNS_THE_ACCOUNT,
    AccountOwnerDep,
    ConfigurationManagerDep,
    CurrentPrincipalDep,
    CurrentUserDep,
    MemberDep,
    require_roles,
)
from app.core.config import get_settings
from app.core.tokens import issue_access_token
from app.models import Tenant, TenantUser, User
from app.providers.email.mock import MockEmailProvider

SETTINGS = get_settings()
AUTH = f"{SETTINGS.api_v1_prefix}/auth"
PROBE = "/probe"
PASSWORD = "una-clave-suficientemente-larga"

_HREF = re.compile(r'href="([^"]+)"')

# Routes that exist only to have something guarded to call. One per rung of
# the dependency chain, so a failure names the rung that broke.
probe_router = APIRouter(prefix=PROBE)


@probe_router.get("/user")
async def probe_user(user: CurrentUserDep) -> dict[str, str]:
    return {"user_id": str(user.id)}


@probe_router.get("/tenant")
async def probe_tenant(principal: CurrentPrincipalDep) -> dict[str, str]:
    return {"tenant_id": str(principal.tenant_id), "role": principal.role}


@probe_router.get("/any-member")
async def probe_any_member(principal: MemberDep) -> dict[str, str]:
    return {"role": principal.role}


@probe_router.get("/configuration")
async def probe_configuration(principal: ConfigurationManagerDep) -> dict[str, str]:
    return {"role": principal.role}


@probe_router.get("/account")
async def probe_account(principal: AccountOwnerDep) -> dict[str, str]:
    return {"role": principal.role}


@pytest.fixture
def probe(app: FastAPI) -> FastAPI:
    """Mount the guarded routes on the app the `api` fixture wraps."""
    app.include_router(probe_router)
    return app


def _payload(**overrides: Any) -> dict[str, Any]:
    body: dict[str, Any] = {
        "full_name": "Ana Quispe",
        "email": f"ana-{uuid.uuid4().hex[:12]}@example.com",
        "password": PASSWORD,
        "business_name": "Panaderia Quispe",
    }
    body.update(overrides)
    return body


def _token_from(message: Any) -> str:
    for href in _HREF.findall(message.html):
        query = parse_qs(urlparse(href).query)
        if "token" in query:
            return str(query["token"][0])
    raise AssertionError("no verification link in the email body")


async def _signed_in(api: AsyncClient, mail: MockEmailProvider) -> dict[str, Any]:
    """Register, verify, sign in. Returns ids plus a usable access token."""
    body = _payload()
    created = await api.post(f"{AUTH}/register", json=body)
    assert created.status_code == 201, created.text
    await api.post(f"{AUTH}/verify-email", json={"token": _token_from(mail.last)})

    session = await api.post(f"{AUTH}/login", json={"email": body["email"], "password": PASSWORD})
    assert session.status_code == 200, session.text
    return {**created.json(), "access_token": session.json()["access_token"]}


def _bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


class TestBearerCredentials:
    async def test_no_header_is_401_not_authenticated(
        self, probe: FastAPI, api: AsyncClient
    ) -> None:
        response = await api.get(f"{PROBE}/user")

        assert response.status_code == 401
        assert response.json()["error"] == "not_authenticated"
        assert response.headers["www-authenticate"] == "Bearer"

    @pytest.mark.parametrize(
        "header",
        [
            {"Authorization": "Bearer "},
            {"Authorization": "Basic YWRtaW46YWRtaW4="},
            {"Authorization": "some-token-without-a-scheme"},
        ],
    )
    async def test_a_header_that_is_not_a_bearer_token_is_401(
        self, probe: FastAPI, api: AsyncClient, header: dict[str, str]
    ) -> None:
        response = await api.get(f"{PROBE}/user", headers=header)

        assert response.status_code == 401
        assert response.json()["error"] in {"not_authenticated", "invalid_token"}

    async def test_a_forged_token_is_401_invalid_token(
        self, probe: FastAPI, api: AsyncClient
    ) -> None:
        forged, _ = issue_access_token(
            user_id=uuid.uuid4(),
            tenant_id=None,
            now=datetime.now(UTC),
            settings=SETTINGS.model_copy(update={"secret_key": "not-our-signing-key-at-all"}),
        )

        response = await api.get(f"{PROBE}/user", headers=_bearer(forged))

        assert response.status_code == 401
        assert response.json()["error"] == "invalid_token"

    async def test_an_expired_token_says_so(
        self, probe: FastAPI, api: AsyncClient, mail: MockEmailProvider
    ) -> None:
        """A distinct code from `invalid_token`: the client's move is to
        refresh, not to send the user back to the login form."""
        account = await _signed_in(api, mail)
        stale, _ = issue_access_token(
            user_id=uuid.UUID(account["user_id"]),
            tenant_id=uuid.UUID(account["tenant_id"]),
            now=datetime.now(UTC) - timedelta(hours=2),
        )

        response = await api.get(f"{PROBE}/user", headers=_bearer(stale))

        assert response.status_code == 401
        assert response.json()["error"] == "token_expired"

    async def test_the_error_body_uses_the_documented_envelope(
        self, probe: FastAPI, api: AsyncClient
    ) -> None:
        body = (await api.get(f"{PROBE}/user")).json()

        assert set(body) == {"error", "message"}
        assert isinstance(body["message"], str)

    async def test_the_openapi_schema_advertises_the_bearer_scheme(
        self, probe: FastAPI, api: AsyncClient
    ) -> None:
        """TASK-020 generates its client from this document."""
        schema = probe.openapi()

        assert "bearerAuth" in schema["components"]["securitySchemes"]


class TestCurrentUser:
    async def test_a_valid_token_resolves_the_user(
        self, probe: FastAPI, api: AsyncClient, mail: MockEmailProvider
    ) -> None:
        account = await _signed_in(api, mail)

        response = await api.get(f"{PROBE}/user", headers=_bearer(account["access_token"]))

        assert response.status_code == 200
        assert response.json()["user_id"] == account["user_id"]

    async def test_an_unverified_email_is_denied_access(
        self, probe: FastAPI, api: AsyncClient, db: Session, mail: MockEmailProvider
    ) -> None:
        """Clarification 005, enforced on every authenticated request and not
        only at the login door."""
        account = await _signed_in(api, mail)
        db.execute(
            update(User)
            .where(User.id == uuid.UUID(account["user_id"]))
            .values(email_verified_at=None)
        )

        response = await api.get(f"{PROBE}/user", headers=_bearer(account["access_token"]))

        assert response.status_code == 403
        assert response.json()["error"] == "email_not_verified"

    async def test_a_token_for_a_deleted_user_is_refused(
        self, probe: FastAPI, api: AsyncClient
    ) -> None:
        orphan, _ = issue_access_token(user_id=uuid.uuid4(), tenant_id=None, now=datetime.now(UTC))

        response = await api.get(f"{PROBE}/user", headers=_bearer(orphan))

        assert response.status_code == 401
        assert response.json()["error"] == "invalid_token"


class TestActiveTenant:
    async def test_the_tenant_claim_resolves_to_a_membership(
        self, probe: FastAPI, api: AsyncClient, mail: MockEmailProvider
    ) -> None:
        account = await _signed_in(api, mail)

        response = await api.get(f"{PROBE}/tenant", headers=_bearer(account["access_token"]))

        assert response.status_code == 200
        assert response.json() == {"tenant_id": account["tenant_id"], "role": "owner"}

    async def test_a_token_with_no_tenant_cannot_reach_a_tenant_endpoint(
        self, probe: FastAPI, api: AsyncClient, db: Session, mail: MockEmailProvider
    ) -> None:
        """A platform admin belongs to no tenant: authenticated, but with
        nothing to act in."""
        account = await _signed_in(api, mail)
        tenantless, _ = issue_access_token(
            user_id=uuid.UUID(account["user_id"]), tenant_id=None, now=datetime.now(UTC)
        )

        response = await api.get(f"{PROBE}/tenant", headers=_bearer(tenantless))

        assert response.status_code == 403
        assert response.json()["error"] == "no_active_tenant"

    async def test_a_tenant_that_does_not_exist_is_refused(
        self, probe: FastAPI, api: AsyncClient, mail: MockEmailProvider
    ) -> None:
        account = await _signed_in(api, mail)
        invented, _ = issue_access_token(
            user_id=uuid.UUID(account["user_id"]),
            tenant_id=uuid.uuid4(),
            now=datetime.now(UTC),
        )

        response = await api.get(f"{PROBE}/tenant", headers=_bearer(invented))

        assert response.status_code == 403
        assert response.json()["error"] == "tenant_access_denied"

    async def test_a_disabled_membership_loses_access_immediately(
        self, probe: FastAPI, api: AsyncClient, db: Session, mail: MockEmailProvider
    ) -> None:
        """The reason the role is not a token claim.

        TASK-009 has to revoke access when a member is deactivated. With the
        membership read on every request that is instant; with it baked into
        the token it would take up to fifteen minutes.
        """
        account = await _signed_in(api, mail)
        assert (
            await api.get(f"{PROBE}/tenant", headers=_bearer(account["access_token"]))
        ).status_code == 200

        db.execute(
            update(TenantUser)
            .where(TenantUser.tenant_id == uuid.UUID(account["tenant_id"]))
            .values(status="disabled")
        )

        response = await api.get(f"{PROBE}/tenant", headers=_bearer(account["access_token"]))

        assert response.status_code == 403
        assert response.json()["error"] == "tenant_access_denied"

    async def test_a_removed_membership_loses_access_immediately(
        self, probe: FastAPI, api: AsyncClient, db: Session, mail: MockEmailProvider
    ) -> None:
        account = await _signed_in(api, mail)
        db.execute(
            TenantUser.__table__.delete().where(
                TenantUser.tenant_id == uuid.UUID(account["tenant_id"])
            )
        )

        response = await api.get(f"{PROBE}/tenant", headers=_bearer(account["access_token"]))

        assert response.status_code == 403

    async def test_the_current_role_is_read_not_remembered(
        self, probe: FastAPI, api: AsyncClient, db: Session, mail: MockEmailProvider
    ) -> None:
        """A demotion takes effect on the very next request."""
        account = await _signed_in(api, mail)
        token = account["access_token"]
        assert (await api.get(f"{PROBE}/tenant", headers=_bearer(token))).json()["role"] == "owner"

        db.execute(
            update(TenantUser)
            .where(TenantUser.tenant_id == uuid.UUID(account["tenant_id"]))
            .values(role="operator")
        )

        assert (await api.get(f"{PROBE}/tenant", headers=_bearer(token))).json()[
            "role"
        ] == "operator"


class TestRoleGuards:
    """Clarification 008: owner everything, tenant_admin everything except
    deleting the account and billing, operator the inbox only."""

    @staticmethod
    async def _as_role(
        api: AsyncClient, db: Session, mail: MockEmailProvider, role: str
    ) -> dict[str, Any]:
        account = await _signed_in(api, mail)
        db.execute(
            update(TenantUser)
            .where(TenantUser.tenant_id == uuid.UUID(account["tenant_id"]))
            .values(role=role)
        )
        return account

    @pytest.mark.parametrize("role", ["owner", "tenant_admin", "operator"])
    async def test_every_role_passes_the_any_member_guard(
        self, probe: FastAPI, api: AsyncClient, db: Session, mail: MockEmailProvider, role: str
    ) -> None:
        account = await self._as_role(api, db, mail, role)

        response = await api.get(f"{PROBE}/any-member", headers=_bearer(account["access_token"]))

        assert response.status_code == 200
        assert response.json()["role"] == role

    @pytest.mark.parametrize(
        ("role", "allowed"),
        [("owner", True), ("tenant_admin", True), ("operator", False)],
    )
    async def test_configuration_is_closed_to_operators(
        self,
        probe: FastAPI,
        api: AsyncClient,
        db: Session,
        mail: MockEmailProvider,
        role: str,
        allowed: bool,
    ) -> None:
        account = await self._as_role(api, db, mail, role)

        response = await api.get(f"{PROBE}/configuration", headers=_bearer(account["access_token"]))

        assert (response.status_code == 200) is allowed
        if not allowed:
            assert response.json()["error"] == "insufficient_role"

    @pytest.mark.parametrize(
        ("role", "allowed"),
        [("owner", True), ("tenant_admin", False), ("operator", False)],
    )
    async def test_only_the_owner_reaches_account_level_actions(
        self,
        probe: FastAPI,
        api: AsyncClient,
        db: Session,
        mail: MockEmailProvider,
        role: str,
        allowed: bool,
    ) -> None:
        """Deleting the account and managing billing are the owner's alone."""
        account = await self._as_role(api, db, mail, role)

        response = await api.get(f"{PROBE}/account", headers=_bearer(account["access_token"]))

        assert (response.status_code == 200) is allowed

    async def test_a_guard_still_requires_authentication(
        self, probe: FastAPI, api: AsyncClient
    ) -> None:
        """The role check must not be reachable without a token."""
        response = await api.get(f"{PROBE}/configuration")

        assert response.status_code == 401

    def test_a_guard_with_no_roles_is_a_programming_error(self) -> None:
        """`require_roles()` with an empty list would admit nobody, silently."""
        with pytest.raises(ValueError, match="at least one role"):
            require_roles()

    def test_the_role_sets_match_the_clarification(self) -> None:
        assert set(ANY_MEMBER) == {"owner", "tenant_admin", "operator"}
        assert set(MANAGES_CONFIGURATION) == {"owner", "tenant_admin"}
        assert set(OWNS_THE_ACCOUNT) == {"owner"}


class TestCrossAccountAccess:
    async def test_a_token_naming_another_users_tenant_is_refused(
        self, probe: FastAPI, api: AsyncClient, db: Session, mail: MockEmailProvider
    ) -> None:
        """Identity is taken from the token; authority never is."""
        account = await _signed_in(api, mail)
        stranger = Tenant(name="Somebody Else SAC")
        db.add(stranger)
        db.flush()

        crafted, _ = issue_access_token(
            user_id=uuid.UUID(account["user_id"]),
            tenant_id=stranger.id,
            now=datetime.now(UTC),
        )
        response = await api.get(f"{PROBE}/tenant", headers=_bearer(crafted))

        assert response.status_code == 403
        assert response.json()["error"] == "tenant_access_denied"

    async def test_the_refusal_does_not_reveal_whether_the_tenant_exists(
        self, probe: FastAPI, api: AsyncClient, db: Session, mail: MockEmailProvider
    ) -> None:
        """Otherwise the endpoint becomes a way to probe for real accounts."""
        account = await _signed_in(api, mail)
        real = Tenant(name="Somebody Else SAC")
        db.add(real)
        db.flush()

        for tenant_id in (real.id, uuid.uuid4()):
            token, _ = issue_access_token(
                user_id=uuid.UUID(account["user_id"]),
                tenant_id=tenant_id,
                now=datetime.now(UTC),
            )
            answer = await api.get(f"{PROBE}/tenant", headers=_bearer(token))
            assert answer.status_code == 403
            assert answer.json()["error"] == "tenant_access_denied"
