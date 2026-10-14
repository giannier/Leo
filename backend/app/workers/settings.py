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
from app.workers.catalog_evals import run_catalog_evals
from app.workers.catalog_indexer import index_catalog_knowledge
from app.workers.channel import (
    answer_conversation,
    process_webhook_event,
    retry_stranded_replies,
)
from app.workers.followups import (
    DISCARD_JOB,
    FOLLOW_UP_JOB,
    discard_stale_orders,
    run_followups,
)
from app.workers.handoff import EXPIRE_JOB, expire_handoff_pauses
from app.workers.knowledge_indexer import index_knowledge_source
from app.workers.media import REFRESH_JOB, refresh_media_provider_ids
from app.workers.situations import embed_situations
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
        public_endpoint_url=settings.s3_public_endpoint_url,
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
    try:
        # The rubro knowledge must exist before the first question about it.
        await index_catalog_knowledge(ctx)
    except Exception:  # noqa: BLE001 - a missing key must not stop the worker
        logger.warning("could not index the catalogue knowledge at startup")
    logger.info("worker ready")


async def shutdown(ctx: dict[str, Any]) -> None:
    ctx.pop("llm", None)
    ctx.pop("storage", None)
    ctx.pop("pdf_extractor", None)
    ctx.pop("email", None)
    ctx.pop("whatsapp", None)


class WorkerSettings:
    """The class the `arq` CLI loads."""

    functions = [
        index_knowledge_source,
        process_webhook_event,
        answer_conversation,
        index_catalog_knowledge,
        embed_situations,
        run_catalog_evals,
    ]

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
        # Every five minutes, off the boundary. A reply that was written down
        # and never sent is a customer being ignored, and nothing else in the
        # system would ever notice: the job that should have sent it died.
        cron(retry_stranded_replies, minute={2, 7, 12, 17, 22, 27, 32, 37, 42, 47, 52, 57}),
        # Daily, in the small hours of Lima (08:25 UTC is 03:25). A file the
        # provider still holds for 48 hours has time to be uploaded again
        # before it lapses, even if one run fails.
        cron(refresh_media_provider_ids, name=f"cron:{REFRESH_JOB}", hour=8, minute=25),
        cron(index_catalog_knowledge, name="cron:index_catalog_knowledge", minute=40),
        cron(embed_situations, name="cron:embed_situations", minute={10, 40}),
        # Every five minutes (`contracts/api.md` section 9): a pause that ran out
        # gives the bot back within five minutes of its `hasta`.
        # C-13: every 15 minutes, off the quarter hours.
        cron(run_followups, name=f"cron:{FOLLOW_UP_JOB}", minute={4, 19, 34, 49}),
        # 08:10 UTC is 03:10 in Lima (`contracts/api.md` section 9).
        cron(discard_stale_orders, name=f"cron:{DISCARD_JOB}", hour=8, minute=10),
        cron(
            expire_handoff_pauses,
            name=f"cron:{EXPIRE_JOB}",
            minute={1, 6, 11, 16, 21, 26, 31, 36, 41, 46, 51, 56},
        ),
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
