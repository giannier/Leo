"""A retried POST must not run twice: same key, same answer, one row."""

from __future__ import annotations

import asyncio
import uuid
from typing import Any

from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from redis.asyncio import Redis

from app.core.config import get_settings
from app.core.idempotency import IdempotencyMiddleware


def _fresh_redis() -> Redis:
    # A client built inside this test's own event loop: the cached one may belong to another.
    return Redis.from_url(get_settings().redis_url, decode_responses=True)


def _app(calls: list[dict[str, Any]], *, status: int = 201, delay: float = 0.0) -> FastAPI:
    app = FastAPI()
    app.add_middleware(IdempotencyMiddleware, redis_factory=_fresh_redis, wait_seconds=3.0)

    @app.post("/api/v1/things")
    async def things(body: dict[str, Any]) -> Any:
        from fastapi.responses import JSONResponse

        calls.append(body)
        if delay:
            await asyncio.sleep(delay)
        return JSONResponse({"id": len(calls), "n": body.get("n")}, status_code=status)

    @app.post("/api/v1/auth/login")
    async def login(body: dict[str, Any]) -> Any:
        calls.append(body)
        return {"id": len(calls)}

    return app


def _client(app: FastAPI) -> AsyncClient:
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


def _key() -> str:
    return f"key-{uuid.uuid4().hex}"


async def test_the_same_key_runs_once_and_replays_the_answer() -> None:
    calls: list[dict[str, Any]] = []
    async with _client(_app(calls)) as api:
        key = _key()
        first = await api.post("/api/v1/things", json={"n": 1}, headers={"Idempotency-Key": key})
        second = await api.post("/api/v1/things", json={"n": 1}, headers={"Idempotency-Key": key})

    assert first.status_code == second.status_code == 201
    assert first.json() == second.json()
    assert len(calls) == 1
    assert second.headers["idempotent-replay"] == "true"
    assert "idempotent-replay" not in first.headers


async def test_no_key_means_every_request_runs() -> None:
    calls: list[dict[str, Any]] = []
    async with _client(_app(calls)) as api:
        await api.post("/api/v1/things", json={"n": 1})
        await api.post("/api/v1/things", json={"n": 1})

    assert len(calls) == 2


async def test_different_keys_are_different_requests() -> None:
    calls: list[dict[str, Any]] = []
    async with _client(_app(calls)) as api:
        await api.post("/api/v1/things", json={"n": 1}, headers={"Idempotency-Key": _key()})
        await api.post("/api/v1/things", json={"n": 1}, headers={"Idempotency-Key": _key()})

    assert len(calls) == 2


async def test_the_same_key_with_other_data_is_refused() -> None:
    calls: list[dict[str, Any]] = []
    async with _client(_app(calls)) as api:
        key = _key()
        await api.post("/api/v1/things", json={"n": 1}, headers={"Idempotency-Key": key})
        other = await api.post("/api/v1/things", json={"n": 2}, headers={"Idempotency-Key": key})

    assert other.status_code == 422
    assert other.json()["error"] == "idempotency_key_reuse"
    assert len(calls) == 1


async def test_simultaneous_presses_run_once() -> None:
    """The case that mattered: many presses while the first is still working."""
    calls: list[dict[str, Any]] = []
    async with _client(_app(calls, delay=0.5)) as api:
        key = _key()
        answers = await asyncio.gather(
            *[
                api.post("/api/v1/things", json={"n": 1}, headers={"Idempotency-Key": key})
                for _ in range(6)
            ]
        )

    assert len(calls) == 1
    assert {a.status_code for a in answers} == {201}
    assert len({a.json()["id"] for a in answers}) == 1


async def test_a_failure_is_not_remembered_so_the_retry_really_runs() -> None:
    calls: list[dict[str, Any]] = []
    async with _client(_app(calls, status=500)) as api:
        key = _key()
        await api.post("/api/v1/things", json={"n": 1}, headers={"Idempotency-Key": key})
        await api.post("/api/v1/things", json={"n": 1}, headers={"Idempotency-Key": key})

    assert len(calls) == 2


async def test_login_is_never_replayed() -> None:
    calls: list[dict[str, Any]] = []
    async with _client(_app(calls)) as api:
        key = _key()
        await api.post("/api/v1/auth/login", json={"a": 1}, headers={"Idempotency-Key": key})
        await api.post("/api/v1/auth/login", json={"a": 1}, headers={"Idempotency-Key": key})

    assert len(calls) == 2


async def test_another_credential_never_sees_the_stored_answer() -> None:
    calls: list[dict[str, Any]] = []
    async with _client(_app(calls)) as api:
        key = _key()
        mine = await api.post(
            "/api/v1/things",
            json={"n": 1},
            headers={"Idempotency-Key": key, "Authorization": "Bearer alice"},
        )
        theirs = await api.post(
            "/api/v1/things",
            json={"n": 1},
            headers={"Idempotency-Key": key, "Authorization": "Bearer bob"},
        )

    assert len(calls) == 2
    assert mine.json()["id"] != theirs.json()["id"]


async def test_without_redis_the_request_still_runs() -> None:
    calls: list[dict[str, Any]] = []
    app = FastAPI()
    app.add_middleware(
        IdempotencyMiddleware,
        redis_factory=lambda: Redis.from_url("redis://127.0.0.1:1/0", decode_responses=True),
    )

    @app.post("/api/v1/things")
    async def things(body: dict[str, Any]) -> Any:
        calls.append(body)
        return {"ok": True}

    async with _client(app) as api:
        answer = await api.post(
            "/api/v1/things", json={"n": 1}, headers={"Idempotency-Key": _key()}
        )

    assert answer.status_code == 200 and len(calls) == 1


async def test_a_file_upload_goes_through_untouched() -> None:
    calls: list[dict[str, Any]] = []
    app = _app(calls)

    @app.post("/api/v1/upload")
    async def upload() -> Any:
        calls.append({})
        return {"ok": True}

    async with _client(app) as api:
        key = _key()
        files = {"file": ("a.txt", b"hello", "text/plain")}
        await api.post("/api/v1/upload", files=files, headers={"Idempotency-Key": key})
        await api.post("/api/v1/upload", files=files, headers={"Idempotency-Key": key})

    assert len(calls) == 2
