"""`media.refresh_provider_ids`: keep the library's uploaded copies alive.

The provider forgets an uploaded file after 30 days. Without this job the
first customer to ask for a price list after that is sent a one-hour link
instead of the file (a fallback, not a failure, but a worse message). Once a
day it uploads again whatever lapses in the next 48 hours.
"""

from __future__ import annotations

import logging
from typing import Any

from app.services.media_delivery import refresh_provider_ids

logger = logging.getLogger(__name__)

# The name `contracts/api.md` section 9 gives the job.
REFRESH_JOB = "media.refresh_provider_ids"


async def refresh_media_provider_ids(ctx: dict[str, Any]) -> str:
    """ARQ entry point. Scheduled in `app/workers/settings.py`."""
    session_factory = ctx["session_factory"]
    with session_factory() as session:
        outcome = await refresh_provider_ids(
            session, provider=ctx["whatsapp"], storage=ctx["storage"]
        )
    logger.info("media refresh", extra={"outcome": str(outcome)})
    return str(outcome)
