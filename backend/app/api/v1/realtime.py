"""The inbox's live channel (Feature 007, TASK-702, RF-001 and RF-003).

`WS /api/v1/ws`, opened with the subprotocols `["leo", <access token>]`:

* **Authentication.** A browser cannot set headers on a WebSocket, and a token in the
  URL ends up in every access log, so the panel offers it as the second subprotocol
  (the server answers with `leo`). It is verified like any request, and the membership
  in its tenant is read from the database: a removed member is refused even with a
  token that has not expired yet. A refused socket closes with 4401 or 4403.
* **Events.** Everything published on the tenant's Redis channel
  (`app/core/events.py`) is forwarded as is: `message.new`, `conversation.updated`,
  `presence`. Events carry ids and kinds, never content; the panel refetches.
* **Presence.** The panel says which conversation it is looking at
  (`{"tipo": "viendo", "conversation_id": ...}`) every 15 seconds; the server
  forwards it to the others as a `presence` event with the person's name. Ephemeral
  by nature: nothing is stored, a panel forgets a viewer after 30 seconds.
* **Re-sync.** A dropped connection loses events; on reconnecting the panel reads
  the state again through the API (the plan's mitigation), so nothing is replayed.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import uuid
from collections.abc import Iterator
from typing import Any

from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from redis.asyncio import Redis
from sqlalchemy.orm import Session

from app.api.deps import get_db, get_redis
from app.core.events import channel
from app.core.tokens import decode_access_token
from app.models.user import User
from app.repositories.tenant import TenantUserRepository

logger = logging.getLogger(__name__)
router = APIRouter(tags=["inbox"])

CLOSE_UNAUTHENTICATED = 4401
CLOSE_FORBIDDEN = 4403
SUBPROTOCOL = "leo"


def _offered_token(websocket: WebSocket) -> str:
    """The access token offered as `["leo", <token>]`, or "" when it is not there."""
    offered = [p.strip() for p in websocket.headers.get("sec-websocket-protocol", "").split(",")]
    return offered[1] if len(offered) == 2 and offered[0] == SUBPROTOCOL else ""


def _member(websocket: WebSocket, token: str) -> tuple[uuid.UUID, uuid.UUID, str] | int:
    """(tenant, user id, name) of a valid token with a live membership, or the close code."""
    try:
        claims = decode_access_token(token)
    except Exception:  # noqa: BLE001 - expired, malformed or forged: all the same here
        return CLOSE_UNAUTHENTICATED
    if claims.tenant_id is None:
        return CLOSE_FORBIDDEN
    # The app's own session provider, so a test's override (its transaction) applies.
    provided = websocket.app.dependency_overrides.get(get_db, get_db)()
    owned = isinstance(provided, Iterator)
    session: Session = next(provided) if owned else provided
    try:
        user = session.get(User, claims.user_id)
        membership = TenantUserRepository(session, claims.tenant_id).active_membership_for(
            claims.user_id
        )
        if user is None or membership is None:
            return CLOSE_FORBIDDEN
        return claims.tenant_id, user.id, user.full_name
    finally:
        if owned:
            with contextlib.suppress(StopIteration):
                next(provided)


@router.websocket("/ws")
async def inbox_socket(websocket: WebSocket) -> None:
    resolved = _member(websocket, _offered_token(websocket))
    if isinstance(resolved, int):
        await websocket.close(code=resolved)
        return
    tenant_id, user_id, user_name = resolved
    redis: Redis = websocket.app.dependency_overrides.get(get_redis, get_redis)()
    await websocket.accept(subprotocol=SUBPROTOCOL)
    pubsub = redis.pubsub()
    await pubsub.subscribe(channel(tenant_id))

    async def forward() -> None:
        async for message in pubsub.listen():
            if message.get("type") != "message":
                continue
            data = message.get("data")
            await websocket.send_text(data if isinstance(data, str) else data.decode())

    async def receive() -> None:
        while True:
            raw = await websocket.receive_text()
            try:
                parsed: dict[str, Any] = json.loads(raw)
            except ValueError:
                continue
            if parsed.get("tipo") == "viendo":
                try:
                    conversation = str(uuid.UUID(str(parsed.get("conversation_id"))))
                except ValueError:
                    continue
                event = {
                    "type": "presence",
                    "conversation_id": conversation,
                    "user_id": str(user_id),
                    "nombre": user_name,
                }
                await redis.publish(channel(tenant_id), json.dumps(event))

    tasks = [asyncio.create_task(forward()), asyncio.create_task(receive())]
    try:
        done, _ = await asyncio.wait(tasks, return_when=asyncio.FIRST_EXCEPTION)
        for task in done:
            exc = task.exception()
            if exc is not None and not isinstance(exc, WebSocketDisconnect):
                logger.warning("inbox socket ended", extra={"error": repr(exc)})
    finally:
        for task in tasks:
            task.cancel()
        with contextlib.suppress(Exception):
            await pubsub.unsubscribe(channel(tenant_id))
            await pubsub.aclose()  # type: ignore[no-untyped-call]
