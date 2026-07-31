"""The 5/min/IP limit on `/auth/*`, exercised through the HTTP endpoints.

The shared `api` fixture disables throttling, because every test in the suite
shares one client address and would otherwise burn the allowance on unrelated
assertions. These tests put the real limiter back, against real Redis, keyed
into a namespace unique to each test.

The limit is on the router, not on `login`, and that is the point of half the
cases below: an attacker throttled at the login endpoint who could simply
move to `resend-verification` would not be throttled at all.
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

from app.api.deps import client_identifier, get_db, get_email_provider, get_rate_limiter
from app.core.config import Settings, get_settings
from app.core.rate_limit import RateLimiter, RedisRateLimiter
from app.providers.email.base import EmailProvider
from app.providers.email.mock import MockEmailProvider

SETTINGS = get_settings()
AUTH = f"{SETTINGS.api_v1_prefix}/auth"

REQUIRE_REDIS_ENV_VAR = "LEO_REQUIRE_REDIS"

# Any well-formed body will do: the limiter runs as a router dependency,
# before the endpoint and before validation of what it was sent.
CREDENTIALS = {"email": "nobody@example.com", "password": "una-clave-que-no-existe"}


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
    transport = ASGITransport(app=app)
    try:
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            yield client
    finally:
        app.dependency_overrides.clear()


class TestTheAuthLimit:
    async def test_the_first_five_attempts_are_let_through(self, throttled: AsyncClient) -> None:
        for _ in range(5):
            response = await throttled.post(f"{AUTH}/login", json=CREDENTIALS)
            assert response.status_code == 401, response.text

    async def test_the_sixth_attempt_in_a_minute_is_429(self, throttled: AsyncClient) -> None:
        """contracts/api.md: rate limit 5/min/IP."""
        for _ in range(5):
            await throttled.post(f"{AUTH}/login", json=CREDENTIALS)

        response = await throttled.post(f"{AUTH}/login", json=CREDENTIALS)

        assert response.status_code == 429
        assert response.json()["error"] == "rate_limited"

    async def test_the_refusal_says_how_long_to_wait(self, throttled: AsyncClient) -> None:
        for _ in range(6):
            response = await throttled.post(f"{AUTH}/login", json=CREDENTIALS)

        assert response.status_code == 429
        assert 0 < int(response.headers["retry-after"]) <= 60

    async def test_the_refusal_uses_the_documented_envelope(self, throttled: AsyncClient) -> None:
        for _ in range(6):
            response = await throttled.post(f"{AUTH}/login", json=CREDENTIALS)

        assert set(response.json()) == {"error", "message"}

    async def test_every_endpoint_taking_a_guessable_secret_shares_the_budget(
        self, throttled: AsyncClient
    ) -> None:
        """Guessing throttled at `login` and free at `resend-verification`
        would not be throttled at all. One shared window, so moving between
        endpoints buys an attacker nothing."""
        for _ in range(5):
            await throttled.post(f"{AUTH}/login", json=CREDENTIALS)

        response = await throttled.post(
            f"{AUTH}/resend-verification", json={"email": "nobody@example.com"}
        )

        assert response.status_code == 429

    async def test_a_successful_request_counts_too(
        self, throttled: AsyncClient, mail: MockEmailProvider
    ) -> None:
        """Otherwise the allowance could be refilled by interleaving one
        valid request between failures."""
        for index in range(5):
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
        for _ in range(6):
            await throttled.post(f"{AUTH}/login", json=CREDENTIALS)

        response = await throttled.get(f"{SETTINGS.api_v1_prefix}/health")

        assert response.status_code == 200

    async def test_the_throttle_runs_before_the_password_is_checked(
        self, throttled: AsyncClient
    ) -> None:
        """A limiter that ran after Argon2 would still let an attacker burn
        CPU at will - which is a denial of service of its own."""
        for _ in range(6):
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
        for _ in range(5):
            await throttled.post(f"{AUTH}/login", json=CREDENTIALS)

        response = await throttled.post(f"{AUTH}/refresh", json={})

        # 401 because there is no cookie here - the point is that it is not
        # 429. A signed-in customer must never be locked out of their own
        # session by somebody else on the same wifi.
        assert response.status_code == 401, response.text

    async def test_logout_is_not_throttled(self, throttled: AsyncClient) -> None:
        """A stop button that answers 429 is worse than no stop button."""
        for _ in range(5):
            await throttled.post(f"{AUTH}/login", json=CREDENTIALS)

        response = await throttled.post(f"{AUTH}/logout")

        assert response.status_code != 429, response.text

    async def test_refresh_does_not_spend_the_budget_either(self, throttled: AsyncClient) -> None:
        """Exempt from the limit means exempt from the counter. If refreshes
        still incremented it, several tabs waking up together would use up a
        real login attempt each."""
        for _ in range(5):
            await throttled.post(f"{AUTH}/refresh", json={})

        response = await throttled.post(f"{AUTH}/login", json=CREDENTIALS)

        assert response.status_code == 401, response.text
