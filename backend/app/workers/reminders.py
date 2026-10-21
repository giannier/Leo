"""`scheduling.reminders`: the appointment reminders, every five minutes (feature 019, phase D).

Each reminder goes under the lock of the customer's open conversation, the same one a turn takes, so
a reminder and an answer to a customer who just wrote never run at once. The free-text reminder is
committed `queued` before it is handed to the provider, like any reply.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any

from app.core.locks import acquire_lock
from app.models.conversation import Message
from app.scheduling.reminders import Due, due_reminders, lock_id_for, send_reminder
from app.services.channel import NoConnectedNumber, ReplySender, lock_key

logger = logging.getLogger(__name__)

REMINDER_JOB = "scheduling.reminders"
LOCK_SECONDS = 300


async def run_reminders(ctx: dict[str, Any]) -> dict[str, int]:
    """ARQ entry point. Returns how many reminders ended in each outcome."""
    outcomes: dict[str, int] = {}
    settings = ctx["settings"]
    now = datetime.now(UTC)
    with ctx["session_factory"]() as session:
        due: list[Due] = due_reminders(session, now)
        session.rollback()
        for item in due:
            lock = await acquire_lock(
                ctx["redis"], lock_key(lock_id_for(session, item)), ttl_seconds=LOCK_SECONDS
            )
            if lock is None:
                outcomes["ocupado"] = outcomes.get("ocupado", 0) + 1
                continue
            try:
                outcome = await send_reminder(
                    session, item, whatsapp=ctx["whatsapp"], storage=ctx.get("storage")
                )
                session.commit()
                if outcome.result == "texto" and outcome.message_id is not None:
                    message = session.get(Message, outcome.message_id)
                    if message is not None:
                        try:
                            await ReplySender(
                                session,
                                item.tenant_id,
                                provider=ctx["whatsapp"],
                                max_attempts=settings.channel_send_max_attempts,
                            ).send(message)
                        except NoConnectedNumber:
                            session.rollback()
                        else:
                            session.commit()
                outcomes[outcome.result] = outcomes.get(outcome.result, 0) + 1
            except Exception:  # noqa: BLE001 - one reminder never stops the rest
                session.rollback()
                logger.exception(
                    "reminder failed",
                    extra={
                        "tenant_id": str(item.tenant_id),
                        "appointment_id": str(item.appointment_id),
                    },
                )
                outcomes["error"] = outcomes.get("error", 0) + 1
            finally:
                await lock.release()
    if outcomes.get("texto") or outcomes.get("plantilla"):
        logger.info("appointment reminders", extra={"outcomes": outcomes})
    return outcomes
