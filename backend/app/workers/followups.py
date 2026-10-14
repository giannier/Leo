"""`followups.run` and `orders.discard_stale` (Feature 004, TASK-444, C-13).

`followups.run`, every 15 minutes: each candidate conversation is followed up
under the same lock a turn takes, so a follow-up and an answer to a customer who
just wrote never run at once. The message is committed `queued` before it is sent,
like any reply, and `ReplySender` checks Meta's window again at send time.

`orders.discard_stale`, daily at 03:10 in Lima.
"""

from __future__ import annotations

import logging
from typing import Any

from app.agent.model_policy import chat_provider_for
from app.core.locks import acquire_lock
from app.services.channel import NoConnectedNumber, ReplySender, lock_key
from app.services.followups import FollowUps, discard_stale_drafts, due_candidates

logger = logging.getLogger(__name__)

# The names `contracts/api.md` section 9 gives the jobs.
FOLLOW_UP_JOB = "followups.run"
DISCARD_JOB = "orders.discard_stale"
LOCK_SECONDS = 600


async def run_followups(ctx: dict[str, Any]) -> dict[str, int]:
    """ARQ entry point. Returns how many conversations ended in each outcome."""
    outcomes: dict[str, int] = {}
    settings = ctx["settings"]
    with ctx["session_factory"]() as session:
        candidates = due_candidates(session)
        session.rollback()
        for tenant_id, conversation_id in candidates:
            lock = await acquire_lock(
                ctx["redis"], lock_key(conversation_id), ttl_seconds=LOCK_SECONDS
            )
            if lock is None:
                outcomes["ocupado"] = outcomes.get("ocupado", 0) + 1
                continue
            try:
                llm, _level = chat_provider_for(session, tenant_id, settings)
                result = await FollowUps(session, tenant_id, llm=llm).run(conversation_id)
                session.commit()
                if result.message is not None:
                    sender = ReplySender(
                        session,
                        tenant_id,
                        provider=ctx["whatsapp"],
                        max_attempts=settings.channel_send_max_attempts,
                    )
                    try:
                        await sender.send(result.message)
                    except NoConnectedNumber:
                        session.rollback()
                    else:
                        session.commit()
                outcomes[result.outcome] = outcomes.get(result.outcome, 0) + 1
            except Exception:  # noqa: BLE001 - one conversation never stops the rest
                session.rollback()
                logger.exception(
                    "follow-up failed",
                    extra={"tenant_id": str(tenant_id), "conversation_id": str(conversation_id)},
                )
                outcomes["error"] = outcomes.get("error", 0) + 1
            finally:
                await lock.release()
    if outcomes.get("enviado"):
        logger.info("follow-ups", extra={"outcomes": outcomes})
    return outcomes


async def discard_stale_orders(ctx: dict[str, Any]) -> int:
    """ARQ entry point."""
    with ctx["session_factory"]() as session:
        return discard_stale_drafts(session)
