"""Sandbox endpoints: converse with your own agent (TASK-015).

The `/tenant/sandbox` section of `contracts/api.md`. Any member may use it,
operators included - the sandbox is where somebody learns what the agent will
say before it says it to a customer.

Free and unmetered by design (Clarification 002). The guard is a per-tenant
rate limit rather than a quota, because a limit throttles abuse without ever
telling a paying customer they have run out of the thing they are evaluating.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Query, status
from pydantic import BaseModel, StringConstraints

from app.api.deps import MemberDep, PlatformLLMDep, SandboxLimiterDep, SessionDep
from app.api.errors import ApiError, error_responses
from app.services.sandbox import ConversationNotFound, SandboxNotReady, SandboxService

router = APIRouter(prefix="/tenant/sandbox", tags=["sandbox"])

# WhatsApp's own limit is 4096; matching it means the sandbox cannot accept a
# message the real channel would reject.
MessageText = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1, max_length=4096)
]

AUTH_RESPONSES = (
    (401, "`not_authenticated`, `token_expired` or `invalid_token`"),
    (403, "`email_not_verified`, `no_active_tenant` or `tenant_access_denied`"),
)


class StartConversationResponse(BaseModel):
    conversation_id: uuid.UUID


class SendMessageRequest(BaseModel):
    content: MessageText


class TraceResponse(BaseModel):
    model: str
    tools_called: list[dict[str, object]]
    rag_sources: list[dict[str, object]]
    input_tokens: int
    output_tokens: int
    latency_ms: int
    # True when the answer is the canned fallback rather than the model's.
    # Surfaced so the panel can say "something went wrong" instead of
    # presenting an apology as if the agent chose to write it.
    degraded: bool
    # Present only when the agent named a product the catalogue does not have
    # and the post-guard stepped in. Null on an ordinary turn, so the owner
    # sees it exactly when there is something to see.
    catalog_guard: dict[str, object] | None = None


class ReplyResponse(BaseModel):
    message_id: uuid.UUID
    content: str


class SendMessageResponse(BaseModel):
    message_id: uuid.UUID
    reply: ReplyResponse
    trace: TraceResponse


class MessageResponse(BaseModel):
    id: uuid.UUID
    direction: str
    author: str
    content: str
    created_at: datetime


class HistoryResponse(BaseModel):
    items: list[MessageResponse]
    total: int
    limit: int
    offset: int


def _not_found() -> ApiError:
    return ApiError(status_code=status.HTTP_404_NOT_FOUND, error="not_found", message="Not found.")


@router.post(
    "/conversations",
    status_code=status.HTTP_201_CREATED,
    summary="Open a sandbox conversation",
    responses=error_responses(
        (409, "`sandbox_not_ready`"), (429, "`rate_limited`"), *AUTH_RESPONSES
    ),
)
async def start_conversation(
    principal: MemberDep, session: SessionDep, llm: PlatformLLMDep
) -> StartConversationResponse:
    """Guarded by the same checklist the panel shows, so "start chatting" is
    never offered next to a call that refuses."""
    service = SandboxService(session, principal.tenant_id, llm=llm)
    try:
        conversation = service.start(user=principal.user, started_by=principal.user_id)
    except SandboxNotReady as exc:
        raise ApiError(
            status_code=status.HTTP_409_CONFLICT,
            error="sandbox_not_ready",
            message="Finish the activation checklist before trying your agent.",
        ) from exc
    return StartConversationResponse(conversation_id=conversation.id)


@router.post(
    "/conversations/{conversation_id}/messages",
    summary="Send a message and get the agent's reply",
    responses=error_responses(
        (400, "`validation_error`"),
        (404, "`not_found`"),
        (429, "`rate_limited`"),
        *AUTH_RESPONSES,
    ),
)
async def send_message(
    conversation_id: uuid.UUID,
    payload: SendMessageRequest,
    principal: MemberDep,
    session: SessionDep,
    limiter: SandboxLimiterDep,
    llm: PlatformLLMDep,
) -> SendMessageResponse:
    """One turn, no streaming in v1.

    Rate limited per tenant rather than per user: the cost being guarded is
    the LLM bill, which the business incurs however many of its people are
    typing.
    """
    decision = await limiter.hit(f"sandbox:{principal.tenant_id}")
    if not decision.allowed:
        raise ApiError(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            error="rate_limited",
            message="Too many messages. Give your agent a moment.",
            headers={"Retry-After": str(decision.retry_after_seconds)},
        )

    service = SandboxService(session, principal.tenant_id, llm=llm)
    try:
        turn = await service.send(conversation_id, content=payload.content)
    except ConversationNotFound as exc:
        raise _not_found() from exc

    return SendMessageResponse(
        message_id=turn.customer_message.id,
        reply=ReplyResponse(message_id=turn.agent_message.id, content=turn.reply.content),
        trace=TraceResponse(
            model=turn.reply.model,
            tools_called=turn.reply.tools_called,
            rag_sources=turn.reply.rag_sources,
            input_tokens=turn.reply.input_tokens,
            output_tokens=turn.reply.output_tokens,
            latency_ms=turn.reply.latency_ms,
            degraded=turn.reply.degraded,
            catalog_guard=turn.reply.catalog_guard,
        ),
    )


@router.get(
    "/conversations/{conversation_id}/messages",
    summary="Read the transcript",
    responses=error_responses((404, "`not_found`"), *AUTH_RESPONSES),
)
async def get_history(
    conversation_id: uuid.UUID,
    principal: MemberDep,
    session: SessionDep,
    llm: PlatformLLMDep,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> HistoryResponse:
    service = SandboxService(session, principal.tenant_id, llm=llm)
    try:
        messages, total = service.history(conversation_id, limit=limit, offset=offset)
    except ConversationNotFound as exc:
        raise _not_found() from exc

    return HistoryResponse(
        items=[
            MessageResponse(
                id=message.id,
                direction=message.direction,
                author=message.author,
                content=message.content,
                created_at=message.created_at,
            )
            for message in messages
        ],
        total=total,
        limit=limit,
        offset=offset,
    )
