"""Tests for the throttle behind the rate limits on `/auth/*`.

`RedisRateLimiter` is exercised against a real Redis, not a fake. The whole
reason it is not an in-process dictionary is that the counter has to be
shared between worker processes, and a fake would prove nothing about that -
one of these tests points two independent limiter instances at the same key
precisely to show they see one counter.

Every test namespaces its keys with a fresh UUID, so runs never collide and
nothing has to be cleaned up: the keys carry a TTL and expire on their own.
"""

from __future__ import annotations

import os
import uuid
from collections.abc import AsyncIterator
from typing import cast

import pytest
from redis.asyncio import Redis
from redis.exceptions import ConnectionError as RedisConnectionError

from app.core.config import get_settings
from app.core.rate_limit import NullRateLimiter, RedisRateLimiter

# Mirrors LEO_REQUIRE_DB: skipping locally when the stack is down is a
# convenience, but in CI an unreachable Redis must fail rather than silently
# remove the rate-limit tests from the run.
REQUIRE_REDIS_ENV_VAR = "LEO_REQUIRE_REDIS"


@pytest.fixture
async def redis_client() -> AsyncIterator[Redis]:
    client: Redis = Redis.from_url(get_settings().redis_url, decode_responses=True)
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
def namespace() -> str:
    """A key prefix unique to this test, so runs never interfere."""
    return f"test:ratelimit:{uuid.uuid4().hex}"


class TestTheLimit:
    async def test_requests_up_to_the_limit_are_allowed(
        self, redis_client: Redis, namespace: str
    ) -> None:
        limiter = RedisRateLimiter(redis_client, limit=5, window_seconds=60, namespace=namespace)

        for _ in range(5):
            assert (await limiter.hit("203.0.113.7")).allowed is True

    async def test_the_request_after_the_limit_is_refused(
        self, redis_client: Redis, namespace: str
    ) -> None:
        """Five per minute means the sixth is the one that fails."""
        limiter = RedisRateLimiter(redis_client, limit=5, window_seconds=60, namespace=namespace)
        for _ in range(5):
            await limiter.hit("203.0.113.7")

        decision = await limiter.hit("203.0.113.7")

        assert decision.allowed is False
        assert 0 < decision.retry_after_seconds <= 60

    async def test_each_caller_has_their_own_allowance(
        self, redis_client: Redis, namespace: str
    ) -> None:
        """One IP exhausting the limit must not lock out everyone else."""
        limiter = RedisRateLimiter(redis_client, limit=2, window_seconds=60, namespace=namespace)
        await limiter.hit("198.51.100.1")
        await limiter.hit("198.51.100.1")
        assert (await limiter.hit("198.51.100.1")).allowed is False

        assert (await limiter.hit("198.51.100.2")).allowed is True

    async def test_the_counter_is_shared_between_processes(
        self, redis_client: Redis, namespace: str
    ) -> None:
        """The reason this is not an in-process dictionary.

        Two limiter objects stand in for two Uvicorn workers. If the state
        lived in the process, each would grant the full allowance and the
        real limit would silently be `limit * workers`.
        """
        worker_one = RedisRateLimiter(redis_client, limit=2, window_seconds=60, namespace=namespace)
        worker_two = RedisRateLimiter(redis_client, limit=2, window_seconds=60, namespace=namespace)

        assert (await worker_one.hit("192.0.2.9")).allowed is True
        assert (await worker_two.hit("192.0.2.9")).allowed is True
        assert (await worker_one.hit("192.0.2.9")).allowed is False

    async def test_the_window_expires(self, redis_client: Redis, namespace: str) -> None:
        """A one-second window, so the reset is observable without sleeping
        through a real minute."""
        limiter = RedisRateLimiter(redis_client, limit=1, window_seconds=1, namespace=namespace)
        key = "198.51.100.55"
        assert (await limiter.hit(key)).allowed is True
        assert (await limiter.hit(key)).allowed is False

        # Rather than sleeping, assert the property that makes the window
        # expire: the window index is part of the Redis key, so the next
        # window is a different counter starting at zero.
        stored = await redis_client.keys(f"{namespace}:{key}:*")
        assert len(stored) == 1
        assert (await redis_client.ttl(stored[0])) <= 1

    async def test_the_key_carries_a_ttl_so_nothing_accumulates(
        self, redis_client: Redis, namespace: str
    ) -> None:
        limiter = RedisRateLimiter(redis_client, limit=5, window_seconds=60, namespace=namespace)
        await limiter.hit("203.0.113.99")

        keys = await redis_client.keys(f"{namespace}:*")
        assert len(keys) == 1
        assert 0 < await redis_client.ttl(keys[0]) <= 60


class _FailingPipeline:
    """A pipeline whose `execute()` raises whatever it was handed."""

    def __init__(self, error: Exception) -> None:
        self._error = error

    def incr(self, key: str) -> None:
        return None

    def expire(self, key: str, seconds: int) -> None:
        return None

    async def execute(self) -> object:
        raise self._error


class _FailingRedis:
    """Stand-in for a Redis that answers every command with a failure."""

    def __init__(self, error: Exception) -> None:
        self._error = error

    def pipeline(self) -> _FailingPipeline:
        return _FailingPipeline(self._error)


class TestDegradation:
    async def test_an_unreachable_redis_allows_the_request(self, namespace: str) -> None:
        """Fails open, on purpose, against a genuinely dead address.

        Locking every customer out of their account because a cache is down
        is a far worse outage than a temporarily unthrottled login endpoint.
        The Constitution's rule is graceful degradation with a visible alert,
        which is the error-level log line the next test asserts.

        Port 1 is reserved and never listening. Which `RedisError` subclass
        that produces differs by platform - refused on Linux, timed out on
        Windows - which is exactly why the limiter catches the base class.
        """
        broken: Redis = Redis.from_url("redis://127.0.0.1:1/0", socket_connect_timeout=0.25)
        limiter = RedisRateLimiter(broken, limit=1, window_seconds=60, namespace=namespace)

        decision = await limiter.hit("203.0.113.1")

        assert decision.allowed is True
        await broken.aclose()

    async def test_the_degradation_is_logged_not_silent(
        self, namespace: str, caplog: pytest.LogCaptureFixture
    ) -> None:
        limiter = RedisRateLimiter(
            cast("Redis", _FailingRedis(RedisConnectionError("connection refused"))),
            limit=1,
            window_seconds=60,
            namespace=namespace,
        )

        with caplog.at_level("ERROR", logger="app.core.rate_limit"):
            decision = await limiter.hit("203.0.113.1")

        assert decision.allowed is True
        assert any("rate limiter unavailable" in record.message for record in caplog.records)

    async def test_a_failure_that_is_not_redis_is_not_swallowed(self, namespace: str) -> None:
        """Guard against the `except RedisError` widening into `Exception`.

        Failing open is a deliberate answer to one specific problem - the
        cache is unreachable. A bug in our own code reaching this line must
        still be a 500, not a silently unthrottled endpoint.
        """
        limiter = RedisRateLimiter(
            cast("Redis", _FailingRedis(ValueError("a bug, not an outage"))),
            limit=1,
            window_seconds=60,
            namespace=namespace,
        )

        with pytest.raises(ValueError, match="a bug"):
            await limiter.hit("203.0.113.1")


class TestTheDegradationLogNamesNobody:
    """Ley 29733: an outage must not turn the log into a customer list.

    These keys are not opaque. The per-account login limiter is keyed on the
    email address on purpose - that is what makes it immune to a whole shop
    sharing one connection - and the per-IP limiter is keyed on the address.
    Both lines below fire exactly when Redis is down, which is to say in
    bursts, into whatever aggregator production ships logs to.
    """

    @staticmethod
    def _emitted(caplog: pytest.LogCaptureFixture) -> str:
        """Everything the handler would render: message and structured extras."""
        return " ".join(f"{record.getMessage()} {record.__dict__}" for record in caplog.records)

    async def test_the_hit_path_does_not_log_the_email_it_is_keyed_on(
        self, namespace: str, caplog: pytest.LogCaptureFixture
    ) -> None:
        limiter = RedisRateLimiter(
            cast("Redis", _FailingRedis(RedisConnectionError("connection refused"))),
            limit=1,
            window_seconds=60,
            namespace=namespace,
        )

        with caplog.at_level("ERROR", logger="app.core.rate_limit"):
            await limiter.hit("ana.quispe@panaderia.pe")

        emitted = self._emitted(caplog)
        assert "ana.quispe@panaderia.pe" not in emitted
        # Still correlatable: an operator has to be able to tell one caller
        # hammering the endpoint from a hundred different ones.
        assert "caller_ref" in emitted
        assert namespace in emitted

    async def test_the_reset_path_does_not_either(
        self, namespace: str, caplog: pytest.LogCaptureFixture
    ) -> None:
        class _FailingDelete(_FailingRedis):
            async def delete(self, *keys: str) -> int:
                raise self._error

        limiter = RedisRateLimiter(
            cast("Redis", _FailingDelete(RedisConnectionError("connection refused"))),
            limit=1,
            window_seconds=60,
            namespace=namespace,
        )

        with caplog.at_level("WARNING", logger="app.core.rate_limit"):
            await limiter.reset("ana.quispe@panaderia.pe")

        assert "ana.quispe@panaderia.pe" not in self._emitted(caplog)

    async def test_the_same_caller_always_gets_the_same_reference(
        self, namespace: str, caplog: pytest.LogCaptureFixture
    ) -> None:
        """A reference that changed per call would be useless for correlation,
        which is the only reason it is logged at all."""
        limiter = RedisRateLimiter(
            cast("Redis", _FailingRedis(RedisConnectionError("connection refused"))),
            limit=1,
            window_seconds=60,
            namespace=namespace,
        )

        with caplog.at_level("ERROR", logger="app.core.rate_limit"):
            await limiter.hit("ana.quispe@panaderia.pe")
            await limiter.hit("ana.quispe@panaderia.pe")
            await limiter.hit("otro@panaderia.pe")

        references = [record.__dict__["caller_ref"] for record in caplog.records]
        assert references[0] == references[1] != references[2]


class TestConfiguration:
    @pytest.mark.parametrize(("limit", "window"), [(0, 60), (-1, 60), (5, 0), (5, -1)])
    def test_a_nonsensical_configuration_is_refused_at_construction(
        self, limit: int, window: int
    ) -> None:
        """A limit of zero would refuse every request including the first."""
        with pytest.raises(ValueError):
            RedisRateLimiter(
                Redis.from_url("redis://127.0.0.1:6379/0"),
                limit=limit,
                window_seconds=window,
            )

    def test_the_configuration_matches_the_published_contract(self) -> None:
        """Reads the numbers out of `contracts/api.md` rather than repeating them.

        This test used to assert `== 5`, and when the limit was raised it
        failed for the wrong reason: it was pinning a literal, not checking
        that the code and the document agreed. Parsing the contract means the
        two can only ever drift apart deliberately - change one without the
        other and this goes red.
        """
        import re
        from pathlib import Path

        contract = (
            Path(__file__).resolve().parents[3]
            / "leo-sdd"
            / "features"
            / "001-onboarding"
            / "contracts"
            / "api.md"
        ).read_text(encoding="utf-8")

        documented = {
            name: int(value)
            for name, value in re.findall(
                r"`(AUTH_RATE_LIMIT_REQUESTS|AUTH_RATE_LIMIT_WINDOW_SECONDS|"
                r"LOGIN_ATTEMPT_LIMIT|LOGIN_ATTEMPT_WINDOW_SECONDS)=(\d+)`",
                contract,
            )
        }
        assert len(documented) == 4, f"contract does not name all four limits: {documented}"

        settings = get_settings()
        assert settings.auth_rate_limit_requests == documented["AUTH_RATE_LIMIT_REQUESTS"]
        assert (
            settings.auth_rate_limit_window_seconds == documented["AUTH_RATE_LIMIT_WINDOW_SECONDS"]
        )
        assert settings.login_attempt_limit == documented["LOGIN_ATTEMPT_LIMIT"]
        assert settings.login_attempt_window_seconds == documented["LOGIN_ATTEMPT_WINDOW_SECONDS"]

    def test_the_other_two_limits_match_the_contract_as_well(self) -> None:
        """The sandbox and OTP budgets, which the contract states in prose.

        They are named nowhere near the `/auth` block and were the two nobody
        was checking, so a change to either could drift from the document
        without a single test noticing.
        """
        import re
        from pathlib import Path

        contract = (
            Path(__file__).resolve().parents[3]
            / "leo-sdd"
            / "features"
            / "001-onboarding"
            / "contracts"
            / "api.md"
        ).read_text(encoding="utf-8")

        otp_per_hour = re.search(r"Rate limit (\d+)/h", contract)
        sandbox_per_minute = re.search(r"`429 rate_limited` \((\d+)/min\)", contract)
        assert otp_per_hour is not None, "the contract no longer states the OTP limit"
        assert sandbox_per_minute is not None, "the contract no longer states the sandbox limit"

        settings = get_settings()
        assert settings.otp_request_limit == int(otp_per_hour.group(1))
        assert settings.otp_request_window_seconds == 3600
        assert settings.sandbox_rate_limit_requests == int(sandbox_per_minute.group(1))
        assert settings.sandbox_rate_limit_window_seconds == 60


class TestNullRateLimiter:
    async def test_it_allows_everything(self) -> None:
        limiter = NullRateLimiter()
        for _ in range(100):
            assert (await limiter.hit("any-key")).allowed is True
