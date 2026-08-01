"""The throttles on `/auth/*`, exercised through the HTTP endpoints.

The shared `api` fixture disables throttling, because every test in the suite
shares one client address and would otherwise burn the allowance on unrelated
assertions. These tests put the real limiter back, against real Redis, keyed
into a namespace unique to each test.

Every endpoint that takes a *guessable* secret shares one window, which is
the point of half the cases below: an attacker throttled at `login` who could
simply move to `resend-verification` would not be throttled at all. `refresh`
and `logout` are deliberately outside it - see the class that pins that.
"""

from __future__ import annotations

import os
import uuid
from collections.abc import AsyncIterator

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from redis.asyncio import Redis
from sqlalchemy.orm import Session

from app.api.deps import (
    client_identifier,
    get_db,
    get_email_provider,
    get_login_limiter,
    get_rate_limiter,
)
from app.core.config import Settings, get_settings
from app.core.rate_limit import NullRateLimiter, RateLimiter, RedisRateLimiter
from app.providers.email.base import EmailProvider
from app.providers.email.mock import MockEmailProvider

SETTINGS = get_settings()
AUTH = f"{SETTINGS.api_v1_prefix}/auth"

REQUIRE_REDIS_ENV_VAR = "LEO_REQUIRE_REDIS"

# Read from configuration, never repeated as a literal. These tests broke
# the day the number changed, which is exactly the failure a hardcoded
# copy is supposed to prevent and instead caused.
IP_BUDGET = SETTINGS.auth_rate_limit_requests
ACCOUNT_BUDGET = SETTINGS.login_attempt_limit

# Any well-formed body will do: the limiter runs as a router dependency,
# before the endpoint and before validation of what it was sent.
CREDENTIALS = {"email": "nobody@example.com", "password": "una-clave-que-no-existe"}

PASSWORD = "una-clave-suficientemente-larga"


def _phone() -> str:
    return f"+519{uuid.uuid4().int % 10**8:08d}"


async def _registered_user(api: AsyncClient, db: Session, mail: MockEmailProvider) -> str:
    """A verified account that can actually sign in.

    Registration costs one request against the very limit under test, so
    the row is created through the API and then verified directly in the
    database - driving the email link would spend the allowance these
    tests are trying to measure.
    """
    from app.models.user import User

    email = f"staff-{uuid.uuid4().hex[:12]}@example.com"
    created = await api.post(
        f"{AUTH}/register",
        json={
            "full_name": "Personal de la tienda",
            "email": email,
            "password": PASSWORD,
            "phone_e164": _phone(),
            "business_name": "Panaderia Quispe",
        },
    )
    assert created.status_code == 201, created.text

    user = db.get(User, uuid.UUID(created.json()["user_id"]))
    assert user is not None
    from datetime import UTC, datetime

    user.email_verified_at = datetime.now(UTC)
    db.commit()
    return email


@pytest.fixture
async def redis_client() -> AsyncIterator[Redis]:
    client: Redis = Redis.from_url(SETTINGS.redis_url, decode_responses=True)
    try:
        await client.ping()
    except Exception as exc:  # pragma: no cover - environment dependent
        await client.aclose()
        message = f"redis not reachable: {type(exc).__name__}: {exc}"
        if os.getenv(REQUIRE_REDIS_ENV_VAR):
            pytest.fail(f"{REQUIRE_REDIS_ENV_VAR} is set but the {message}")
        pytest.skip(message)
    try:
        yield client
    finally:
        await client.aclose()


@pytest.fixture
async def throttled(
    app: FastAPI, db: Session, mail: MockEmailProvider, redis_client: Redis
) -> AsyncIterator[AsyncClient]:
    """The `api` fixture, but with the real limiter installed.

    Same 5-per-60-seconds configuration the application runs with, in a Redis
    namespace unique to this test so parallel runs and reruns never see each
    other's counters.
    """
    provider: EmailProvider = mail
    limiter: RateLimiter = RedisRateLimiter(
        redis_client,
        limit=SETTINGS.auth_rate_limit_requests,
        window_seconds=SETTINGS.auth_rate_limit_window_seconds,
        namespace=f"test:ratelimit:auth:{uuid.uuid4().hex}",
    )
    app.dependency_overrides[get_db] = lambda: db
    app.dependency_overrides[get_email_provider] = lambda: provider
    app.dependency_overrides[get_rate_limiter] = lambda: limiter
    # A real per-account limiter as well, in its own namespace, at the
    # configured value. These tests are the only place both are live.
    login_limiter: RateLimiter = RedisRateLimiter(
        redis_client,
        limit=SETTINGS.login_attempt_limit,
        window_seconds=SETTINGS.login_attempt_window_seconds,
        namespace=f"test:ratelimit:login:{uuid.uuid4().hex}",
    )
    app.dependency_overrides[get_login_limiter] = lambda: login_limiter
    transport = ASGITransport(app=app)
    try:
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            yield client
    finally:
        app.dependency_overrides.clear()


@pytest.fixture
async def account_throttled(
    app: FastAPI, db: Session, mail: MockEmailProvider, redis_client: Redis
) -> AsyncIterator[AsyncClient]:
    """Only the per-account limiter is live; the per-IP one is switched off.

    One control per test. With both real, a case that spends the account
    budget also spends the IP budget - `ACCOUNT_BUDGET` failures plus the
    registration plus a success is already past `IP_BUDGET` - and the refusal
    it observes comes from the wrong counter. The test then passes or fails
    for reasons that have nothing to do with what it is named after.
    """
    provider: EmailProvider = mail
    login_limiter: RateLimiter = RedisRateLimiter(
        redis_client,
        limit=SETTINGS.login_attempt_limit,
        window_seconds=SETTINGS.login_attempt_window_seconds,
        namespace=f"test:ratelimit:account:{uuid.uuid4().hex}",
    )
    app.dependency_overrides[get_db] = lambda: db
    app.dependency_overrides[get_email_provider] = lambda: provider
    app.dependency_overrides[get_rate_limiter] = NullRateLimiter
    app.dependency_overrides[get_login_limiter] = lambda: login_limiter
    transport = ASGITransport(app=app)
    try:
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            yield client
    finally:
        app.dependency_overrides.clear()


class TestTheAuthLimit:
    async def test_every_attempt_inside_the_budget_is_let_through(
        self, throttled: AsyncClient
    ) -> None:
        """A different address each time, which is what this guard is for.

        Repeating one address would trip the *per-account* limiter first - it
        is deliberately the stricter of the two - and this case is about the
        per-IP budget. One source spraying many accounts is precisely the
        attack the IP counter still has to catch.
        """
        for index in range(IP_BUDGET):
            response = await throttled.post(
                f"{AUTH}/login",
                json={"email": f"nobody-{index}@example.com", "password": CREDENTIALS["password"]},
            )
            assert response.status_code == 401, response.text

    async def test_the_first_attempt_past_the_budget_is_429(self, throttled: AsyncClient) -> None:
        """The coarse per-IP guard. `contracts/api.md` documents the number."""
        for index in range(IP_BUDGET):
            await throttled.post(
                f"{AUTH}/login",
                json={"email": f"spray-{index}@example.com", "password": CREDENTIALS["password"]},
            )

        response = await throttled.post(
            f"{AUTH}/login",
            json={"email": "spray-last@example.com", "password": CREDENTIALS["password"]},
        )

        assert response.status_code == 429
        assert response.json()["error"] == "rate_limited"

    async def test_the_refusal_says_how_long_to_wait(self, throttled: AsyncClient) -> None:
        for index in range(IP_BUDGET + 1):
            response = await throttled.post(
                f"{AUTH}/login",
                json={"email": f"wait-{index}@example.com", "password": CREDENTIALS["password"]},
            )

        assert response.status_code == 429
        assert 0 < int(response.headers["retry-after"]) <= 60

    async def test_the_refusal_uses_the_documented_envelope(self, throttled: AsyncClient) -> None:
        for index in range(IP_BUDGET + 1):
            response = await throttled.post(
                f"{AUTH}/login",
                json={"email": f"shape-{index}@example.com", "password": CREDENTIALS["password"]},
            )

        assert set(response.json()) == {"error", "message"}

    async def test_every_endpoint_taking_a_guessable_secret_shares_the_budget(
        self, throttled: AsyncClient
    ) -> None:
        """Guessing throttled at `login` and free at `resend-verification`
        would not be throttled at all. One shared window, so moving between
        endpoints buys an attacker nothing."""
        for index in range(IP_BUDGET):
            await throttled.post(
                f"{AUTH}/login",
                json={"email": f"moved-{index}@example.com", "password": CREDENTIALS["password"]},
            )

        response = await throttled.post(
            f"{AUTH}/resend-verification", json={"email": "nobody@example.com"}
        )

        assert response.status_code == 429

    async def test_a_successful_request_counts_too(
        self, throttled: AsyncClient, mail: MockEmailProvider
    ) -> None:
        """Otherwise the allowance could be refilled by interleaving one
        valid request between failures."""
        for index in range(IP_BUDGET):
            await throttled.post(
                f"{AUTH}/resend-verification", json={"email": f"user-{index}@example.com"}
            )

        response = await throttled.post(f"{AUTH}/login", json=CREDENTIALS)

        assert response.status_code == 429

    async def test_the_limit_does_not_reach_beyond_the_auth_router(
        self, throttled: AsyncClient
    ) -> None:
        """The health check must stay answerable while logins are throttled -
        it is what the uptime monitor polls."""
        for _ in range(IP_BUDGET + 1):
            await throttled.post(f"{AUTH}/login", json=CREDENTIALS)

        response = await throttled.get(f"{SETTINGS.api_v1_prefix}/health")

        assert response.status_code == 200

    async def test_the_throttle_runs_before_the_password_is_checked(
        self, throttled: AsyncClient
    ) -> None:
        """A limiter that ran after Argon2 would still let an attacker burn
        CPU at will - which is a denial of service of its own."""
        for _ in range(IP_BUDGET + 1):
            response = await throttled.post(f"{AUTH}/login", json={"email": "x", "password": ""})

        # 429, not the 400 an invalid body would otherwise produce: the guard
        # is a router dependency, so it runs before the payload is validated.
        assert response.status_code == 429


class TestClientIdentification:
    """Who the counter is keyed on. Getting this wrong either throttles the
    entire platform as one caller or lets every caller pick their own key."""

    @staticmethod
    def _request(headers: dict[str, str], host: str | None = "203.0.113.10") -> object:
        from starlette.datastructures import Address, Headers

        class _Stub:
            def __init__(self) -> None:
                self.headers = Headers(headers)
                self.client = Address(host, 40000) if host else None

        return _Stub()

    def test_without_a_trusted_proxy_the_socket_address_is_used(self) -> None:
        settings = Settings(environment="test", trust_proxy_headers=False)
        request = self._request({"x-forwarded-for": "1.2.3.4"})

        assert client_identifier(request, settings) == "203.0.113.10"  # type: ignore[arg-type]

    def test_an_untrusted_forwarded_header_cannot_change_the_key(self) -> None:
        """Believing the header by default would let one attacker vary it per
        request and never be counted twice."""
        settings = Settings(environment="test", trust_proxy_headers=False)

        keys = {
            client_identifier(
                self._request({"x-forwarded-for": f"10.0.0.{n}"}),  # type: ignore[arg-type]
                settings,
            )
            for n in range(10)
        }

        assert keys == {"203.0.113.10"}

    def test_behind_a_trusted_proxy_the_forwarded_address_is_used(self) -> None:
        """Otherwise every request looks like it came from Caddy and the
        whole platform shares one allowance."""
        settings = Settings(environment="test", trust_proxy_headers=True)
        request = self._request({"x-forwarded-for": "198.51.100.42"}, host="172.18.0.2")

        assert client_identifier(request, settings) == "198.51.100.42"  # type: ignore[arg-type]

    def test_a_spoofed_prefix_is_ignored_in_favour_of_the_last_hop(self) -> None:
        """Each hop appends, so the tail is what the proxy we trust observed;
        the head is whatever the client sent before reaching us."""
        settings = Settings(environment="test", trust_proxy_headers=True)
        request = self._request({"x-forwarded-for": "1.1.1.1, 198.51.100.42"}, host="172.18.0.2")

        assert client_identifier(request, settings) == "198.51.100.42"  # type: ignore[arg-type]

    def test_a_trusted_proxy_with_no_header_falls_back_to_the_socket(self) -> None:
        settings = Settings(environment="test", trust_proxy_headers=True)

        assert client_identifier(self._request({}), settings) == "203.0.113.10"  # type: ignore[arg-type]

    def test_a_request_with_no_client_still_gets_a_key(self) -> None:
        """ASGI does not guarantee `client`; a crash here would 500 the
        endpoint the limiter is supposed to be protecting."""
        settings = Settings(environment="test")

        assert client_identifier(self._request({}, host=None), settings) == "unknown"  # type: ignore[arg-type]

    def test_the_default_is_not_to_trust_the_header(self) -> None:
        """Safe by default: a deployment opts in once a proxy is in front."""
        assert Settings(environment="test").trust_proxy_headers is False


class TestWhatTheLimitDeliberatelyDoesNotCover:
    """`refresh` and `logout` are exempt, and that is a decision, not an
    oversight.

    The budget is five requests per minute **per IP address**, and a shop is
    one IP. The owner and two assistants signing in at opening time, each with
    a couple of tabs whose access tokens expire together fifteen minutes
    later, exhaust it between them - and the failure reads as "the panel keeps
    logging me out", produced by a control meant to stop strangers guessing
    passwords.
    """

    async def test_refresh_is_not_throttled(self, throttled: AsyncClient) -> None:
        """It authenticates a 256-bit opaque token that rotates on every use,
        with family-wide revocation the moment one is replayed. Guessing is
        not the threat, and a single replay is already fatal to the session."""
        for index in range(IP_BUDGET):
            await throttled.post(
                f"{AUTH}/login",
                json={"email": f"fill-{index}@example.com", "password": CREDENTIALS["password"]},
            )

        response = await throttled.post(f"{AUTH}/refresh", json={})

        # 401 because there is no cookie here - the point is that it is not
        # 429. A signed-in customer must never be locked out of their own
        # session by somebody else on the same wifi.
        assert response.status_code == 401, response.text

    async def test_logout_is_not_throttled(self, throttled: AsyncClient) -> None:
        """A stop button that answers 429 is worse than no stop button."""
        for index in range(IP_BUDGET):
            await throttled.post(
                f"{AUTH}/login",
                json={"email": f"stop-{index}@example.com", "password": CREDENTIALS["password"]},
            )

        response = await throttled.post(f"{AUTH}/logout")

        assert response.status_code != 429, response.text

    async def test_refresh_does_not_spend_the_budget_either(self, throttled: AsyncClient) -> None:
        """Exempt from the limit means exempt from the counter. If refreshes
        still incremented it, several tabs waking up together would use up a
        real login attempt each."""
        for _ in range(IP_BUDGET):
            await throttled.post(f"{AUTH}/refresh", json={})

        response = await throttled.post(f"{AUTH}/login", json=CREDENTIALS)

        assert response.status_code == 401, response.text


class TestAShopOnOneConnection:
    """The scenario the limit has to survive: several people, one IP.

    A Peruvian small business is the whole target market, and a small business
    is one wifi router. The owner and two assistants opening the panel at
    eight in the morning are, to this limiter, a single client - and a couple
    of mistyped passwords between them is indistinguishable from an attack.

    Being locked out of your own account by a colleague's typo is not a
    security control working. It is the product breaking, for the customers
    who behave most normally.
    """

    async def test_three_people_signing_in_together_are_not_locked_out(
        self, throttled: AsyncClient, db: Session, mail: MockEmailProvider
    ) -> None:
        staff = [await _registered_user(throttled, db, mail) for _ in range(3)]

        # One of them fat-fingers their password twice before getting it right.
        # Nothing unusual, and nothing an attacker did.
        await throttled.post(
            f"{AUTH}/login", json={"email": staff[0], "password": "clave-equivocada"}
        )
        await throttled.post(
            f"{AUTH}/login", json={"email": staff[0], "password": "otra-equivocada"}
        )

        for email in staff:
            response = await throttled.post(
                f"{AUTH}/login", json={"email": email, "password": PASSWORD}
            )
            assert response.status_code == 200, (
                f"{email} refused with {response.status_code} "
                f"({response.json().get('error')}). Three colleagues on one "
                "connection must be able to sign in."
            )


class TestTheAccountLimit:
    """The counter that actually stops somebody guessing a password.

    Keyed on the account, not the connection, so a shop sharing one router is
    never treated as one attacker - and so an attacker gets the same budget
    whether they come from one address or a thousand.
    """

    async def test_it_refuses_after_the_configured_number_of_failures(
        self, account_throttled: AsyncClient, db: Session, mail: MockEmailProvider
    ) -> None:
        email = await _registered_user(account_throttled, db, mail)

        for _ in range(ACCOUNT_BUDGET):
            response = await account_throttled.post(
                f"{AUTH}/login", json={"email": email, "password": "no-es-la-clave"}
            )
            assert response.status_code == 401, response.text

        refused = await account_throttled.post(
            f"{AUTH}/login", json={"email": email, "password": "no-es-la-clave"}
        )
        assert refused.status_code == 429
        assert refused.json()["error"] == "rate_limited"

    async def test_the_right_password_still_opens_the_door_until_then(
        self, account_throttled: AsyncClient, db: Session, mail: MockEmailProvider
    ) -> None:
        email = await _registered_user(account_throttled, db, mail)
        for _ in range(ACCOUNT_BUDGET - 1):
            await account_throttled.post(f"{AUTH}/login", json={"email": email, "password": "mal"})

        response = await account_throttled.post(
            f"{AUTH}/login", json={"email": email, "password": PASSWORD}
        )
        assert response.status_code == 200, response.text

    async def test_signing_in_successfully_clears_the_failures(
        self, account_throttled: AsyncClient, db: Session, mail: MockEmailProvider
    ) -> None:
        """Otherwise today's two typos are counted against tomorrow's.

        Somebody who fumbles their password, gets it right, and comes back an
        hour later would find their budget already half spent - and the
        limiter would eventually lock out the person it was protecting.
        """
        email = await _registered_user(account_throttled, db, mail)

        for _ in range(ACCOUNT_BUDGET - 1):
            await account_throttled.post(f"{AUTH}/login", json={"email": email, "password": "mal"})

        success = await account_throttled.post(
            f"{AUTH}/login", json={"email": email, "password": PASSWORD}
        )
        assert success.status_code == 200, success.text

        # The slate is clean, so the whole budget is available again. Without
        # the reset the very next attempt would be refused.
        for attempt in range(ACCOUNT_BUDGET):
            response = await account_throttled.post(
                f"{AUTH}/login", json={"email": email, "password": "mal"}
            )
            assert response.status_code == 401, (
                f"attempt {attempt + 1} after a successful sign-in was "
                f"{response.status_code}; the failure counter was not cleared."
            )

    async def test_one_account_being_attacked_does_not_lock_out_another(
        self, account_throttled: AsyncClient, db: Session, mail: MockEmailProvider
    ) -> None:
        """The counter is per account. A colleague under attack must not cost
        you your own sign-in."""
        victim = await _registered_user(account_throttled, db, mail)
        bystander = await _registered_user(account_throttled, db, mail)

        for _ in range(ACCOUNT_BUDGET + 1):
            await account_throttled.post(f"{AUTH}/login", json={"email": victim, "password": "mal"})

        response = await account_throttled.post(
            f"{AUTH}/login", json={"email": bystander, "password": PASSWORD}
        )
        assert response.status_code == 200, response.text

    async def test_changing_the_capitalisation_does_not_reset_the_budget(
        self, account_throttled: AsyncClient, db: Session, mail: MockEmailProvider
    ) -> None:
        """`users.email` is CITEXT, so `Ana@` and `ana@` are one account. A
        counter keyed on the raw string would hand an attacker a fresh budget
        for every way of typing the same address."""
        email = await _registered_user(account_throttled, db, mail)

        for _ in range(ACCOUNT_BUDGET):
            await account_throttled.post(f"{AUTH}/login", json={"email": email, "password": "mal"})

        response = await account_throttled.post(
            f"{AUTH}/login", json={"email": email.upper(), "password": "mal"}
        )
        assert response.status_code == 429, response.text
