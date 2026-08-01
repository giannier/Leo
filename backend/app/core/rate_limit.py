"""Per-caller request throttling.

`contracts/api.md` puts a per-IP limit on `/auth/*`. That number is not
about capacity - it is what makes online password guessing and account
enumeration impractical, so the counter has to be shared by every worker
process. An in-process dictionary would give each Gunicorn/Uvicorn worker its
own allowance and quietly multiply the real limit by the worker count, which
is why the default implementation is backed by Redis.

**Fixed window, not sliding.** The window index is part of the key, so the
whole state is one integer with a TTL and one `INCR` per request. A sliding
window would need a sorted set and a read-modify-write; the extra precision
buys nothing here, because the worst a fixed window allows is a burst of
`2 * limit` across a window boundary - eleven login attempts in a minute
instead of five is still nowhere near a usable brute force.

**Fails open, loudly.** If Redis is down, requests are allowed and the
failure is logged at error level. The alternative - locking every customer
out of their account because a cache is unreachable - is a far worse outage
than a temporarily unthrottled login endpoint, and the Constitution's rule is
graceful degradation with a visible alert, never a silent failure.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Protocol

from redis.asyncio import Redis
from redis.exceptions import RedisError

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class RateLimitDecision:
    """The verdict for one request.

    `retry_after_seconds` is how long the caller must wait for the current
    window to end. It is meaningful only when `allowed` is false.
    """

    allowed: bool
    retry_after_seconds: int


class RateLimiter(Protocol):
    """What the API layer depends on. Neither Redis nor time appears here."""

    async def hit(self, key: str) -> RateLimitDecision:
        """Count one request against `key` and say whether it may proceed."""
        ...

    async def reset(self, key: str) -> None:
        """Forget everything counted against `key`.

        Exists for counters that measure *failure*. A password limiter that
        never resets punishes the person who mistyped twice and then got it
        right: their two failures sit in the window for the next quarter of an
        hour, and the next honest mistake locks them out.
        """
        ...


class NullRateLimiter:
    """Allows everything.

    Used where throttling is deliberately out of scope - most of the test
    suite, which would otherwise burn the shared per-IP allowance on
    unrelated assertions.
    """

    async def hit(self, key: str) -> RateLimitDecision:
        return RateLimitDecision(allowed=True, retry_after_seconds=0)

    async def reset(self, key: str) -> None:
        return None


class RedisRateLimiter:
    """Fixed-window counter shared by every process pointing at one Redis."""

    def __init__(
        self,
        client: Redis,
        *,
        limit: int,
        window_seconds: int,
        namespace: str = "ratelimit",
    ) -> None:
        if limit < 1:
            raise ValueError("limit must be at least 1")
        if window_seconds < 1:
            raise ValueError("window_seconds must be at least 1")
        self._client = client
        self._limit = limit
        self._window = window_seconds
        self._namespace = namespace

    @property
    def limit(self) -> int:
        return self._limit

    @property
    def window_seconds(self) -> int:
        return self._window

    async def hit(self, key: str) -> RateLimitDecision:
        now = int(time.time())
        window_index = now // self._window
        seconds_left = self._window - (now % self._window)
        redis_key = f"{self._namespace}:{key}:{window_index}"

        try:
            # One round trip. The `EXPIRE` is unconditional rather than
            # "only on the first hit": the window index is part of the key,
            # so refreshing the TTL cannot extend the window a caller is
            # being measured in - it only leaves the spent key in memory a
            # little longer.
            pipeline = self._client.pipeline()
            pipeline.incr(redis_key)
            pipeline.expire(redis_key, self._window)
            count, _ = await pipeline.execute()
        except RedisError:
            logger.error(
                "rate limiter unavailable, request allowed unthrottled",
                exc_info=True,
                extra={"rate_limit_key": key},
            )
            return RateLimitDecision(allowed=True, retry_after_seconds=0)

        if int(count) > self._limit:
            return RateLimitDecision(allowed=False, retry_after_seconds=seconds_left)
        return RateLimitDecision(allowed=True, retry_after_seconds=0)

    async def reset(self, key: str) -> None:
        """Drop the counter for `key`, in this window and the one before it.

        Both, because the window boundary is arbitrary: a failure at 09:14:58
        and a success at 09:15:01 fall either side of it, and clearing only
        the current window would leave that failure to be counted against the
        next honest mistake.

        A Redis outage is swallowed. Failing to clear a counter can at worst
        delay somebody by one window; failing their *successful* login because
        the bookkeeping did not work would be far worse.
        """
        now = int(time.time())
        current = now // self._window
        keys = [f"{self._namespace}:{key}:{index}" for index in (current, current - 1)]
        try:
            await self._client.delete(*keys)
        except RedisError:
            logger.warning(
                "could not clear rate limit counter", exc_info=True, extra={"rate_limit_key": key}
            )
