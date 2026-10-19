"""`campaigns.send`, every minute (Feature 011): one batch per campaign that is due.

The live conversation has its own jobs and never waits for this one (C-106): a campaign
sends at most 30 templates a minute and stops at the number's daily cap.
"""

from __future__ import annotations

import logging
from typing import Any

from app.services.campaigns.service import CampaignRunner

logger = logging.getLogger(__name__)

SEND_JOB = "campaigns.send"


async def send_campaigns(ctx: dict[str, Any]) -> int:
    """ARQ entry point. Returns how many templates went out."""
    with ctx["session_factory"]() as session:
        try:
            sent = await CampaignRunner(
                session, provider=ctx["whatsapp"], storage=ctx.get("storage")
            ).tick()
        except Exception:  # noqa: BLE001 - next minute tries again
            session.rollback()
            logger.exception("campaign batch failed")
            return 0
    if sent:
        logger.info("campaign templates sent", extra={"sent": sent})
    return sent
