"""The end of a handover: the pause runs out, or a person gives the bot back (TASK-443).

`derivar_a_humano` writes `derivado = {motivo, resumen, hasta}` and the turn
pipeline stays quiet until `hasta` (C-14). Two ways out, and both clear the fact and
recompute the stage with an event, so the history says who gave the bot back:

* `expire_handoffs`, every five minutes (`handoff.expire`): `pausa_vencida`.
* `resume_bot`, from the panel (`POST /tenant/conversations/{id}/resume-bot`):
  `operador:reanudar`.

Neither sends anything: the bot waits for the customer to write
(`plantillas.md` section 7). `asesoria_derivada_en` stays, so ASESORIA does not
hand the customer over again on its own.
"""

from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime

from sqlalchemy import DateTime, cast, select
from sqlalchemy.orm import Session

from app.models.conversation import Conversation
from app.models.conversation_state import ConversationState
from app.process.recompute import recompute_stage
from app.process.state import SaveResult
from app.repositories.conversation import ConversationRepository

logger = logging.getLogger(__name__)

REASON_RESUME = "operador:reanudar"
REASON_PAUSE_OVER = "pausa_vencida"
_BATCH = 200


def resume_bot(
    session: Session,
    tenant_id: uuid.UUID,
    conversation: Conversation,
    *,
    now: datetime | None = None,
) -> SaveResult | None:
    """A person hands the conversation back before the pause ends."""
    return recompute_stage(
        session,
        tenant_id,
        conversation,
        reason=REASON_RESUME,
        now=now,
        force_event=True,
        facts_patch={"derivado": None},
    )


def expired_handoffs(
    session: Session, *, now: datetime, limit: int = _BATCH
) -> list[tuple[uuid.UUID, uuid.UUID]]:
    """(tenant, conversation) of the pauses that ran out.

    Platform-wide, like `stranded_replies`: the caller re-scopes to each tenant
    before touching anything.
    """
    hasta = cast(ConversationState.facts["derivado"]["hasta"].astext, DateTime(timezone=True))
    statement = (
        select(ConversationState.tenant_id, ConversationState.conversation_id)
        .where(ConversationState.facts.has_key("derivado"), hasta <= now)
        .order_by(hasta)
        .limit(limit)
    )
    return [(row[0], row[1]) for row in session.execute(statement).all()]


def expire_handoffs(session: Session, *, now: datetime | None = None) -> int:
    """Clear every pause that ran out; one commit per conversation."""
    moment = now or datetime.now(UTC)
    done = 0
    for tenant_id, conversation_id in expired_handoffs(session, now=moment):
        conversation = ConversationRepository(session, tenant_id).get(conversation_id)
        if conversation is None:  # pragma: no cover - the state points at it
            continue
        try:
            recompute_stage(
                session,
                tenant_id,
                conversation,
                reason=REASON_PAUSE_OVER,
                now=moment,
                force_event=True,
                facts_patch={"derivado": None},
            )
            session.commit()
            done += 1
        except Exception:  # noqa: BLE001 - one conversation never stops the rest
            session.rollback()
            logger.exception(
                "handoff expiry failed",
                extra={"tenant_id": str(tenant_id), "conversation_id": str(conversation_id)},
            )
    return done
