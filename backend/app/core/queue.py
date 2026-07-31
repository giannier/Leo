"""Handing work to the ARQ workers.

Sits next to `core/db.py` rather than under `providers/` on purpose: like
SQLAlchemy, ARQ is our own infrastructure rather than a third-party service
we integrate with, and the codebase already keeps that kind of plumbing in
`core`. The `JobQueue` protocol is here for the same reason `get_db` exists -
so a request handler can be tested without Redis running.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from arq import create_pool
from arq.connections import ArqRedis, RedisSettings

from app.core.config import Settings, get_settings


class QueueError(RuntimeError):
    """The job could not be handed to the queue.

    Raised instead of letting a redis error escape, so callers depend on this
    module rather than on ARQ's exception hierarchy.
    """


@runtime_checkable
class JobQueue(Protocol):
    async def enqueue(self, function: str, *args: object) -> str | None:
        """Queue `function` with `args`. Returns the job id, if there is one."""
        ...


class ArqJobQueue:
    """`JobQueue` backed by a Redis pool."""

    def __init__(self, pool: ArqRedis) -> None:
        self._pool = pool

    async def enqueue(self, function: str, *args: object) -> str | None:
        try:
            job = await self._pool.enqueue_job(function, *args)
        except Exception as exc:  # noqa: BLE001 - redis raises a wide family
            raise QueueError(f"could not enqueue {function}: {exc}") from exc
        # ARQ answers `None` when a job with the same id is already queued.
        return None if job is None else job.job_id

    async def close(self) -> None:
        await self._pool.aclose()


def redis_settings(settings: Settings | None = None) -> RedisSettings:
    """ARQ connection settings from `REDIS_URL`."""
    settings = settings or get_settings()
    return RedisSettings.from_dsn(settings.redis_url)


async def create_job_queue(settings: Settings | None = None) -> ArqJobQueue:
    """Open a pool to the configured Redis."""
    try:
        pool = await create_pool(redis_settings(settings))
    except Exception as exc:  # noqa: BLE001 - connection errors vary by driver
        raise QueueError(f"could not connect to the job queue: {exc}") from exc
    return ArqJobQueue(pool)
