"""Tests for the throttle behind the 5/min/IP rule on `/auth/*`.

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

    def test_the_configured_default_is_the_contracted_five_per_minute(self) -> None:
        """contracts/api.md: rate limit 5/min/IP on login."""
        settings = get_settings()
        assert settings.auth_rate_limit_requests == 5
        assert settings.auth_rate_limit_window_seconds == 60


class TestNullRateLimiter:
    async def test_it_allows_everything(self) -> None:
        limiter = NullRateLimiter()
        for _ in range(100):
            assert (await limiter.hit("any-key")).allowed is True
