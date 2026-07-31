"""Redis connection for the running application.

The mirror of `core/db.py`: one lazily built, process-wide client that
everything sharing this process reuses. Redis is our own infrastructure -
like Postgres - not a third-party *provider*, so it lives in `core/` rather
than behind a `providers/` adapter. What is behind an interface is the
*behaviour* built on it: see `core/rate_limit.py`, whose consumers depend on
the `RateLimiter` protocol and never on this module.

Asynchronous on purpose. The endpoints are `async def`, so a blocking round
trip here would stall the event loop for every other in-flight request.
"""

from __future__ import annotations

from functools import lru_cache

from redis.asyncio import Redis

from app.core.config import get_settings


@lru_cache
def get_redis_client() -> Redis:
    """The process-wide Redis client.

    `redis.asyncio.Redis.from_url` builds a connection pool rather than a
    socket, so nothing is opened until the first command; importing this
    module on a machine with no Redis is harmless.

    `decode_responses=True` so callers get `str` back instead of `bytes`.
    Everything we keep in Redis is text or a counter.
    """
    # `from_url` is annotated as returning `Any`; the local annotation is what
    # pins it back down for the type checker, as in `core/security.py`.
    client: Redis = Redis.from_url(get_settings().redis_url, decode_responses=True)
    return client
