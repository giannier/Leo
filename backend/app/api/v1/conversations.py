"""Conversations: where the sales engine has one, and giving the bot back (TASK-443).

`contracts/api.md` section 6. Any member of the business (operator): the person who
picks up a handed-over conversation is whoever is on shift, not only the owner.

* `GET /tenant/conversations/{id}/state`: stage, funnel column, the facts without
  personal data, the handover if one is running, and the last 50 events.
* `POST /tenant/conversations/{id}/resume-bot`: clears `derivado` before its pause
  ends and recomputes the stage (event `operador:reanudar`).

Another business's conversation is a 404, never a 403.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter
from pydantic import BaseModel

from app.api.deps import MemberDep, SessionDep
from app.api.errors import ApiError, error_responses
from app.models.conversation import Conversation
from app.process.facts import FactsLoader
from app.process.service import ProcessConfigService
from app.process.state import StateRepo, facts_snapshot
from app.repositories.conversation import ConversationRepository
from app.repositories.conversation_state import ConversationStageEventRepository
from app.services.handoff import resume_bot

router = APIRouter(prefix="/tenant/conversations", tags=["conversations"])

AUTH_RESPONSES = (
    (401, "`not_authenticated`, `token_expired` or `invalid_token`"),
    (403, "`email_not_verified`, `no_active_tenant` or `tenant_access_denied`"),
)
NOT_FOUND = (404, "`not_found`: the conversation does not exist or is another business's")
MAX_EVENTS = 50


class Handover(BaseModel):
    motivo: str
    resumen: str | None
    hasta: datetime


class StageEvent(BaseModel):
    de: str | None
    a: str
    embudo: str
    motivo: str
    en: datetime


class ConversationStateOut(BaseModel):
    etapa: str
    embudo: str
    hechos: dict[str, Any]
    derivado: Handover | None
    eventos: list[StageEvent]


def _conversation(
    session: SessionDep, tenant_id: uuid.UUID, conversation_id: uuid.UUID
) -> Conversation:
    conversation = ConversationRepository(session, tenant_id).get(conversation_id)
    if conversation is None:
        raise ApiError(status_code=404, error="not_found", message="Conversation not found.")
    return conversation


def _state(
    session: SessionDep, tenant_id: uuid.UUID, conversation: Conversation
) -> ConversationStateOut:
    now = datetime.now(UTC)
    config = ProcessConfigService(session).effective(tenant_id)
    state = StateRepo(session, tenant_id).get_or_create(conversation, config.template)
    facts = FactsLoader(session, tenant_id, config.facts_config()).load(conversation, now=now)
    events = ConversationStageEventRepository(session, tenant_id).for_conversation(conversation.id)
    handover = facts.derivado if facts.derivado_activo(now) else None
    return ConversationStateOut(
        etapa=state.stage_key,
        embudo=state.funnel_code,
        hechos=facts_snapshot(facts),
        derivado=Handover(**handover.model_dump()) if handover is not None else None,
        eventos=[
            StageEvent(
                de=event.from_stage,
                a=event.to_stage,
                embudo=event.to_funnel,
                motivo=event.reason,
                en=event.created_at,
            )
            for event in list(events)[-MAX_EVENTS:]
        ],
    )


@router.get(
    "/{conversation_id}/state",
    summary="Where the sales engine has a conversation",
    responses=error_responses(*AUTH_RESPONSES, NOT_FOUND),
)
def conversation_state(
    conversation_id: uuid.UUID, principal: MemberDep, session: SessionDep
) -> ConversationStateOut:
    conversation = _conversation(session, principal.tenant_id, conversation_id)
    out = _state(session, principal.tenant_id, conversation)
    session.commit()  # `get_or_create` may have written the first state
    return out


@router.post(
    "/{conversation_id}/resume-bot",
    summary="Give the conversation back to the assistant before the pause ends",
    responses=error_responses(*AUTH_RESPONSES, NOT_FOUND),
)
def resume_conversation_bot(
    conversation_id: uuid.UUID, principal: MemberDep, session: SessionDep
) -> ConversationStateOut:
    conversation = _conversation(session, principal.tenant_id, conversation_id)
    resume_bot(session, principal.tenant_id, conversation)
    session.commit()
    return _state(session, principal.tenant_id, conversation)
