"""Recomputing a conversation's stage outside a turn (Feature 004, TASK-430).

The operator confirms a payment, rejects an order or changes its status while the
customer is not talking. The facts changed under the conversation (`pago_estado`),
so its stage has to be recomputed and the change recorded as an event with a reason
(`operador:confirmar`, `operador:rechazar`, `operador:estado`), exactly as a turn
does. The same two pure functions (`compute_stage`, `funnel_for`) decide.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy.orm import Session

from app.models.conversation import Conversation
from app.process.engine import compute_stage, funnel_for
from app.process.facts import FactsLoader
from app.process.service import ProcessConfigService
from app.process.state import SaveResult, StateConflict, StateRepo, facts_snapshot
from app.repositories.base import TenantScopeError

REASON_CONFIRM = "operador:confirmar"
REASON_REJECT = "operador:rechazar"
REASON_STATUS = "operador:estado"


def recompute_stage(
    session: Session,
    tenant_id: uuid.UUID,
    conversation: Conversation,
    *,
    reason: str,
    now: datetime | None = None,
    force_event: bool = False,
) -> SaveResult | None:
    """Recompute and save the stage of one conversation.

    Returns the save, or None when neither the stage nor the funnel column changed
    (nothing is written then, and no event) unless `force_event` is set: a decision
    that must stay in the history even when the stage did not move, such as
    rejecting a receipt. Retries once on a concurrent write: the bot may be
    answering at the same moment.
    """
    if conversation.tenant_id != tenant_id:
        raise TenantScopeError("the conversation belongs to another tenant")
    moment = now or datetime.now(UTC)
    config = ProcessConfigService(session).effective(tenant_id)
    states = StateRepo(session, tenant_id)
    state = states.get_or_create(conversation, config.template)
    facts = FactsLoader(session, tenant_id, config.facts_config()).load(conversation, now=moment)
    stage = compute_stage(config.template, config.options, facts, moment)
    funnel = funnel_for(config.template, stage, facts)
    if not force_event and (stage.value, funnel.value) == (state.stage_key, state.funnel_code):
        return None

    version = state.version
    for attempt in (1, 2):
        try:
            return states.save(
                conversation.id,
                stage=stage,
                funnel=funnel,
                facts_patch=None,
                reason=reason,
                expected_version=version,
                snapshot=facts_snapshot(facts),
                force_event=force_event,
            )
        except StateConflict:
            if attempt == 2:
                raise
            fresh = states.get(conversation.id)
            if fresh is None:  # pragma: no cover - the row was just read
                raise
            version = fresh.version
    raise StateConflict("unreachable")  # pragma: no cover
