"""`inbox.auto_return` and `inbox.unattended` (Feature 007, TASK-703 and TASK-705)."""

from __future__ import annotations

import logging
from typing import Any

from app.services.inbox_jobs import notify_unattended, return_idle_takeovers

logger = logging.getLogger(__name__)

AUTO_RETURN_JOB = "inbox.auto_return"
UNATTENDED_JOB = "inbox.unattended"


async def auto_return_takeovers(ctx: dict[str, Any]) -> int:
    with ctx["session_factory"]() as session:
        return return_idle_takeovers(session)


async def email_unattended_handovers(ctx: dict[str, Any]) -> int:
    with ctx["session_factory"]() as session:
        emails = notify_unattended(session)
    provider = ctx.get("email")
    for message in emails:
        if provider is None:
            break
        try:
            await provider.send(message)
        except Exception:  # noqa: BLE001 - a lost email never stops the job
            logger.exception("unattended email failed")
    return len(emails)
