"""`handoff.expire`: give the bot back when a handover's pause runs out (TASK-443)."""

from __future__ import annotations

import logging
from typing import Any

from app.services.handoff import expire_handoffs

logger = logging.getLogger(__name__)

# The name `contracts/api.md` section 9 gives the job.
EXPIRE_JOB = "handoff.expire"


async def expire_handoff_pauses(ctx: dict[str, Any]) -> int:
    """ARQ entry point. Scheduled in `app/workers/settings.py`, every five minutes."""
    with ctx["session_factory"]() as session:
        done = expire_handoffs(session)
    if done:
        logger.info("handoff pauses expired", extra={"count": done})
    return done
