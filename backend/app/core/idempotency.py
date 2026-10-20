"""Idempotent POSTs, so a retry never creates a second copy.

On a flaky connection a person presses "Guardar" again and again, or the answer to
the first press is lost and the screen retries. Without help each press is a new
request and a new row. The browser sends one `Idempotency-Key` per intent and reuses
it for every retry of the same payload; the first request that gets through runs, and
the others receive *its* response instead of running again.

What this deliberately does not do:

* Only successful (2xx) responses are remembered. A refused request can be corrected
  and sent again, and a crash must be retried for real.
* Only small JSON bodies, and never `/auth/*` (tokens and cookies): uploads and logins
  go through untouched.
* It never makes a request fail because Redis is down; it just stops protecting.
* The key is scoped to the caller's own credential and the path, so one person's key
  can never replay another's response.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import logging
import re
import time
from collections.abc import Callable
from typing import Any

from redis.asyncio import Redis
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.core.redis import get_redis_client

logger = logging.getLogger(__name__)

KEY_PATTERN = re.compile(rb"^[A-Za-z0-9_\-]{8,128}$")
RESULT_TTL_SECONDS = 24 * 3600
LOCK_SECONDS = 60
WAIT_SECONDS = 15.0
POLL_SECONDS = 0.2
MAX_BODY_BYTES = 1_000_000


class IdempotencyMiddleware:
    def __init__(
        self,
        app: ASGIApp,
        *,
        prefix: str = "/api/v1/",
        excluded: tuple[str, ...] = ("/api/v1/auth/",),
        redis_factory: Callable[[], Redis] = get_redis_client,
        wait_seconds: float = WAIT_SECONDS,
    ) -> None:
        self._app = app
        self._prefix = prefix
        self._excluded = excluded
        self._redis_factory = redis_factory
        self._wait = wait_seconds

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if not self._applies(scope):
            await self._app(scope, receive, send)
            return

        headers = dict(scope["headers"])
        key = headers[b"idempotency-key"]
        length = int(headers.get(b"content-length", b"0") or 0)
        body = await _read_body(receive, length)

        digest = hashlib.sha256(
            headers.get(b"authorization", b"") + b"|" + scope["path"].encode() + b"|" + key
        ).hexdigest()
        fingerprint = hashlib.sha256(scope["path"].encode() + b"|" + body).hexdigest()
        result_key, lock_key = f"idem:{digest}", f"idem:lock:{digest}"

        try:
            redis = self._redis_factory()
            stored = await self._wait_for_turn(redis, result_key, lock_key)
        except Exception:
            logger.warning(
                "idempotency is off for this request: redis is unavailable", exc_info=True
            )
            await self._app(scope, _replay(body, receive), send)
            return

        if isinstance(stored, str):
            await _respond(
                send,
                409,
                {
                    "error": "request_in_progress",
                    "message": "La solicitud anterior sigue en curso. Espera un momento.",
                },
            )
            return
        if stored is not None:
            if stored["fp"] != fingerprint:
                await _respond(
                    send,
                    422,
                    {
                        "error": "idempotency_key_reuse",
                        "message": "Esa clave ya se usó con otros datos.",
                    },
                )
                return
            await _respond_stored(send, stored)
            return

        # This request owns the key: run it, remember a success, release the lock.
        captured: list[Message] = []

        async def capture(message: Message) -> None:
            captured.append(message)

        try:
            await self._app(scope, _replay(body, receive), capture)
            start = next((m for m in captured if m["type"] == "http.response.start"), None)
            if start is not None and 200 <= start["status"] < 300:
                payload = b"".join(
                    m.get("body", b"") for m in captured if m["type"] == "http.response.body"
                )
                ctype = next(
                    (v for k, v in start.get("headers", []) if k.lower() == b"content-type"), b""
                )
                record = {
                    "fp": fingerprint,
                    "status": start["status"],
                    "body": base64.b64encode(payload).decode(),
                    "ctype": ctype.decode(),
                }
                try:
                    await redis.set(result_key, json.dumps(record), ex=RESULT_TTL_SECONDS)
                except Exception:
                    logger.warning("could not remember an idempotent response", exc_info=True)
        finally:
            try:
                await redis.delete(lock_key)
            except Exception:
                logger.warning("could not release an idempotency lock", exc_info=True)

        for message in captured:
            await send(message)

    def _applies(self, scope: Scope) -> bool:
        if scope["type"] != "http" or scope["method"] != "POST":
            return False
        path: str = scope["path"]
        if not path.startswith(self._prefix) or path.startswith(self._excluded):
            return False
        headers = dict(scope["headers"])
        key = headers.get(b"idempotency-key")
        if key is None or KEY_PATTERN.match(key) is None:
            return False
        if not headers.get(b"content-type", b"").startswith(b"application/json"):
            return False
        length = headers.get(b"content-length")
        return length is not None and length.isdigit() and int(length) <= MAX_BODY_BYTES

    async def _wait_for_turn(
        self, redis: Redis, result_key: str, lock_key: str
    ) -> dict[str, Any] | str | None:
        """The stored response, "busy" if the one running did not finish in time, or
        None once this request holds the lock."""
        deadline = time.monotonic() + self._wait
        while True:
            raw = await redis.get(result_key)
            if raw:
                loaded: dict[str, Any] = json.loads(raw)
                return loaded
            if await redis.set(lock_key, "1", nx=True, ex=LOCK_SECONDS):
                # It may have finished between the read and the lock.
                raw = await redis.get(result_key)
                if raw:
                    await redis.delete(lock_key)
                    loaded = json.loads(raw)
                    return loaded
                return None
            if time.monotonic() >= deadline:
                return "busy"
            await asyncio.sleep(POLL_SECONDS)


async def _read_body(receive: Receive, length: int) -> bytes:
    chunks: list[bytes] = []
    received = 0
    while received < length:
        message = await receive()
        if message["type"] != "http.request":
            break
        chunk = message.get("body", b"")
        chunks.append(chunk)
        received += len(chunk)
        if not message.get("more_body", False):
            break
    return b"".join(chunks)


def _replay(body: bytes, receive: Receive) -> Receive:
    """A `receive` that hands the application the body already read, once."""
    sent = False

    async def replay() -> Message:
        nonlocal sent
        if not sent:
            sent = True
            return {"type": "http.request", "body": body, "more_body": False}
        return await receive()

    return replay


async def _respond(send: Send, status: int, content: dict[str, str]) -> None:
    payload = json.dumps(content).encode()
    await send(
        {
            "type": "http.response.start",
            "status": status,
            "headers": [
                (b"content-type", b"application/json"),
                (b"content-length", str(len(payload)).encode()),
            ],
        }
    )
    await send({"type": "http.response.body", "body": payload})


async def _respond_stored(send: Send, stored: dict[str, Any]) -> None:
    payload = base64.b64decode(stored["body"])
    await send(
        {
            "type": "http.response.start",
            "status": stored["status"],
            "headers": [
                (b"content-type", stored["ctype"].encode() or b"application/json"),
                (b"content-length", str(len(payload)).encode()),
                (b"idempotent-replay", b"true"),
            ],
        }
    )
    await send({"type": "http.response.body", "body": payload})
