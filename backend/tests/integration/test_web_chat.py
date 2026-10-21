"""The web chat: a visitor writes, the assistant answers, no WhatsApp number (019, F)."""

from __future__ import annotations

# ruff: noqa: F811
import uuid
from collections import defaultdict
from contextlib import nullcontext
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from fastapi import FastAPI
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import get_job_queue, get_redis
from app.api.v1 import public_chat
from app.core.config import get_settings
from app.core.rate_limit import RateLimitDecision
from app.models.business import AgentConfig, BusinessProfile
from app.models.conversation import Conversation, Message
from app.models.web_chat import WebChat
from app.providers.llm import ChatResult, MockLLMProvider
from app.services.webchat import WebSession, issue_token, read_token
from app.workers.channel import ANSWER_JOB, answer_conversation
from tests.integration.campaign_fixtures import FakeWhatsApp
from tests.integration.test_channel_pipeline import RecordingQueue
from tests.integration.test_orders_api import operator_for
from tests.unit.test_locks import FakeStore

PANEL = "/api/v1/tenant/web-chat"


class CountingLimiter:
    """A rate limiter that counts in memory, so the tests can hit a limit."""

    counts: dict[str, int] = defaultdict(int)

    def __init__(self, client: object, *, limit: int, window_seconds: int, namespace: str) -> None:
        self._limit, self._namespace = limit, namespace

    async def hit(self, key: str) -> RateLimitDecision:
        slot = f"{self._namespace}:{key}"
        CountingLimiter.counts[slot] += 1
        ok = CountingLimiter.counts[slot] <= self._limit
        return RateLimitDecision(allowed=ok, retry_after_seconds=0 if ok else 30)


@pytest.fixture
def plumbing(app: FastAPI, monkeypatch: pytest.MonkeyPatch) -> tuple[FakeStore, RecordingQueue]:
    store, queue = FakeStore(), RecordingQueue()
    app.dependency_overrides[get_redis] = lambda: store
    app.dependency_overrides[get_job_queue] = lambda: queue
    CountingLimiter.counts.clear()
    monkeypatch.setattr(public_chat, "RedisRateLimiter", CountingLimiter)
    return store, queue


async def _business(
    api: AsyncClient, mail: Any, db: Session, *, enabled: bool = True
) -> tuple[uuid.UUID, dict[str, str], dict[str, str], str]:
    account, owner, operator = await operator_for(api, mail, db)
    tenant_id = uuid.UUID(str(account["tenant_id"]))
    db.add(BusinessProfile(tenant_id=tenant_id, business_name="Panadería Quispe"))
    agent = db.get(AgentConfig, tenant_id)
    if agent is None:
        db.add(AgentConfig(tenant_id=tenant_id, is_active=True, agent_name="Lucía"))
    else:
        agent.is_active = True
        agent.agent_name = "Lucía"
    db.commit()
    chat = (await api.get(PANEL, headers=owner)).json()
    if enabled:
        await api.put(PANEL, headers=owner, json={"enabled": True})
    return tenant_id, owner, operator, chat["public_key"]


async def _visit(api: AsyncClient, key: str) -> str:
    opened = await api.post(f"/api/v1/public/chat/{key}/session")
    assert opened.status_code == 201, opened.text
    return str(opened.json()["token"])


def _says(api: AsyncClient, key: str, token: str, text: str):  # type: ignore[no-untyped-def]
    return api.post(
        f"/api/v1/public/chat/{key}/messages",
        headers={"X-Chat-Session": token},
        json={"texto": text},
    )


def _reads(api: AsyncClient, key: str, token: str, **params: str):  # type: ignore[no-untyped-def]
    return api.get(
        f"/api/v1/public/chat/{key}/messages", headers={"X-Chat-Session": token}, params=params
    )


class TestThePanel:
    async def test_it_starts_off_with_an_address_and_a_snippet(
        self, api: AsyncClient, mail: Any, db: Session, plumbing: Any
    ) -> None:
        _, owner, _, key = await _business(api, mail, db, enabled=False)

        chat = (await api.get(PANEL, headers=owner)).json()

        assert chat["enabled"] is False and chat["public_key"] == key and len(key) >= 20
        assert chat["url"].endswith(f"/chat/{key}") and f'data-chat="{key}"' in chat["snippet"]

    async def test_the_owner_sets_it_up_and_an_operator_cannot(
        self, api: AsyncClient, mail: Any, db: Session, plumbing: Any
    ) -> None:
        _, owner, operator, _ = await _business(api, mail, db, enabled=False)

        refused = await api.put(PANEL, headers=operator, json={"enabled": True})
        saved = await api.put(
            PANEL,
            headers=owner,
            json={
                "enabled": True,
                "saludo": "¡Hola! ¿En qué te ayudo?",
                "color": "#16a34a",
                "limite_diario": 50,
            },
        )
        bad = await api.put(PANEL, headers=owner, json={"color": "verde"})
        cleared = await api.put(PANEL, headers=owner, json={"saludo": ""})

        assert refused.status_code == 403
        assert (
            saved.json()["enabled"]
            and saved.json()["color"] == "#16a34a"
            and saved.json()["limite_diario"] == 50
        )
        assert bad.status_code == 400
        assert cleared.json()["saludo"] is None and cleared.json()["enabled"] is True

    async def test_rotating_the_key_kills_the_old_address(
        self, api: AsyncClient, mail: Any, db: Session, plumbing: Any
    ) -> None:
        _, owner, _, old = await _business(api, mail, db)

        new = (await api.post(f"{PANEL}/rotate", headers=owner)).json()["public_key"]

        assert new != old
        assert (await api.get(f"/api/v1/public/chat/{old}/config")).status_code == 404
        assert (await api.get(f"/api/v1/public/chat/{new}/config")).status_code == 200


class TestTheVisitor:
    async def test_off_or_unknown_is_not_found_and_on_shows_the_business(
        self, api: AsyncClient, mail: Any, db: Session, plumbing: Any
    ) -> None:
        _, owner, _, key = await _business(api, mail, db, enabled=False)

        off = await api.get(f"/api/v1/public/chat/{key}/config")
        unknown = await api.get("/api/v1/public/chat/nada/config")
        no_session = await api.post(f"/api/v1/public/chat/{key}/session")
        await api.put(PANEL, headers=owner, json={"enabled": True, "saludo": "Hola"})
        on = (await api.get(f"/api/v1/public/chat/{key}/config")).json()

        assert off.status_code == unknown.status_code == no_session.status_code == 404
        assert on == {
            "negocio": "Panadería Quispe",
            "asistente": "Lucía",
            "saludo": "Hola",
            "color": "#2563eb",
        }

    async def test_a_message_is_stored_as_a_web_conversation_and_the_answer_is_booked(
        self, api: AsyncClient, mail: Any, db: Session, plumbing: Any
    ) -> None:
        tenant_id, _, _, key = await _business(api, mail, db)
        _, queue = plumbing
        token = await _visit(api, key)

        sent = await _says(api, key, token, "Hola, ¿tienen pan integral?")

        assert sent.status_code == 202 and sent.json()["texto"] == "Hola, ¿tienen pan integral?"
        conversation = db.execute(
            select(Conversation).where(
                Conversation.tenant_id == tenant_id, Conversation.channel == "web"
            )
        ).scalar_one()
        message = db.execute(
            select(Message).where(Message.conversation_id == conversation.id)
        ).scalar_one()
        assert message.direction == "inbound" and message.author == "customer"
        assert [job[0] for job in queue.enqueued] == [ANSWER_JOB]

    async def test_the_assistant_answers_without_any_whatsapp_and_the_visitor_reads_it(
        self,
        api: AsyncClient,
        mail: Any,
        db: Session,
        plumbing: Any,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        tenant_id, _, _, key = await _business(api, mail, db)
        token = await _visit(api, key)
        await _says(api, key, token, "Hola")
        llm = MockLLMProvider(responses=[ChatResult(content="¡Hola! Soy Lucía. ¿Qué buscas?")])
        monkeypatch.setattr("app.workers.channel.chat_provider_for", lambda *_a: (llm, "economico"))
        whatsapp = FakeWhatsApp()
        ctx = {
            "redis": FakeStore(),
            "settings": get_settings(),
            "session_factory": lambda: nullcontext(db),
            "whatsapp": whatsapp,
        }
        conversation = db.execute(
            select(Conversation).where(Conversation.channel == "web")
        ).scalar_one()

        outcome = await answer_conversation(ctx, str(tenant_id), str(conversation.id))
        read = (await _reads(api, key, token)).json()["items"]

        assert outcome == "answered"
        assert whatsapp.texts == [] and whatsapp.templates == []  # nothing went to a provider
        assert [(m["de"], m["texto"]) for m in read] == [
            ("asistente", "¡Hola! Soy Lucía. ¿Qué buscas?")
        ]
        later = (await _reads(api, key, token, after=read[0]["en"])).json()["items"]
        assert later == []

    async def test_a_person_of_the_team_answers_from_the_inbox_and_the_visitor_sees_it(
        self, api: AsyncClient, mail: Any, db: Session, plumbing: Any
    ) -> None:
        tenant_id, owner, _, key = await _business(api, mail, db)
        token = await _visit(api, key)
        await _says(api, key, token, "Quiero hablar con alguien")
        conversation = db.execute(
            select(Conversation).where(Conversation.channel == "web")
        ).scalar_one()

        sent = await api.post(
            f"/api/v1/tenant/conversations/{conversation.id}/messages",
            headers=owner,
            json={"texto": "Hola, soy María, te ayudo yo."},
        )
        read = (await _reads(api, key, token)).json()["items"]

        assert sent.status_code in (200, 201), sent.text
        assert [(m["de"], m["texto"]) for m in read] == [
            ("equipo", "Hola, soy María, te ayudo yo.")
        ]


class TestWhoMayDoWhat:
    async def test_a_visitor_sees_only_their_own_conversation(
        self, api: AsyncClient, mail: Any, db: Session, plumbing: Any
    ) -> None:
        _, _, _, key = await _business(api, mail, db)
        _, _, _, other_key = await _business(api, mail, db)
        mine, theirs = await _visit(api, key), await _visit(api, key)
        await _says(api, key, mine, "Mi mensaje")
        elsewhere = await _visit(api, other_key)

        wrong_chat = await _reads(api, key, elsewhere)  # a token of another business
        forged = await _reads(api, key, mine[:-3] + "xyz")
        none = await _reads(api, key, "")

        assert wrong_chat.status_code == forged.status_code == none.status_code == 401
        assert (await _reads(api, key, theirs)).json()["items"] == []

    def test_tokens_expire_and_cannot_be_forged(self) -> None:
        visitor = WebSession(uuid.uuid4(), uuid.uuid4(), uuid.uuid4())
        old = issue_token(visitor, now=datetime.now(UTC) - timedelta(days=30))
        fresh = issue_token(visitor)

        assert read_token(old) is None
        assert read_token(fresh) == visitor
        body, signature = fresh.split(".")
        assert read_token(f"{body}.{signature[::-1]}") is None and read_token("basura") is None

    async def test_the_message_is_clean_and_bounded(
        self, api: AsyncClient, mail: Any, db: Session, plumbing: Any
    ) -> None:
        _, _, _, key = await _business(api, mail, db)
        token = await _visit(api, key)

        long = await _says(api, key, token, "a" * 501)
        empty = await _says(api, key, token, "   ")
        control = await _says(api, key, token, "hola\x00\x07 mundo")

        assert long.status_code == 400 and empty.status_code == 400
        assert control.json()["texto"] == "hola mundo"


class TestAbuseCosts:
    async def test_a_session_may_not_write_faster_than_a_person(
        self, api: AsyncClient, mail: Any, db: Session, plumbing: Any
    ) -> None:
        _, _, _, key = await _business(api, mail, db)
        token = await _visit(api, key)

        codes = [(await _says(api, key, token, f"mensaje {i}")).status_code for i in range(14)]

        assert codes[:12] == [202] * 12 and codes[12:] == [429, 429]

    async def test_the_business_has_a_daily_cap(
        self, api: AsyncClient, mail: Any, db: Session, plumbing: Any
    ) -> None:
        _, owner, _, key = await _business(api, mail, db)
        await api.put(PANEL, headers=owner, json={"limite_diario": 10})
        codes = []
        for _ in range(4):  # several visitors, three messages each
            token = await _visit(api, key)
            codes += [(await _says(api, key, token, "hola")).status_code for _ in range(3)]

        assert codes.count(202) == 10 and codes.count(429) == 2

    async def test_an_address_may_only_open_so_many_sessions(
        self, api: AsyncClient, mail: Any, db: Session, plumbing: Any
    ) -> None:
        _, _, _, key = await _business(api, mail, db)

        codes = [
            (await api.post(f"/api/v1/public/chat/{key}/session")).status_code for _ in range(12)
        ]

        assert codes[:10] == [201] * 10 and codes[10:] == [429, 429]


def test_the_web_chat_row_is_a_business_row(db: Session) -> None:
    assert WebChat.__tablename__ == "web_chats"
