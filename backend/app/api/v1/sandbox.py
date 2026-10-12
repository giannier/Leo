"""Sandbox endpoints: converse with your own agent (TASK-015, TASK-422).

The `/tenant/sandbox` section of `contracts/api.md` (section 4 there writes
`/sandbox/...`; the repo's tenant routers hang from `/tenant`, `conciliacion.md`
3.2). Any member may use it, operators included - the sandbox is where somebody
learns what the agent will say before it says it to a customer.

Free and unmetered by design (Clarification 002). The guard is a per-tenant
rate limit rather than a quota, because a limit throttles abuse without ever
telling a paying customer they have run out of the thing they are evaluating.

Feature 004 turns the reply into what a customer would see: `salientes` are the
typed outbound messages in order (a file, buttons, a list, the text), and
`?debug=true` adds the debug panel - the stage before and after, the funnel, the
facts, the tools offered and called (C-19). Nothing here reaches the provider.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, Query, Request, Response, status
from pydantic import BaseModel, StringConstraints, ValidationError, model_validator
from sqlalchemy.orm import Session
from starlette.datastructures import UploadFile

from app.agent.pipeline import TurnResult
from app.api.deps import (
    MemberDep,
    PlatformLLMDep,
    SandboxLimiterDep,
    SessionDep,
    get_storage_provider,
)
from app.api.errors import ApiError, error_responses
from app.models.conversation import Message
from app.process.state import facts_snapshot
from app.providers.storage.base import StorageError, StorageProvider
from app.providers.whatsapp.base import InteractiveReply
from app.repositories.media import MediaAssetRepository
from app.services.sandbox import ConversationNotFound, SandboxNotReady, SandboxService

router = APIRouter(prefix="/tenant/sandbox", tags=["sandbox"])

# WhatsApp's own limit is 4096; matching it means the sandbox cannot accept a
# message the real channel would reject.
MessageText = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1, max_length=4096)
]

# What WhatsApp accepts as an image, and its size cap.
IMAGE_TYPES = ("image/jpeg", "image/png", "image/webp")
MAX_IMAGE_BYTES = 5 * 1024 * 1024
# The line stored for an image: the same placeholder the real channel uses.
IMAGE_PLACEHOLDER = "[imagen]"
# How long the link to a library file lives. Short: it opens a private object.
FILE_LINK_SECONDS = 600

AUTH_RESPONSES = (
    (401, "`not_authenticated`, `token_expired` or `invalid_token`"),
    (403, "`email_not_verified`, `no_active_tenant` or `tenant_access_denied`"),
)

StorageDep = Annotated[StorageProvider, Depends(get_storage_provider)]


class StartConversationResponse(BaseModel):
    conversation_id: uuid.UUID


class SendMessageRequest(BaseModel):
    """What the customer did: wrote (`content`) or tapped (`interactive_id`).

    An image goes as `multipart/form-data` with an `image` part instead.
    """

    content: MessageText | None = None
    interactive_id: (
        Annotated[str, StringConstraints(strip_whitespace=True, max_length=200)] | None
    ) = None
    # The title the customer saw on the button or row; it is what the transcript shows.
    title: Annotated[str, StringConstraints(strip_whitespace=True, max_length=200)] | None = None

    @model_validator(mode="after")
    def _one_kind_of_message(self) -> SendMessageRequest:
        if bool(self.content) == bool(self.interactive_id):
            raise ValueError("send either `content` or `interactive_id`")
        if self.interactive_id and not self.title:
            raise ValueError("`title` is required with `interactive_id`")
        return self


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


class Button(BaseModel):
    id: str
    title: str


class Row(BaseModel):
    id: str
    title: str
    description: str | None = None


class Saliente(BaseModel):
    """One outbound message, typed the way the panel draws it.

    `tipo` says which fields are filled: `texto` -> `texto`; `imagen`, `documento`,
    `video`, `audio` -> `clave`, `titulo`, `caption`, `url`; `botones` -> `body`,
    `botones`; `lista` -> `body`, `boton`, `filas`.
    """

    message_id: uuid.UUID
    tipo: Literal["texto", "imagen", "documento", "video", "audio", "botones", "lista"]
    created_at: datetime
    texto: str | None = None
    body: str | None = None
    caption: str | None = None
    clave: str | None = None
    titulo: str | None = None
    # A short-lived link to the file; null if the library file no longer exists.
    url: str | None = None
    botones: list[Button] | None = None
    boton: str | None = None
    filas: list[Row] | None = None


class DebugTool(BaseModel):
    nombre: str
    status: str


class DebugTokens(BaseModel):
    prompt: int
    completion: int


class DebugResponse(BaseModel):
    """The debug panel (C-19): where the conversation is and what the agent had."""

    etapa_antes: str
    etapa_despues: str
    embudo: str
    # Stored and derived facts, without the customer's personal data (C-20).
    hechos: dict[str, Any]
    herramientas_habilitadas: list[str]
    herramientas_llamadas: list[DebugTool]
    tokens: DebugTokens
    modelo: str | None = None
    # Why the assistant stayed quiet, if it did: `handover`, `paused`, `opted_out`.
    silencio: str | None = None


class SendMessageResponse(BaseModel):
    message_id: uuid.UUID
    salientes: list[Saliente]
    # The first text bubble and the trace of the turn; null when the turn
    # produced no text (a handover is in force, or the tools said it all).
    reply: ReplyResponse | None = None
    trace: TraceResponse | None = None
    debug: DebugResponse | None = None


class MessageResponse(BaseModel):
    id: uuid.UUID
    direction: str
    author: str
    content: str
    created_at: datetime
    # The typed form of an outbound message; null for what the customer said.
    saliente: Saliente | None = None


class HistoryResponse(BaseModel):
    items: list[MessageResponse]
    total: int
    limit: int
    offset: int


def _not_found() -> ApiError:
    return ApiError(status_code=status.HTTP_404_NOT_FOUND, error="not_found", message="Not found.")


def _invalid(message: str, field: str) -> ApiError:
    return ApiError(
        status_code=status.HTTP_400_BAD_REQUEST,
        error="validation_error",
        message=message,
        fields={field: message},
    )


# ------------------------------------------------------------ presentation


def present(
    messages: list[Message], session: Session, service_tenant: uuid.UUID, storage: StorageProvider
) -> list[Saliente]:
    """The typed form of outbound messages, in order.

    The payload holds the structure; a file is turned into a fresh short-lived
    link here, at read time, because a link stored with the message would expire
    while the transcript lives on.
    """
    assets = MediaAssetRepository(session, service_tenant)
    typed: list[Saliente] = []
    for message in messages:
        payload = message.payload or {}
        kind = payload.get("tipo")
        base: dict[str, Any] = {"message_id": message.id, "created_at": message.created_at}
        if kind == "botones":
            typed.append(
                Saliente(
                    **base,
                    tipo="botones",
                    body=str(payload.get("body", message.content)),
                    botones=[Button(**button) for button in payload.get("botones", [])],
                )
            )
        elif kind == "lista":
            typed.append(
                Saliente(
                    **base,
                    tipo="lista",
                    body=str(payload.get("body", message.content)),
                    boton=payload.get("boton"),
                    filas=[Row(**row) for row in payload.get("filas", [])],
                )
            )
        elif kind == "media":
            url: str | None = None
            asset = None
            raw_id = payload.get("asset_id")
            if isinstance(raw_id, str):
                try:
                    asset = assets.get(uuid.UUID(raw_id))
                except ValueError:
                    asset = None
            if asset is not None:
                try:
                    url = storage.presigned_url(asset.storage_key, expires_in=FILE_LINK_SECONDS)
                except StorageError:
                    # The file left the object store: the transcript still shows
                    # that it was sent, just without a preview.
                    url = None
            typed.append(
                Saliente(
                    **base,
                    tipo=payload.get("media", "imagen"),
                    clave=payload.get("clave"),
                    titulo=payload.get("titulo"),
                    caption=payload.get("caption"),
                    url=url,
                )
            )
        else:
            typed.append(Saliente(**base, tipo="texto", texto=message.content))
    return typed


def _debug(result: TurnResult) -> DebugResponse:
    reply = result.reply
    return DebugResponse(
        etapa_antes=result.stage_before.value,
        etapa_despues=result.stage_after.value,
        embudo=result.funnel.value,
        hechos=facts_snapshot(result.facts),
        herramientas_habilitadas=list(result.tools_enabled),
        herramientas_llamadas=[
            DebugTool(nombre=str(call["tool"]), status=str(call.get("status", "hecho")))
            for call in (reply.tools_called if reply else [])
        ],
        tokens=DebugTokens(
            prompt=reply.input_tokens if reply else 0,
            completion=reply.output_tokens if reply else 0,
        ),
        modelo=result.model_tier or (reply.model if reply else None),
        silencio=result.gate,
    )


async def _read_request(request: Request) -> tuple[str, InteractiveReply | None, bool]:
    """The customer's message from either shape of body: (text, tap, is_image)."""
    content_type = request.headers.get("content-type", "")
    if content_type.startswith("multipart/form-data"):
        form = await request.form()
        upload = form.get("image")
        if not isinstance(upload, UploadFile):
            raise _invalid("An `image` part is required.", "image")
        if upload.content_type not in IMAGE_TYPES:
            raise _invalid("The image must be a JPEG, PNG or WebP file.", "image")
        # One byte past the cap is enough to know it is too large.
        data = await upload.read(MAX_IMAGE_BYTES + 1)
        if not data:
            raise _invalid("The image is empty.", "image")
        if len(data) > MAX_IMAGE_BYTES:
            raise _invalid("The image is larger than 5 MB.", "image")
        caption = form.get("content")
        text = (
            caption.strip() if isinstance(caption, str) and caption.strip() else IMAGE_PLACEHOLDER
        )
        return text[:4096], None, True

    try:
        payload = SendMessageRequest.model_validate(await request.json())
    except (ValidationError, ValueError) as exc:
        raise _invalid("Send `content`, or `interactive_id` with its `title`.", "content") from exc
    if payload.interactive_id:
        return (
            payload.title or "",
            InteractiveReply(id=payload.interactive_id, title=payload.title or ""),
            False,
        )
    return payload.content or "", None, False


# --------------------------------------------------------------- endpoints


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
    summary="Send a message, a tapped option or an image, and get the agent's reply",
    openapi_extra={
        "requestBody": {
            "required": True,
            "content": {
                "application/json": {"schema": SendMessageRequest.model_json_schema()},
                "multipart/form-data": {
                    "schema": {
                        "type": "object",
                        "properties": {
                            "image": {"type": "string", "format": "binary"},
                            "content": {"type": "string", "description": "Optional caption."},
                        },
                        "required": ["image"],
                    }
                },
            },
        }
    },
    responses=error_responses(
        (400, "`validation_error`"),
        (404, "`not_found`"),
        (429, "`rate_limited`"),
        *AUTH_RESPONSES,
    ),
)
async def send_message(
    conversation_id: uuid.UUID,
    request: Request,
    principal: MemberDep,
    session: SessionDep,
    limiter: SandboxLimiterDep,
    llm: PlatformLLMDep,
    storage: StorageDep,
    debug: Annotated[bool, Query()] = False,
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

    text, tap, is_image = await _read_request(request)
    service = SandboxService(session, principal.tenant_id, llm=llm)
    try:
        turn = await service.send(conversation_id, content=text, interactive=tap, image=is_image)
    except ConversationNotFound as exc:
        raise _not_found() from exc

    reply = turn.result.reply
    first = turn.agent_message
    return SendMessageResponse(
        message_id=turn.customer_message.id,
        salientes=present(turn.outbound, session, principal.tenant_id, storage),
        reply=ReplyResponse(message_id=first.id, content=first.content) if first else None,
        trace=(
            TraceResponse(
                model=reply.model,
                tools_called=reply.tools_called,
                rag_sources=reply.rag_sources,
                input_tokens=reply.input_tokens,
                output_tokens=reply.output_tokens,
                latency_ms=reply.latency_ms,
                degraded=reply.degraded,
                catalog_guard=reply.catalog_guard,
            )
            if reply is not None
            else None
        ),
        debug=_debug(turn.result) if debug else None,
    )


@router.get(
    "/conversations/{conversation_id}/messages",
    summary="Read the transcript, or only what the agent said after a message",
    responses=error_responses((404, "`not_found`"), *AUTH_RESPONSES),
)
async def get_history(
    conversation_id: uuid.UUID,
    principal: MemberDep,
    session: SessionDep,
    llm: PlatformLLMDep,
    storage: StorageDep,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
    after: Annotated[uuid.UUID | None, Query()] = None,
) -> HistoryResponse:
    """With `after`, the asynchronous outbounds posterior to that message (the
    confirmation or rejection of a payment, a handover, a follow-up): the
    simulator asks every few seconds while it is open (C-19)."""
    service = SandboxService(session, principal.tenant_id, llm=llm)
    try:
        if after is not None:
            messages = service.outbound_after(conversation_id, after)
            total = len(messages)
        else:
            messages, total = service.history(conversation_id, limit=limit, offset=offset)
    except ConversationNotFound as exc:
        raise _not_found() from exc

    typed = {
        item.message_id: item
        for item in present(
            [m for m in messages if m.direction == "outbound"],
            session,
            principal.tenant_id,
            storage,
        )
    }
    return HistoryResponse(
        items=[
            MessageResponse(
                id=message.id,
                direction=message.direction,
                author=message.author,
                content=message.content,
                created_at=message.created_at,
                saliente=typed.get(message.id),
            )
            for message in messages
        ],
        total=total,
        limit=limit,
        offset=offset,
    )


@router.post(
    "/conversations/{conversation_id}/reset",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Start the conversation over",
    responses=error_responses((404, "`not_found`"), *AUTH_RESPONSES),
)
async def reset_conversation(
    conversation_id: uuid.UUID,
    principal: MemberDep,
    session: SessionDep,
    llm: PlatformLLMDep,
) -> Response:
    """Deletes the state, the messages and the sandbox orders of this
    conversation, so the owner can walk the sales process from the greeting."""
    service = SandboxService(session, principal.tenant_id, llm=llm)
    try:
        service.reset(conversation_id)
    except ConversationNotFound as exc:
        raise _not_found() from exc
    return Response(status_code=status.HTTP_204_NO_CONTENT)
