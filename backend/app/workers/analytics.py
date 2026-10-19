"""Feature 013 jobs: the satisfaction survey (every 15 minutes) and the monthly report.

`csat.run`: surveys due an hour after a sale or a closed conversation are written `queued`,
committed, then handed to `ReplySender` (which checks Meta's window again); surveys nobody
answered in 48 hours expire. `reports.monthly`: daily, sends on the 1st (idempotent).
"""

from __future__ import annotations

import contextlib
import logging
from datetime import UTC, datetime
from typing import Any
from zoneinfo import ZoneInfo

from app.services.analytics import csat, monthly
from app.services.channel import NoConnectedNumber, ReplySender, SessionWindowClosed

logger = logging.getLogger(__name__)

CSAT_JOB = "csat.run"


async def run_csat(ctx: dict[str, Any]) -> dict[str, int]:
    now = datetime.now(UTC)
    outcomes = {"enviadas": 0, "omitidas": 0, "vencidas": 0}
    settings = ctx["settings"]
    with ctx["session_factory"]() as session:
        outcomes["vencidas"] = csat.expire(session, now=now)
        session.commit()
        for candidate in csat.candidates(session, now=now):
            try:
                message = csat.prepare(session, candidate, now=now)
                session.commit()
                if message is None:
                    outcomes["omitidas"] += 1
                    continue
                sender = ReplySender(
                    session,
                    candidate.tenant_id,
                    provider=ctx["whatsapp"],
                    max_attempts=settings.channel_send_max_attempts,
                )
                with contextlib.suppress(NoConnectedNumber, SessionWindowClosed):
                    await sender.send(message)
                session.commit()
                outcomes["enviadas"] += 1
            except Exception:  # noqa: BLE001 - one business never stops the rest
                session.rollback()
                logger.exception("csat failed", extra={"tenant_id": str(candidate.tenant_id)})
    if outcomes["enviadas"]:
        logger.info("csat", extra={"outcomes": outcomes})
    return outcomes


REPORT_JOB = "reports.monthly"


async def send_monthly_reports(ctx: dict[str, Any]) -> int:
    """Daily; on the 1st (Lima) the owners get last month's summary (C-108)."""
    today = datetime.now(ZoneInfo("America/Lima")).date()
    email = ctx.get("email")
    with ctx["session_factory"]() as session:
        reports = monthly.due_reports(session, today=today)
        session.commit()
    if not reports:
        return 0
    if email is None:
        logger.warning("no email provider: %d monthly report(s) not sent", len(reports))
        return 0
    sent = 0
    for tenant_id, message in reports:
        try:
            await email.send(message)
            sent += 1
        except Exception:  # noqa: BLE001 - one mailbox never stops the rest
            logger.exception("monthly report not sent", extra={"tenant_id": str(tenant_id)})
    return sent
