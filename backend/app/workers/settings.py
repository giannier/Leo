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

from arq import cron

from app.core.config import get_settings
from app.core.db import get_session_factory
from app.core.queue import redis_settings
from app.providers.email.factory import build_email_provider
from app.providers.llm.factory import platform_provider
from app.providers.pdf.pypdf_extractor import PypdfTextExtractor
from app.providers.storage.s3_provider import S3StorageProvider
from app.providers.whatsapp.ycloud import YCloudProvider
from app.workers.channel import process_webhook_event
from app.workers.knowledge_indexer import index_knowledge_source
from app.workers.trial_lifecycle import (
    tenant_archiver,
    trial_enforcer,
    trial_notifier,
    wizard_reminder,
)

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
    # The lifecycle jobs write to customers. `None` when no provider is
    # configured, which the jobs treat as "do the state change, skip the
    # email" rather than as a reason to fail.
    ctx["email"] = build_email_provider(settings)
    # The channel adapter. Stateless - credentials travel with each call - so
    # one instance serves every tenant.
    ctx["whatsapp"] = YCloudProvider(base_url=settings.ycloud_base_url)
    logger.info("worker ready")


async def shutdown(ctx: dict[str, Any]) -> None:
    ctx.pop("llm", None)
    ctx.pop("storage", None)
    ctx.pop("pdf_extractor", None)
    ctx.pop("email", None)
    ctx.pop("whatsapp", None)


class WorkerSettings:
    """The class the `arq` CLI loads."""

    functions = [index_knowledge_source, process_webhook_event]

    # Times are UTC and deliberately off the hour: everything on a VPS
    # that fires at :00 competes with everything else that fires at :00.
    #
    # 11:00 UTC is 06:00 in Lima - the notices land before the business
    # day rather than in the middle of the night, which is when a customer
    # can actually act on "your trial ends tomorrow".
    cron_jobs = [
        cron(trial_notifier, hour=11, minute=5),
        # Hourly: the quota can run out at any moment, and a tenant that
        # spent its last message at 09:05 should not keep answering free
        # until midnight.
        cron(trial_enforcer, minute=20),
        cron(tenant_archiver, hour=11, minute=35),
        cron(wizard_reminder, hour=14, minute=50),
    ]
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
