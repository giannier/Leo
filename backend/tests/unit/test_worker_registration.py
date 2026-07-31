"""The API enqueues by name; the worker registers by function.

If those two ever drift, nothing breaks loudly: uploads keep answering `202`
and every source sits `pending` forever. That is exactly the silent failure
the Constitution forbids, so the name is pinned here.
"""

from __future__ import annotations

from arq.worker import Worker

from app.core.config import get_settings
from app.services.knowledge import INDEX_JOB
from app.workers.settings import WorkerSettings


def _registered_names() -> set[str]:
    settings = {
        key: value for key, value in vars(WorkerSettings).items() if not key.startswith("__")
    }
    return set(Worker(**settings).functions)


class TestTheIndexingJobIsReachable:
    def test_the_name_the_api_enqueues_is_the_name_the_worker_registers(self) -> None:
        assert INDEX_JOB in _registered_names()

    def test_the_worker_registers_the_indexer(self) -> None:
        assert [f.__name__ for f in WorkerSettings.functions] == ["index_knowledge_source"]


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
