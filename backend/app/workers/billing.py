"""`billing.calendar` (Feature 009, TASK-905): renewals, period ends and dunning, hourly."""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any

from app.services.billing.jobs import close_periods, dun, issue_renewals

logger = logging.getLogger(__name__)

CALENDAR_JOB = "billing.calendar"


async def billing_calendar(ctx: dict[str, Any]) -> int:
    """One pass of the billing calendar; returns how many emails went out."""
    now = datetime.now(UTC)
    with ctx["session_factory"]() as session:
        emails = issue_renewals(session, now=now)
        emails += close_periods(session, now=now)
        emails += dun(session, now=now)
        session.commit()
    provider = ctx.get("email")
    sent = 0
    for message in emails:
        if provider is None:
            break
        try:
            await provider.send(message)
            sent += 1
        except Exception:  # noqa: BLE001 - a lost email never undoes the billing change
            logger.exception("billing email failed")
    return sent
