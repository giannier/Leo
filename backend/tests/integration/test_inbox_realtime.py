"""The inbox's live channel (Feature 007, TASK-702).

Against a real Redis (CI runs one): an authenticated panel receives the events of
its business and only those, sees who else is looking at a conversation, and a
socket without a valid token or membership is closed before it hears anything.
"""

from __future__ import annotations

import json
import uuid
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from redis import Redis as SyncRedis
from redis.asyncio import Redis
from redis.exceptions import ConnectionError as RedisConnectionError
from sqlalchemy.orm import Session
from starlette.websockets import WebSocketDisconnect

from app.api.deps import get_db, get_redis
from app.core import events
from app.core.config import get_settings
from app.core.tokens import issue_access_token
from app.models.tenant import Tenant, TenantUser
from app.models.user import User


@pytest.fixture
def live(app: FastAPI, db: Session) -> Iterator[TestClient]:
    url = get_settings().redis_url
    try:
        SyncRedis.from_url(url, socket_connect_timeout=1).ping()
    except RedisConnectionError:
        pytest.skip("Redis is not reachable")
    app.dependency_overrides[get_db] = lambda: db
    app.dependency_overrides[get_redis] = lambda: Redis.from_url(url, decode_responses=True)
    events.set_publisher(None)  # the real Redis, not the recorder
    with TestClient(app) as client:
        yield client


def _member(db: Session, *, role: str = "operator", active: bool = True) -> tuple[uuid.UUID, str]:
    tenant = Tenant(
        name=f"WS {uuid.uuid4().hex[:6]}",
        status="trial",
        plan_id="trial",
        trial_ends_at=datetime.now(UTC) + timedelta(days=7),
    )
    user = User(
        email=f"ws-{uuid.uuid4().hex[:10]}@example.com",
        password_hash="x",
        full_name="Bruno",
        email_verified_at=datetime.now(UTC),
    )
    db.add_all([tenant, user])
    db.flush()
    db.add(
        TenantUser(
            tenant_id=tenant.id,
            user_id=user.id,
            role=role,
            status="active" if active else "disabled",
        )
    )
    db.flush()
    token, _ = issue_access_token(user_id=user.id, tenant_id=tenant.id, now=datetime.now(UTC))
    return tenant.id, token


class TestTheLiveChannel:
    def test_a_panel_hears_its_business_and_only_its_business(
        self, live: TestClient, db: Session
    ) -> None:
        mine, token = _member(db)
        other, _ = _member(db)
        conversation = uuid.uuid4()

        with live.websocket_connect("/api/v1/ws", subprotocols=["leo", token]) as socket:
            socket.send_text(json.dumps({"tipo": "viendo", "conversation_id": str(conversation)}))
            presence = json.loads(socket.receive_text())
            events.publish(other, "message.new", conversation_id=uuid.uuid4())
            events.publish(mine, "message.new", conversation_id=conversation)
            heard = json.loads(socket.receive_text())

        assert presence == {
            "type": "presence",
            "conversation_id": str(conversation),
            "user_id": presence["user_id"],
            "nombre": "Bruno",
        }
        assert heard == {"type": "message.new", "conversation_id": str(conversation)}

    @pytest.mark.parametrize("bad", ["", "not-a-token"])
    def test_without_a_valid_token_the_socket_is_closed(self, live: TestClient, bad: str) -> None:
        with (
            pytest.raises(WebSocketDisconnect) as closed,
            live.websocket_connect(
                "/api/v1/ws", subprotocols=["leo", bad] if bad else ["leo"]
            ) as socket,
        ):
            socket.receive_text()

        assert closed.value.code == 4401

    def test_a_disabled_member_is_refused_even_with_a_live_token(
        self, live: TestClient, db: Session
    ) -> None:
        _, token = _member(db, active=False)

        with (
            pytest.raises(WebSocketDisconnect) as closed,
            live.websocket_connect("/api/v1/ws", subprotocols=["leo", token]) as socket,
        ):
            socket.receive_text()

        assert closed.value.code == 4403
