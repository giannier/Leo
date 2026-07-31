"""End-to-end tests for TASK-007: login, rotating refresh, logout.

Everything is driven through the real HTTP endpoints against the real
database inside a rolled-back transaction, so what is asserted is what a
client actually gets - including the cookie attributes, which are half the
security of the design and are invisible from the response body.

The heart of the file is `TestRefreshReuseDetection`. Rotation on its own
only limits the *window* in which a stolen refresh token is useful; what
makes theft **detectable** is that the stolen copy and the real one are the
same row, so the second party to present it shows up with something already
revoked. Those tests are the ones that would let the whole design down
silently if they were ever weakened.
"""

from __future__ import annotations

import re
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import parse_qs, urlparse

import pytest
from httpx import AsyncClient
from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.security import hash_token
from app.core.tokens import decode_access_token
from app.models import RefreshToken, Tenant, TenantUser, User
from app.providers.email.mock import MockEmailProvider

SETTINGS = get_settings()
AUTH = f"{SETTINGS.api_v1_prefix}/auth"
REFRESH_COOKIE = "refresh_token"
EXPECTED_COOKIE_PATH = f"{SETTINGS.api_v1_prefix}/auth"

PASSWORD = "una-clave-suficientemente-larga"

_HREF = re.compile(r'href="([^"]+)"')


def _unique_phone() -> str:
    return f"+519{uuid.uuid4().int % 10**8:08d}"


def _payload(**overrides: Any) -> dict[str, Any]:
    body: dict[str, Any] = {
        "full_name": "Ana Quispe",
        "email": f"ana-{uuid.uuid4().hex[:12]}@example.com",
        "password": PASSWORD,
        "phone_e164": _unique_phone(),
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


async def _register_verified(
    api: AsyncClient, mail: MockEmailProvider, **overrides: Any
) -> dict[str, Any]:
    """Register an account and click the verification link.

    Returns the registration body plus the ids, because almost every test
    below needs a usable account and none of them are about registration.
    """
    body = _payload(**overrides)
    created = await api.post(f"{AUTH}/register", json=body)
    assert created.status_code == 201, created.text

    verified = await api.post(f"{AUTH}/verify-email", json={"token": _token_from(mail.last)})
    assert verified.status_code == 204

    return {**body, **created.json()}


async def _login(api: AsyncClient, account: dict[str, Any], **extra: Any) -> Any:
    return await api.post(
        f"{AUTH}/login",
        json={"email": account["email"], "password": account["password"], **extra},
    )


def _refresh_cookie_of(response: Any) -> str:
    token = response.cookies.get(REFRESH_COOKIE)
    assert token, f"no refresh cookie on the response: {dict(response.cookies)}"
    return str(token)


def _set_cookie_header(response: Any) -> str:
    for name, value in response.headers.multi_items():
        if name.lower() == "set-cookie" and value.startswith(f"{REFRESH_COOKIE}="):
            return value
    raise AssertionError(f"no {REFRESH_COOKIE} Set-Cookie header: {response.headers}")


def _assert_deletes_the_cookie(header: str) -> None:
    """A `Set-Cookie` that really removes it, not one that re-sets it.

    Every attribute has to match how the cookie was set or the browser treats
    it as a *different* cookie and leaves the original in place - which is
    why `Path` is checked here and not just the expiry.
    """
    assert "Max-Age=0" in header, header
    assert 'refresh_token=""' in header or "refresh_token=;" in header, header
    assert f"Path={EXPECTED_COOKIE_PATH}" in header, header
    assert "HttpOnly" in header, header


def _tokens_of(db: Session, user_id: uuid.UUID) -> list[RefreshToken]:
    statement = (
        select(RefreshToken)
        .where(RefreshToken.user_id == user_id)
        .order_by(RefreshToken.created_at)
    )
    return list(db.execute(statement).scalars().all())


class TestLogin:
    async def test_a_verified_account_receives_an_access_token(
        self, api: AsyncClient, mail: MockEmailProvider
    ) -> None:
        account = await _register_verified(api, mail)

        response = await _login(api, account)

        assert response.status_code == 200
        body = response.json()
        assert body["token_type"] == "bearer"
        assert body["user"]["email"] == account["email"]
        assert body["user"]["full_name"] == "Ana Quispe"
        assert body["user"]["id"] == account["user_id"]

    async def test_the_access_token_carries_the_caller_and_their_tenant(
        self, api: AsyncClient, mail: MockEmailProvider
    ) -> None:
        account = await _register_verified(api, mail)

        body = (await _login(api, account)).json()
        claims = decode_access_token(body["access_token"])

        assert claims.user_id == uuid.UUID(account["user_id"])
        assert claims.tenant_id == uuid.UUID(account["tenant_id"])
        assert body["active_tenant_id"] == account["tenant_id"]

    async def test_the_access_token_expires_in_the_contracted_fifteen_minutes(
        self, api: AsyncClient, mail: MockEmailProvider
    ) -> None:
        account = await _register_verified(api, mail)
        before = datetime.now(UTC)

        body = (await _login(api, account)).json()

        assert body["expires_in"] == 15 * 60
        claims = decode_access_token(body["access_token"])
        assert abs((claims.expires_at - before) - timedelta(minutes=15)) < timedelta(seconds=5)

    async def test_memberships_are_returned_so_the_panel_can_choose(
        self, api: AsyncClient, mail: MockEmailProvider
    ) -> None:
        account = await _register_verified(api, mail)

        memberships = (await _login(api, account)).json()["memberships"]

        assert memberships == [
            {
                "tenant_id": account["tenant_id"],
                "tenant_name": "Panaderia Quispe",
                "role": "owner",
            }
        ]

    async def test_the_password_is_never_echoed_back(
        self, api: AsyncClient, mail: MockEmailProvider
    ) -> None:
        account = await _register_verified(api, mail)

        response = await _login(api, account)

        assert PASSWORD not in response.text

    async def test_a_wrong_password_is_401_invalid_credentials(
        self, api: AsyncClient, mail: MockEmailProvider
    ) -> None:
        account = await _register_verified(api, mail)

        response = await api.post(
            f"{AUTH}/login", json={"email": account["email"], "password": "not-the-password"}
        )

        assert response.status_code == 401
        assert response.json()["error"] == "invalid_credentials"

    async def test_an_unknown_address_answers_exactly_like_a_wrong_password(
        self, api: AsyncClient, mail: MockEmailProvider
    ) -> None:
        """No account-enumeration oracle: the two answers are identical."""
        account = await _register_verified(api, mail)

        wrong_password = await api.post(
            f"{AUTH}/login", json={"email": account["email"], "password": "not-the-password"}
        )
        unknown_address = await api.post(
            f"{AUTH}/login", json={"email": "nobody@example.com", "password": PASSWORD}
        )

        assert wrong_password.status_code == unknown_address.status_code == 401
        assert wrong_password.json() == unknown_address.json()

    async def test_a_failed_login_issues_no_refresh_token(
        self, api: AsyncClient, db: Session, mail: MockEmailProvider
    ) -> None:
        account = await _register_verified(api, mail)

        await api.post(
            f"{AUTH}/login", json={"email": account["email"], "password": "wrong-password"}
        )

        assert _tokens_of(db, uuid.UUID(account["user_id"])) == []

    async def test_login_is_case_insensitive_about_the_address(
        self, api: AsyncClient, mail: MockEmailProvider
    ) -> None:
        """`users.email` is CITEXT."""
        await _register_verified(api, mail, email="Ana.Quispe@Example.com")

        response = await api.post(
            f"{AUTH}/login", json={"email": "ANA.QUISPE@example.com", "password": PASSWORD}
        )

        assert response.status_code == 200

    async def test_an_invalid_body_is_400_validation_error(self, api: AsyncClient) -> None:
        response = await api.post(f"{AUTH}/login", json={"email": "nope", "password": "x"})

        assert response.status_code == 400
        assert response.json()["error"] == "validation_error"


class TestEmailVerificationGate:
    async def test_an_unverified_account_cannot_sign_in(
        self, api: AsyncClient, mail: MockEmailProvider
    ) -> None:
        """Clarification 005: a verified email is what admits you at all."""
        body = _payload()
        assert (await api.post(f"{AUTH}/register", json=body)).status_code == 201

        response = await api.post(
            f"{AUTH}/login", json={"email": body["email"], "password": PASSWORD}
        )

        assert response.status_code == 403
        assert response.json()["error"] == "email_not_verified"

    async def test_the_refusal_is_not_a_wrong_password(
        self, api: AsyncClient, mail: MockEmailProvider
    ) -> None:
        """403, not 401: the credentials were right, the account is not ready.

        Telling the two apart matters here - the panel has to show "check
        your inbox", not "wrong password", or the customer will reset a
        password that was never the problem.
        """
        body = _payload()
        await api.post(f"{AUTH}/register", json=body)

        wrong = await api.post(
            f"{AUTH}/login", json={"email": body["email"], "password": "wrong-password"}
        )
        unverified = await api.post(
            f"{AUTH}/login", json={"email": body["email"], "password": PASSWORD}
        )

        assert wrong.status_code == 401
        assert unverified.status_code == 403

    async def test_no_session_is_opened_for_an_unverified_account(
        self, api: AsyncClient, db: Session
    ) -> None:
        body = _payload()
        created = await api.post(f"{AUTH}/register", json=body)
        await api.post(f"{AUTH}/login", json={"email": body["email"], "password": PASSWORD})

        assert _tokens_of(db, uuid.UUID(created.json()["user_id"])) == []

    async def test_verifying_the_address_unlocks_the_login(
        self, api: AsyncClient, mail: MockEmailProvider
    ) -> None:
        body = _payload()
        await api.post(f"{AUTH}/register", json=body)
        refused = await api.post(
            f"{AUTH}/login", json={"email": body["email"], "password": PASSWORD}
        )
        assert refused.status_code == 403

        await api.post(f"{AUTH}/verify-email", json={"token": _token_from(mail.last)})

        allowed = await api.post(
            f"{AUTH}/login", json={"email": body["email"], "password": PASSWORD}
        )
        assert allowed.status_code == 200

    async def test_a_live_session_stops_working_if_verification_is_withdrawn(
        self, api: AsyncClient, db: Session, mail: MockEmailProvider
    ) -> None:
        """The gate is re-checked on refresh, not only at login."""
        account = await _register_verified(api, mail)
        assert (await _login(api, account)).status_code == 200

        db.execute(
            update(User)
            .where(User.id == uuid.UUID(account["user_id"]))
            .values(email_verified_at=None)
        )

        response = await api.post(f"{AUTH}/refresh")

        assert response.status_code == 403
        assert response.json()["error"] == "email_not_verified"


class TestRefreshCookie:
    async def test_the_refresh_token_is_never_in_the_response_body(
        self, api: AsyncClient, db: Session, mail: MockEmailProvider
    ) -> None:
        """It is the long-lived half of the session: script must not see it."""
        account = await _register_verified(api, mail)

        response = await _login(api, account)

        cookie_value = _refresh_cookie_of(response)
        assert cookie_value not in response.text
        assert "refresh" not in response.json()

    async def test_the_cookie_is_httponly_and_scoped_to_the_auth_path(
        self, api: AsyncClient, mail: MockEmailProvider
    ) -> None:
        account = await _register_verified(api, mail)

        header = _set_cookie_header(await _login(api, account))

        assert "HttpOnly" in header
        assert f"Path={EXPECTED_COOKIE_PATH}" in header
        assert "SameSite=lax" in header.replace("samesite", "SameSite")
        # No Domain attribute: a host-only cookie is not shared with
        # subdomains that may be operated by someone else.
        assert "Domain=" not in header

    async def test_the_cookie_lives_as_long_as_the_token_it_carries(
        self, api: AsyncClient, mail: MockEmailProvider
    ) -> None:
        """A browser that kept presenting an expired token would just be
        collecting 401s."""
        account = await _register_verified(api, mail)

        header = _set_cookie_header(await _login(api, account))

        assert f"Max-Age={SETTINGS.refresh_token_expire_days * 24 * 60 * 60}" in header

    async def test_the_secure_flag_follows_the_environment(
        self, api: AsyncClient, mail: MockEmailProvider
    ) -> None:
        """Off on plain-HTTP localhost, on wherever real users are served.

        Set unconditionally, the cookie would simply never be stored during
        local development; never set, the token would travel in clear text in
        production.
        """
        account = await _register_verified(api, mail)
        header = _set_cookie_header(await _login(api, account))

        assert ("Secure" in header) is SETTINGS.is_deployed

    async def test_only_the_digest_of_the_token_reaches_the_database(
        self, api: AsyncClient, db: Session, mail: MockEmailProvider
    ) -> None:
        """A dump of `refresh_tokens` must yield nothing presentable."""
        account = await _register_verified(api, mail)

        response = await _login(api, account)
        plaintext = _refresh_cookie_of(response)

        stored = _tokens_of(db, uuid.UUID(account["user_id"]))
        assert len(stored) == 1
        assert stored[0].token_hash == hash_token(plaintext)
        assert plaintext not in stored[0].token_hash

    async def test_the_refresh_token_expires_in_thirty_days(
        self, api: AsyncClient, db: Session, mail: MockEmailProvider
    ) -> None:
        account = await _register_verified(api, mail)
        before = datetime.now(UTC)

        await _login(api, account)

        stored = _tokens_of(db, uuid.UUID(account["user_id"]))[0]
        assert abs((stored.expires_at - before) - timedelta(days=30)) < timedelta(seconds=5)


class TestRefreshRotation:
    async def test_refreshing_returns_a_new_access_token(
        self, api: AsyncClient, mail: MockEmailProvider
    ) -> None:
        account = await _register_verified(api, mail)
        first = (await _login(api, account)).json()["access_token"]

        response = await api.post(f"{AUTH}/refresh")

        assert response.status_code == 200
        assert response.json()["access_token"] != first

    async def test_refreshing_replaces_the_cookie(
        self, api: AsyncClient, mail: MockEmailProvider
    ) -> None:
        account = await _register_verified(api, mail)
        first = _refresh_cookie_of(await _login(api, account))

        second = _refresh_cookie_of(await api.post(f"{AUTH}/refresh"))

        assert second != first

    async def test_the_presented_token_is_revoked_by_the_rotation(
        self, api: AsyncClient, db: Session, mail: MockEmailProvider
    ) -> None:
        account = await _register_verified(api, mail)
        first = _refresh_cookie_of(await _login(api, account))

        await api.post(f"{AUTH}/refresh")

        rows = _tokens_of(db, uuid.UUID(account["user_id"]))
        assert len(rows) == 2
        used = next(row for row in rows if row.token_hash == hash_token(first))
        assert used.revoked_at is not None
        successor = next(row for row in rows if row.token_hash != hash_token(first))
        assert successor.revoked_at is None

    async def test_the_successor_stays_in_the_same_family(
        self, api: AsyncClient, db: Session, mail: MockEmailProvider
    ) -> None:
        """Rotation extends one session chain; it does not start a new one."""
        account = await _register_verified(api, mail)
        await _login(api, account)

        await api.post(f"{AUTH}/refresh")
        await api.post(f"{AUTH}/refresh")

        rows = _tokens_of(db, uuid.UUID(account["user_id"]))
        assert len(rows) == 3
        assert len({row.family_id for row in rows}) == 1

    async def test_a_second_login_starts_a_separate_family(
        self, api: AsyncClient, db: Session, mail: MockEmailProvider
    ) -> None:
        """Signing in on another device must not join the existing chain, or
        a theft on one device would revoke the other."""
        account = await _register_verified(api, mail)
        await _login(api, account)

        await _login(api, account)

        rows = _tokens_of(db, uuid.UUID(account["user_id"]))
        assert len({row.family_id for row in rows}) == 2

    async def test_a_chain_of_rotations_keeps_working(
        self, api: AsyncClient, mail: MockEmailProvider
    ) -> None:
        account = await _register_verified(api, mail)
        await _login(api, account)

        for _ in range(5):
            assert (await api.post(f"{AUTH}/refresh")).status_code == 200

    async def test_refreshing_without_a_cookie_is_401_invalid_refresh(
        self, api: AsyncClient
    ) -> None:
        response = await api.post(f"{AUTH}/refresh")

        assert response.status_code == 401
        assert response.json()["error"] == "invalid_refresh"

    async def test_an_unknown_token_is_401_invalid_refresh(self, api: AsyncClient) -> None:
        api.cookies.set(REFRESH_COOKIE, "not-a-real-token", path=EXPECTED_COOKIE_PATH)

        response = await api.post(f"{AUTH}/refresh")

        assert response.status_code == 401
        assert response.json()["error"] == "invalid_refresh"

    async def test_a_refused_refresh_clears_the_dead_cookie(self, api: AsyncClient) -> None:
        """Otherwise the browser keeps presenting it for the rest of its
        30-day `Max-Age` and every refresh is another 401.

        This one is easy to get wrong: FastAPI merges the injected
        `Response`'s headers into a *returned* body, but an exception goes
        straight to the error handler and never sees them. Clearing the
        cookie on a failed refresh therefore has to travel on the `ApiError`
        itself - do it on the injected response and the header silently never
        ships.
        """
        api.cookies.set(REFRESH_COOKIE, "not-a-real-token", path=EXPECTED_COOKIE_PATH)

        response = await api.post(f"{AUTH}/refresh")

        assert response.status_code == 401
        _assert_deletes_the_cookie(_set_cookie_header(response))

    async def test_a_detected_reuse_also_clears_the_cookie(
        self, api: AsyncClient, mail: MockEmailProvider
    ) -> None:
        account = await _register_verified(api, mail)
        stolen = _refresh_cookie_of(await _login(api, account))
        await api.post(f"{AUTH}/refresh")

        api.cookies.set(REFRESH_COOKIE, stolen, path=EXPECTED_COOKIE_PATH)
        response = await api.post(f"{AUTH}/refresh")

        assert response.status_code == 401
        _assert_deletes_the_cookie(_set_cookie_header(response))

    async def test_an_expired_token_is_refused(
        self, api: AsyncClient, db: Session, mail: MockEmailProvider
    ) -> None:
        account = await _register_verified(api, mail)
        await _login(api, account)
        db.execute(
            update(RefreshToken)
            .where(RefreshToken.user_id == uuid.UUID(account["user_id"]))
            .values(expires_at=datetime.now(UTC) - timedelta(minutes=1))
        )

        response = await api.post(f"{AUTH}/refresh")

        assert response.status_code == 401
        assert response.json()["error"] == "invalid_refresh"

    async def test_an_expired_token_is_not_treated_as_a_theft(
        self, api: AsyncClient, db: Session, mail: MockEmailProvider
    ) -> None:
        """Expiry is the normal end of a session, not a security event: the
        family must not be marked revoked just for ageing out."""
        account = await _register_verified(api, mail)
        await _login(api, account)
        db.execute(
            update(RefreshToken)
            .where(RefreshToken.user_id == uuid.UUID(account["user_id"]))
            .values(expires_at=datetime.now(UTC) - timedelta(minutes=1))
        )

        await api.post(f"{AUTH}/refresh")

        db.expire_all()
        rows = _tokens_of(db, uuid.UUID(account["user_id"]))
        assert [row.revoked_at for row in rows] == [None]


class TestRefreshReuseDetection:
    """Presenting an already-revoked token means two parties hold the same
    credential. Since neither can be told from the other, the whole chain
    dies and both are sent back through the password."""

    async def test_replaying_a_rotated_token_is_refused(
        self, api: AsyncClient, mail: MockEmailProvider
    ) -> None:
        account = await _register_verified(api, mail)
        stolen = _refresh_cookie_of(await _login(api, account))
        await api.post(f"{AUTH}/refresh")  # the legitimate rotation

        api.cookies.set(REFRESH_COOKIE, stolen, path=EXPECTED_COOKIE_PATH)
        replay = await api.post(f"{AUTH}/refresh")

        assert replay.status_code == 401
        assert replay.json()["error"] == "invalid_refresh"

    async def test_the_replay_revokes_the_whole_family(
        self, api: AsyncClient, db: Session, mail: MockEmailProvider
    ) -> None:
        """The point of the whole design: the thief's *successor* dies too."""
        account = await _register_verified(api, mail)
        stolen = _refresh_cookie_of(await _login(api, account))
        await api.post(f"{AUTH}/refresh")

        api.cookies.set(REFRESH_COOKIE, stolen, path=EXPECTED_COOKIE_PATH)
        await api.post(f"{AUTH}/refresh")

        db.expire_all()
        rows = _tokens_of(db, uuid.UUID(account["user_id"]))
        assert len(rows) == 2
        assert all(row.revoked_at is not None for row in rows)

    async def test_the_live_token_stops_working_after_the_replay(
        self, api: AsyncClient, db: Session, mail: MockEmailProvider
    ) -> None:
        """Whoever refreshed legitimately is logged out as well.

        That is the deliberate cost: there is no way to tell the victim from
        the thief, so both go back through the password - which only one of
        them has.
        """
        account = await _register_verified(api, mail)
        stolen = _refresh_cookie_of(await _login(api, account))
        live = _refresh_cookie_of(await api.post(f"{AUTH}/refresh"))

        api.cookies.set(REFRESH_COOKIE, stolen, path=EXPECTED_COOKIE_PATH)
        await api.post(f"{AUTH}/refresh")

        api.cookies.set(REFRESH_COOKIE, live, path=EXPECTED_COOKIE_PATH)
        response = await api.post(f"{AUTH}/refresh")

        assert response.status_code == 401
        assert response.json()["error"] == "invalid_refresh"

    async def test_the_revocation_survives_the_failed_request(
        self, api: AsyncClient, db: Session, mail: MockEmailProvider
    ) -> None:
        """The regression this guards against is subtle.

        The reuse response is a 401, which propagates out through `get_db` -
        and that dependency rolls the session back on the way past. If the
        family revocation were not committed before the exception is raised,
        the rollback would quietly undo it: theft detected, nothing done.
        """
        account = await _register_verified(api, mail)
        stolen = _refresh_cookie_of(await _login(api, account))
        await api.post(f"{AUTH}/refresh")

        api.cookies.set(REFRESH_COOKIE, stolen, path=EXPECTED_COOKIE_PATH)
        assert (await api.post(f"{AUTH}/refresh")).status_code == 401

        # Read in a way that cannot see uncommitted identity-map state.
        db.expire_all()
        live = db.execute(
            select(func.count())
            .select_from(RefreshToken)
            .where(
                RefreshToken.user_id == uuid.UUID(account["user_id"]),
                RefreshToken.revoked_at.is_(None),
            )
        ).scalar_one()
        assert live == 0

    async def test_other_devices_are_left_alone(
        self, api: AsyncClient, db: Session, mail: MockEmailProvider
    ) -> None:
        """Only the compromised chain dies.

        This is what `family_id` buys over "revoke everything this user has":
        a theft on a phone does not sign the customer out of their laptop.
        """
        account = await _register_verified(api, mail)
        stolen = _refresh_cookie_of(await _login(api, account))
        await api.post(f"{AUTH}/refresh")
        other_device = _refresh_cookie_of(await _login(api, account))

        api.cookies.set(REFRESH_COOKIE, stolen, path=EXPECTED_COOKIE_PATH)
        await api.post(f"{AUTH}/refresh")

        api.cookies.set(REFRESH_COOKIE, other_device, path=EXPECTED_COOKIE_PATH)
        response = await api.post(f"{AUTH}/refresh")

        assert response.status_code == 200

    async def test_replaying_a_logged_out_token_also_kills_the_family(
        self, api: AsyncClient, db: Session, mail: MockEmailProvider
    ) -> None:
        """A revoked token is a revoked token, whatever revoked it."""
        account = await _register_verified(api, mail)
        signed_out = _refresh_cookie_of(await _login(api, account))
        await api.post(f"{AUTH}/logout")

        api.cookies.set(REFRESH_COOKIE, signed_out, path=EXPECTED_COOKIE_PATH)
        response = await api.post(f"{AUTH}/refresh")

        assert response.status_code == 401

    async def test_the_detection_is_logged(
        self, api: AsyncClient, mail: MockEmailProvider, caplog: pytest.LogCaptureFixture
    ) -> None:
        """Silent detection is not detection: the operator has to be able to
        see that someone's session was compromised."""
        account = await _register_verified(api, mail)
        stolen = _refresh_cookie_of(await _login(api, account))
        await api.post(f"{AUTH}/refresh")

        api.cookies.set(REFRESH_COOKIE, stolen, path=EXPECTED_COOKIE_PATH)
        with caplog.at_level("WARNING", logger="app.services.sessions"):
            await api.post(f"{AUTH}/refresh")

        assert any("reuse detected" in record.message for record in caplog.records)
        # The log line carries ids, never the address or the token itself
        # (Ley 29733, and the Constitution's logging standard).
        assert all(account["email"] not in record.getMessage() for record in caplog.records)


class TestLogout:
    async def test_logout_revokes_the_presented_token(
        self, api: AsyncClient, db: Session, mail: MockEmailProvider
    ) -> None:
        account = await _register_verified(api, mail)
        await _login(api, account)

        response = await api.post(f"{AUTH}/logout")

        assert response.status_code == 204
        db.expire_all()
        rows = _tokens_of(db, uuid.UUID(account["user_id"]))
        assert [row.revoked_at is not None for row in rows] == [True]

    async def test_the_token_cannot_be_used_afterwards(
        self, api: AsyncClient, mail: MockEmailProvider
    ) -> None:
        account = await _register_verified(api, mail)
        token = _refresh_cookie_of(await _login(api, account))
        await api.post(f"{AUTH}/logout")

        api.cookies.set(REFRESH_COOKIE, token, path=EXPECTED_COOKIE_PATH)
        response = await api.post(f"{AUTH}/refresh")

        assert response.status_code == 401

    async def test_logout_clears_the_cookie(
        self, api: AsyncClient, mail: MockEmailProvider
    ) -> None:
        account = await _register_verified(api, mail)
        await _login(api, account)

        response = await api.post(f"{AUTH}/logout")

        _assert_deletes_the_cookie(_set_cookie_header(response))
        # End-to-end proof that the attributes really do match: this cookie
        # was set by the server, so the client's own jar drops it.
        assert not api.cookies.get(REFRESH_COOKIE)

    async def test_logout_without_a_session_is_still_204(self, api: AsyncClient) -> None:
        """A client that cannot clear its own cookie because the server
        objected is strictly worse off than one that just succeeds."""
        response = await api.post(f"{AUTH}/logout")

        assert response.status_code == 204

    async def test_logout_is_idempotent(self, api: AsyncClient, mail: MockEmailProvider) -> None:
        account = await _register_verified(api, mail)
        token = _refresh_cookie_of(await _login(api, account))
        await api.post(f"{AUTH}/logout")

        api.cookies.set(REFRESH_COOKIE, token, path=EXPECTED_COOKIE_PATH)
        assert (await api.post(f"{AUTH}/logout")).status_code == 204

    async def test_logout_leaves_other_devices_signed_in(
        self, api: AsyncClient, mail: MockEmailProvider
    ) -> None:
        """Signing out of one browser is not a security event."""
        account = await _register_verified(api, mail)
        other_device = _refresh_cookie_of(await _login(api, account))
        await _login(api, account)

        await api.post(f"{AUTH}/logout")

        api.cookies.set(REFRESH_COOKIE, other_device, path=EXPECTED_COOKIE_PATH)
        assert (await api.post(f"{AUTH}/refresh")).status_code == 200


class TestTenantSelection:
    """A person can belong to more than one business from TASK-009 onward."""

    @staticmethod
    def _join(db: Session, user_id: uuid.UUID, name: str, role: str) -> uuid.UUID:
        tenant = Tenant(name=name)
        db.add(tenant)
        db.flush()
        db.add(TenantUser(tenant_id=tenant.id, user_id=user_id, role=role))
        db.flush()
        return tenant.id

    async def test_every_active_membership_is_listed(
        self, api: AsyncClient, db: Session, mail: MockEmailProvider
    ) -> None:
        account = await _register_verified(api, mail)
        second = self._join(db, uuid.UUID(account["user_id"]), "Cafe Luna", "operator")

        memberships = (await _login(api, account)).json()["memberships"]

        assert {m["tenant_id"] for m in memberships} == {account["tenant_id"], str(second)}
        roles = {m["tenant_id"]: m["role"] for m in memberships}
        assert roles[str(second)] == "operator"

    async def test_an_explicit_choice_scopes_the_access_token(
        self, api: AsyncClient, db: Session, mail: MockEmailProvider
    ) -> None:
        account = await _register_verified(api, mail)
        second = self._join(db, uuid.UUID(account["user_id"]), "Cafe Luna", "operator")

        body = (await _login(api, account, tenant_id=str(second))).json()

        assert body["active_tenant_id"] == str(second)
        assert decode_access_token(body["access_token"]).tenant_id == second

    async def test_a_tenant_the_caller_does_not_belong_to_is_refused(
        self, api: AsyncClient, db: Session, mail: MockEmailProvider
    ) -> None:
        """Naming a tenant is a request, not a grant."""
        account = await _register_verified(api, mail)
        stranger = Tenant(name="Somebody Else SAC")
        db.add(stranger)
        db.flush()

        response = await _login(api, account, tenant_id=str(stranger.id))

        assert response.status_code == 403
        assert response.json()["error"] == "tenant_access_denied"

    async def test_a_disabled_membership_is_not_offered(
        self, api: AsyncClient, db: Session, mail: MockEmailProvider
    ) -> None:
        account = await _register_verified(api, mail)
        second = self._join(db, uuid.UUID(account["user_id"]), "Cafe Luna", "operator")
        db.execute(
            update(TenantUser).where(TenantUser.tenant_id == second).values(status="disabled")
        )

        memberships = (await _login(api, account)).json()["memberships"]

        assert [m["tenant_id"] for m in memberships] == [account["tenant_id"]]

    async def test_refresh_keeps_the_chosen_tenant_when_asked(
        self, api: AsyncClient, db: Session, mail: MockEmailProvider
    ) -> None:
        account = await _register_verified(api, mail)
        second = self._join(db, uuid.UUID(account["user_id"]), "Cafe Luna", "operator")
        await _login(api, account, tenant_id=str(second))

        body = (await api.post(f"{AUTH}/refresh", json={"tenant_id": str(second)})).json()

        assert decode_access_token(body["access_token"]).tenant_id == second

    async def test_refresh_can_switch_tenant_without_a_password(
        self, api: AsyncClient, db: Session, mail: MockEmailProvider
    ) -> None:
        account = await _register_verified(api, mail)
        second = self._join(db, uuid.UUID(account["user_id"]), "Cafe Luna", "operator")
        await _login(api, account)

        body = (await api.post(f"{AUTH}/refresh", json={"tenant_id": str(second)})).json()

        assert body["active_tenant_id"] == str(second)

    async def test_refresh_cannot_switch_to_a_foreign_tenant(
        self, api: AsyncClient, db: Session, mail: MockEmailProvider
    ) -> None:
        account = await _register_verified(api, mail)
        stranger = Tenant(name="Somebody Else SAC")
        db.add(stranger)
        db.flush()
        await _login(api, account)

        response = await api.post(f"{AUTH}/refresh", json={"tenant_id": str(stranger.id)})

        assert response.status_code == 403
        assert response.json()["error"] == "tenant_access_denied"

    async def test_the_default_choice_is_stable_across_refreshes(
        self, api: AsyncClient, db: Session, mail: MockEmailProvider
    ) -> None:
        """A client that never sends `tenant_id` must not drift between
        businesses every fifteen minutes."""
        account = await _register_verified(api, mail)
        self._join(db, uuid.UUID(account["user_id"]), "Cafe Luna", "operator")
        first = (await _login(api, account)).json()["active_tenant_id"]

        for _ in range(3):
            assert (await api.post(f"{AUTH}/refresh")).json()["active_tenant_id"] == first
