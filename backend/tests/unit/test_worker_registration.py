"""The API enqueues by name; the worker registers by function.

If those two ever drift, nothing breaks loudly: uploads keep answering `202`
and every source sits `pending` forever. That is exactly the silent failure
the Constitution forbids, so the name is pinned here.
"""

from __future__ import annotations

from arq.worker import Worker

from app.core.config import get_settings
from app.services.catalog_evals import EVALS_JOB
from app.services.catalog_knowledge import INDEX_CATALOG_JOB
from app.services.knowledge import INDEX_JOB
from app.services.situations import EMBED_JOB
from app.workers.channel import ANSWER_JOB, PROCESS_EVENT_JOB
from app.workers.settings import WorkerSettings

# Every name anything enqueues by - the API, or a worker booking follow-up
# work for itself. Adding a job means adding it here, which is the point: the
# list is the contract between processes that never import each other.
# INDEX_CATALOG_JOB: the platform panel, when it publishes a rubro version.
ENQUEUED_BY_THE_API = {
    INDEX_JOB,
    PROCESS_EVENT_JOB,
    ANSWER_JOB,
    INDEX_CATALOG_JOB,
    EMBED_JOB,
    EVALS_JOB,
}


def _registered_names() -> set[str]:
    """Job names the worker answers to, excluding the ones it schedules itself.

    ARQ registers cron jobs in the same table under a `cron:` prefix. Those
    are triggered by the clock, not enqueued by anyone, so they are not part
    of the contract between the two processes.
    """
    settings = {
        key: value for key, value in vars(WorkerSettings).items() if not key.startswith("__")
    }
    return {name for name in Worker(**settings).functions if not name.startswith("cron:")}


class TestEveryEnqueuedJobIsReachable:
    def test_the_names_the_api_enqueues_are_the_names_the_worker_registers(self) -> None:
        assert _registered_names() >= ENQUEUED_BY_THE_API

    def test_the_worker_registers_nothing_nobody_asks_for(self) -> None:
        """A registered function nothing enqueues is either dead code or a
        name that drifted, and both look identical from here."""
        assert _registered_names() == ENQUEUED_BY_THE_API


class TestWorkerConnection:
    def test_it_points_at_the_configured_redis(self) -> None:
        """A worker on a different Redis than the API drains nothing."""
        redis = WorkerSettings.redis_settings
        assert f"redis://{redis.host}:{redis.port}/{redis.database}" == get_settings().redis_url


class TestWorkerBounds:
    def test_retries_are_bounded(self) -> None:
        """`index_source` never raises for a bad document, so retries are
        only for a worker that died mid-job. Unbounded tries would re-embed
        the same document on the tenant's bill."""
        assert WorkerSettings.max_tries == 2

    def test_concurrency_and_timeout_are_explicit(self) -> None:
        assert WorkerSettings.max_jobs > 0
        assert WorkerSettings.job_timeout >= 60
