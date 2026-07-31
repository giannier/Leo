"""ARQ worker registration.

Run it from the repository root with:

    uv run --directory backend arq app.workers.settings.WorkerSettings

or `make worker`, which is the same command. The API process enqueues by
job *name* (`app.services.knowledge.INDEX_JOB`), so it never imports this
module and never pulls the model or PDF libraries into a web worker.

Expensive objects - the session factory, the LLM provider, the S3 client, the
PDF reader - are built once in `startup` and handed to every job through
`ctx`. Building them per job would open a connection pool per document.
"""

from __future__ import annotations

import logging
from typing import Any

from app.core.config import get_settings
from app.core.db import get_session_factory
from app.core.queue import redis_settings
from app.providers.llm.factory import platform_provider
from app.providers.pdf.pypdf_extractor import PypdfTextExtractor
from app.providers.storage.s3_provider import S3StorageProvider
from app.workers.knowledge_indexer import index_knowledge_source

logger = logging.getLogger(__name__)


def build_storage_provider() -> S3StorageProvider:
    """The configured object store."""
    settings = get_settings()
    return S3StorageProvider(
        bucket=settings.s3_bucket,
        endpoint_url=settings.s3_endpoint_url,
        access_key=settings.s3_access_key,
        secret_key=settings.s3_secret_key,
        region=settings.s3_region,
    )


async def startup(ctx: dict[str, Any]) -> None:
    """Build the shared resources every job uses."""
    settings = get_settings()
    ctx["settings"] = settings
    ctx["session_factory"] = get_session_factory()
    ctx["storage"] = build_storage_provider()
    ctx["pdf_extractor"] = PypdfTextExtractor()
    # Embeddings are always platform-funded, never the tenant's own key -
    # see `app/models/llm_settings.py` for why that is not configurable.
    ctx["llm"] = platform_provider(settings)
    logger.info("knowledge worker ready")


async def shutdown(ctx: dict[str, Any]) -> None:
    ctx.pop("llm", None)
    ctx.pop("storage", None)
    ctx.pop("pdf_extractor", None)


class WorkerSettings:
    """The class the `arq` CLI loads."""

    functions = [index_knowledge_source]
    on_startup = startup
    on_shutdown = shutdown
    redis_settings = redis_settings()

    # One document at a time per worker process. Indexing is dominated by
    # waiting on the embedding API, but a 10 MB PDF is also held in memory
    # while it is parsed, and a single-VPS deployment (Constitution:
    # "simplicidad operativa") has no headroom for many of those at once.
    max_jobs = 4

    # A long document is several embedding round trips. The default 300s is
    # generous enough; making it explicit means a change is deliberate.
    job_timeout = 300

    # `index_source` never raises for a document it cannot handle, so a retry
    # only ever re-runs a job that died with its worker. Two attempts.
    max_tries = 2
