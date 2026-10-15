"""The inbox, through the API and the pipeline (Feature 007, TASK-703 to TASK-706).

Takeover with its lock and the agent quiet at once, including a reply the model was
still writing; a person's message (window, author, the agent reading it later);
return and steal; notes, close, reopen and opt-out; the list's filters, search,
unread counter and cursor; the auto-return and the unattended-queue email.
"""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.agent.core import AgentCore
from app.agent.pipeline import InboundTurn
from app.models.audit import AuditLog
from app.models.business import AgentConfig
from app.models.channel import Contact
from app.models.conversation import Conversation, Message
from app.models.conversation_state import FunnelCode
from app.models.inbox import NotificationSettings
from app.models.tenant import Tenant
from app.process.service import ProcessConfigCache, ProcessConfigService
from app.process.state import StateRepo
from app.process.templates.base import Stage
from app.providers.llm import ChatResult, MockLLMProvider
from app.services.channel import ConversationResponder
from app.services.inbox import Inbox, discard_replies, reply_still_wanted
from app.services.inbox_jobs import notify_unattended, return_idle_takeovers
from tests.integration.test_orders_api import FakeWhatsApp, operator_for, whatsapp  # noqa: F401
from tests.integration.test_turn_pipeline import _pipeline

C = "/api/v1/tenant/conversations"


def _thread(
    db: Session,
    tenant_id: uuid.UUID,
    *,
    name: str = "Rosa Díaz",
    window_hours_ago: float = 1,
    said: str = "Hola, ¿tienen polos?",
) -> Conversation:
    config = db.get(AgentConfig, tenant_id)
    if config is None:
        config = AgentConfig(
            tenant_id=tenant_id, agent_name="Leo", process_template_key="productos"
        )
        db.add(config)
    # Active, so a quiet agent is quiet because of the takeover and not because it is off.
    config.is_active = True
    contact = Contact(
        tenant_id=tenant_id,
        phone_e164=f"+5198{uuid.uuid4().int % 10**7:07d}",
        profile_name=name,
        last_inbound_at=datetime.now(UTC) - timedelta(hours=window_hours_ago),
    )
    db.add(contact)
    db.flush()
    conversation = Conversation(tenant_id=tenant_id, channel="whatsapp", contact_id=contact.id)
    db.add(conversation)
    db.flush()
    from app.repositories.conversation import MessageRepository

    MessageRepository(db, tenant_id).append(
        conversation_id=conversation.id,
        direction="inbound",
        author="customer",
        content=said,
        provider_message_id=f"wamid.{uuid.uuid4().hex}",
    )
    return conversation


def _hand_over(
    db: Session, tenant_id: uuid.UUID, conversation: Conversation, *, minutes_ago: int
) -> None:
    config = ProcessConfigService(db, cache=ProcessConfigCache()).effective(tenant_id)
    states = StateRepo(db, tenant_id)
    state = states.get_or_create(conversation, config.template)
    since = datetime.now(UTC) - timedelta(minutes=minutes_ago)
    states.save(
        conversation.id,
        stage=Stage.DERIVADO,
        funnel=FunnelCode.DERIVADO,
        facts_patch={
            "saludo_enviado": True,
            "derivado": {
                "motivo": "queja",
                "resumen": "Pedido incompleto",
                "desde": since.isoformat(),
                "hasta": (since + timedelta(hours=6)).isoformat(),
            },
        },
        reason="test",
        expected_version=state.version,
    )


async def _team(
    api: AsyncClient, mail: Any, db: Session
) -> tuple[uuid.UUID, dict[str, str], dict[str, str]]:
    account, owner, operator = await operator_for(api, mail, db)
    return uuid.UUID(account["tenant_id"]), owner, operator


# ------------------------------------------------------------------ takeover


class TestTakeover:
    async def test_one_person_wins_the_other_is_told_who(
        self, api: AsyncClient, mail: Any, db: Session
    ) -> None:
        tenant_id, owner, operator = await _team(api, mail, db)
        conversation = _thread(db, tenant_id)

        first = await api.post(f"{C}/{conversation.id}/takeover", headers=operator)
        second = await api.post(f"{C}/{conversation.id}/takeover", headers=owner)

        assert first.status_code == 200, first.text
        assert first.json()["tomada_por"]["nombre"] == "Bruno"
        assert second.status_code == 409
        assert second.json()["error"] == "already_taken"
        assert second.json()["fields"]["by"] == "Bruno"

    async def test_while_taken_the_agent_is_quiet_and_spends_no_quota(
        self, db: Session, api: AsyncClient, mail: Any
    ) -> None:
        tenant_id, _, operator = await _team(api, mail, db)
        conversation = _thread(db, tenant_id)
        await api.post(f"{C}/{conversation.id}/takeover", headers=operator)
        tenant = db.get(Tenant, tenant_id)
        assert tenant is not None
        before = tenant.ai_messages_used
        llm = MockLLMProvider()

        answers = await ConversationResponder(db, tenant_id, llm=llm).respond_turn(conversation.id)

        assert answers == []
        assert llm.chat_calls == []
        db.refresh(tenant)
        assert tenant.ai_messages_used == before

    async def test_a_reply_the_model_was_still_writing_is_never_sent(
        self, db: Session, api: AsyncClient, mail: Any
    ) -> None:
        """The CancelGuard: somebody takes it while the model generates."""
        tenant_id, _, _ = await _team(api, mail, db)
        conversation = _thread(db, tenant_id)
        user = db.execute(
            select(Tenant).where(Tenant.id == tenant_id)
        ).scalar_one()  # the owner below is any member; the service takes a User
        from app.models.tenant import TenantUser
        from app.models.user import User

        member = (
            db.execute(select(TenantUser).where(TenantUser.tenant_id == tenant_id))
            .scalars()
            .first()
        )
        assert member is not None and user is not None
        person = db.get(User, member.user_id)
        assert person is not None

        class TakesOverMidway(MockLLMProvider):
            async def chat(self, messages, **kwargs):  # type: ignore[no-untyped-def,override]
                Inbox(db, tenant_id).take(conversation.id, person)
                return await super().chat(messages, **kwargs)

        llm = TakesOverMidway()
        llm.queue(ChatResult(content="¡Hola! Sí, tenemos polos."))

        answers = await ConversationResponder(db, tenant_id, llm=llm).respond_turn(conversation.id)

        assert answers and not reply_still_wanted(db, conversation.id)
        discard_replies(db, answers)
        assert all(a.delivery_status == "failed" for a in answers)
        assert all((a.payload or {}).get("descartado") == "takeover" for a in answers)

    async def test_a_handed_over_conversation_leaves_the_queue_when_taken(
        self, api: AsyncClient, mail: Any, db: Session
    ) -> None:
        tenant_id, owner, operator = await _team(api, mail, db)
        conversation = _thread(db, tenant_id)
        _hand_over(db, tenant_id, conversation, minutes_ago=3)
        waiting = (await api.get(f"{C}?filtro=necesitan_humano", headers=owner)).json()
        assert [i["id"] for i in waiting["items"]] == [str(conversation.id)]
        assert waiting["esperando"] == 1

        await api.post(f"{C}/{conversation.id}/takeover", headers=operator)

        after = (await api.get(f"{C}?filtro=necesitan_humano", headers=owner)).json()
        assert after["items"] == [] and after["esperando"] == 0
        state = StateRepo(db, tenant_id).get(conversation.id)
        assert state is not None and "derivado" not in state.facts

    async def test_return_steal_and_who_may(self, api: AsyncClient, mail: Any, db: Session) -> None:
        tenant_id, owner, operator = await _team(api, mail, db)
        conversation = _thread(db, tenant_id)
        await api.post(f"{C}/{conversation.id}/takeover", headers=operator)

        operator_steals = await api.post(f"{C}/{conversation.id}/steal", headers=owner.copy() | {})
        assert operator_steals.status_code == 200  # the owner may
        assert operator_steals.json()["tomada_por"]["nombre"] != "Bruno"
        not_holder = await api.post(f"{C}/{conversation.id}/release", headers=operator)
        assert not_holder.status_code == 409 and not_holder.json()["error"] == "not_holder"
        stolen_by_operator = await api.post(f"{C}/{conversation.id}/steal", headers=operator)
        assert stolen_by_operator.status_code == 403

        released = await api.post(f"{C}/{conversation.id}/release", headers=owner)

        assert released.json()["tomada_por"] is None
        thread = (await api.get(f"{C}/{conversation.id}/messages", headers=owner)).json()
        events = [m["evento"] for m in thread["items"] if m["tipo"] == "event"]
        assert events == ["tomada", "robada", "devuelta"]


# ------------------------------------------------------------------ writing


class TestAPersonWrites:
    async def test_writing_takes_it_and_the_agent_later_reads_it(
        self,
        api: AsyncClient,
        mail: Any,
        db: Session,
        whatsapp: FakeWhatsApp,  # noqa: F811
    ) -> None:
        tenant_id, _, operator = await _team(api, mail, db)
        conversation = _thread(db, tenant_id)

        sent = await api.post(
            f"{C}/{conversation.id}/messages",
            headers=operator,
            json={"texto": "Hola Rosa, te ayudo yo"},
        )

        assert sent.status_code == 200, sent.text
        body = sent.json()
        assert body["mensaje"]["autor"] == "human_operator"
        assert body["mensaje"]["operador"] == "Bruno"
        detail = (await api.get(f"{C}/{conversation.id}", headers=operator)).json()
        assert detail["tomada_por"]["nombre"] == "Bruno"
        history = AgentCore(db, tenant_id, llm=MockLLMProvider()).history(conversation.id)
        assert ("assistant", "Hola Rosa, te ayudo yo") in [(m.role, m.content) for m in history]

    async def test_outside_the_24_hour_window_it_is_refused(
        self, api: AsyncClient, mail: Any, db: Session
    ) -> None:
        tenant_id, _, operator = await _team(api, mail, db)
        conversation = _thread(db, tenant_id, window_hours_ago=25)

        response = await api.post(
            f"{C}/{conversation.id}/messages", headers=operator, json={"texto": "Hola"}
        )
        detail = (await api.get(f"{C}/{conversation.id}", headers=operator)).json()

        assert response.status_code == 403
        assert response.json()["error"] == "outside_24h_window"
        assert detail["ventana"]["abierta"] is False

    async def test_someone_else_s_conversation_cannot_be_written_to(
        self, api: AsyncClient, mail: Any, db: Session
    ) -> None:
        tenant_id, owner, operator = await _team(api, mail, db)
        conversation = _thread(db, tenant_id)
        await api.post(f"{C}/{conversation.id}/takeover", headers=owner)

        response = await api.post(
            f"{C}/{conversation.id}/messages", headers=operator, json={"texto": "Yo"}
        )

        assert response.status_code == 409


# ------------------------------------------------------------------ actions


class TestActions:
    async def test_notes_close_reopen_and_opt_out(
        self, api: AsyncClient, mail: Any, db: Session
    ) -> None:
        tenant_id, owner, operator = await _team(api, mail, db)
        conversation = _thread(db, tenant_id)

        note = await api.post(
            f"{C}/{conversation.id}/notes", headers=operator, json={"texto": "Cliente VIP"}
        )
        notes = (await api.get(f"{C}/{conversation.id}/notes", headers=owner)).json()["items"]
        closed = await api.post(f"{C}/{conversation.id}/close", headers=operator)
        reopened = await api.post(f"{C}/{conversation.id}/reopen", headers=operator)
        opted = await api.post(f"{C}/{conversation.id}/opt-out", headers=operator)

        assert note.status_code == 201
        assert [(n["autor"], n["texto"]) for n in notes] == [("Bruno", "Cliente VIP")]
        assert closed.json()["estado"] == "closed"
        assert reopened.json()["estado"] == "open"
        assert opted.json()["contacto"]["opt_out"] is True
        audit = (
            db.execute(
                select(AuditLog).where(
                    AuditLog.tenant_id == tenant_id, AuditLog.action == "contact.opt_out"
                )
            )
            .scalars()
            .all()
        )
        assert len(audit) == 1

    async def test_reopen_refuses_when_the_customer_has_another_open_thread(
        self, api: AsyncClient, mail: Any, db: Session
    ) -> None:
        tenant_id, owner, _ = await _team(api, mail, db)
        old = _thread(db, tenant_id)
        await api.post(f"{C}/{old.id}/close", headers=owner)
        newer = Conversation(tenant_id=tenant_id, channel="whatsapp", contact_id=old.contact_id)
        db.add(newer)
        db.flush()

        response = await api.post(f"{C}/{old.id}/reopen", headers=owner)

        assert response.status_code == 409 and response.json()["error"] == "another_open"


# ------------------------------------------------------------------ the list


class TestTheList:
    async def test_filters_unread_search_and_cursor(
        self, api: AsyncClient, mail: Any, db: Session
    ) -> None:
        tenant_id, owner, operator = await _team(api, mail, db)
        rosa = _thread(db, tenant_id, name="Rosa Díaz", said="Quiero devolver mi mochila")
        luis = _thread(db, tenant_id, name="Luis Pérez", said="¿Hacen envíos a Callao?")
        ana = _thread(db, tenant_id, name="Ana Quispe", said="Hola")
        await api.post(f"{C}/{ana.id}/takeover", headers=operator)

        everything = (await api.get(C, headers=owner)).json()["items"]
        taken = (await api.get(f"{C}?filtro=tomadas", headers=owner)).json()["items"]
        mine = (await api.get(f"{C}?filtro=mias", headers=operator)).json()["items"]
        by_word = (await api.get(f"{C}?q=mochilas", headers=owner)).json()["items"]
        by_name = (await api.get(f"{C}?q=Pérez", headers=owner)).json()["items"]
        page = await api.get(f"{C}", headers=owner, params={})
        first = (await api.get(C, headers=owner)).json()

        ids = [i["id"] for i in everything]
        assert {str(rosa.id), str(luis.id), str(ana.id)} <= set(ids)
        assert [i["id"] for i in taken] == [str(ana.id)]
        assert [i["id"] for i in mine] == [str(ana.id)]
        assert [i["id"] for i in by_word] == [str(rosa.id)]  # Spanish stemming: mochilas ~ mochila
        assert [i["id"] for i in by_name] == [str(luis.id)]
        assert page.status_code == 200 and first["siguiente"] is None
        unread = {i["id"]: i["no_leidos"] for i in everything}
        assert unread[str(rosa.id)] == 1

        await api.get(f"{C}/{rosa.id}/messages", headers=owner)
        after = {i["id"]: i["no_leidos"] for i in (await api.get(C, headers=owner)).json()["items"]}
        assert after[str(rosa.id)] == 0

    async def test_the_cursor_walks_every_conversation_once(
        self, api: AsyncClient, mail: Any, db: Session, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from app.services import inbox_queries

        monkeypatch.setattr(inbox_queries, "PAGE", 2)
        tenant_id, owner, _ = await _team(api, mail, db)
        made = {str(_thread(db, tenant_id, name=f"Cliente {i}").id) for i in range(5)}

        seen: list[str] = []
        cursor: str | None = None
        for _ in range(5):
            params = {"cursor": cursor} if cursor else {}
            body = (await api.get(C, headers=owner, params=params)).json()
            seen.extend(i["id"] for i in body["items"])
            cursor = body["siguiente"]
            if cursor is None:
                break

        assert sorted(seen) == sorted(made) and len(seen) == len(set(seen))


# ------------------------------------------------------------------ jobs


class TestJobs:
    async def test_an_idle_takeover_returns_and_never_means_never(
        self,
        api: AsyncClient,
        mail: Any,
        db: Session,
        monkeypatch: pytest.MonkeyPatch,
        inbox_events: list[tuple[str, str]],
    ) -> None:
        monkeypatch.setattr(db, "commit", db.flush)
        tenant_id, _, operator = await _team(api, mail, db)
        conversation = _thread(db, tenant_id)
        await api.post(f"{C}/{conversation.id}/takeover", headers=operator)
        soon = datetime.now(UTC) + timedelta(minutes=26)
        later = datetime.now(UTC) + timedelta(minutes=31)

        return_idle_takeovers(db, now=soon)
        warned = [json.loads(d)["type"] for _, d in inbox_events]
        db.refresh(conversation)
        assert conversation.takeover_user_id is not None
        assert "takeover.expiring" in warned

        return_idle_takeovers(db, now=later)
        db.refresh(conversation)
        assert conversation.takeover_user_id is None

        await api.post(f"{C}/{conversation.id}/takeover", headers=operator)
        db.add(
            NotificationSettings(
                tenant_id=tenant_id, unattended_queue_minutes=10, auto_return_minutes=None
            )
        )
        db.flush()
        return_idle_takeovers(db, now=datetime.now(UTC) + timedelta(days=2))
        db.refresh(conversation)
        assert conversation.takeover_user_id is not None

    async def test_an_unattended_handover_is_emailed_once(
        self, api: AsyncClient, mail: Any, db: Session, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(db, "commit", db.flush)
        tenant_id, _, _ = await _team(api, mail, db)
        fresh = _thread(db, tenant_id)
        waiting = _thread(db, tenant_id)
        _hand_over(db, tenant_id, fresh, minutes_ago=3)
        _hand_over(db, tenant_id, waiting, minutes_ago=12)

        emails = notify_unattended(db)
        again = notify_unattended(db)

        assert len(emails) == 1 and "sigue esperando" in emails[0].subject
        assert again == []
        state = StateRepo(db, tenant_id).get(waiting.id)
        assert state is not None and state.facts["derivado"]["avisado"] is True


class TestSettings:
    async def test_never_is_kept_on_the_first_save(
        self, api: AsyncClient, mail: Any, db: Session
    ) -> None:
        """A new row with "never" must not fall back to the database's 30."""
        _, owner, operator = await _team(api, mail, db)

        saved = await api.put(
            "/api/v1/tenant/notification-settings",
            headers=owner,
            json={"unattended_queue_minutes": 15, "auto_return_minutes": None},
        )
        read = await api.get("/api/v1/tenant/notification-settings", headers=operator)
        refused = await api.put(
            "/api/v1/tenant/notification-settings",
            headers=operator,
            json={"unattended_queue_minutes": 5, "auto_return_minutes": 30},
        )

        assert saved.json() == {"unattended_queue_minutes": 15, "auto_return_minutes": None}
        assert read.json() == {"unattended_queue_minutes": 15, "auto_return_minutes": None}
        assert refused.status_code == 403


class TestThePipelineGate:
    async def test_the_simulator_respects_a_takeover_too(
        self, db: Session, api: AsyncClient, mail: Any
    ) -> None:
        tenant_id, _, _ = await _team(api, mail, db)
        from app.models.tenant import TenantUser
        from app.models.user import User

        member = (
            db.execute(select(TenantUser).where(TenantUser.tenant_id == tenant_id))
            .scalars()
            .first()
        )
        assert member is not None
        person = db.get(User, member.user_id)
        assert person is not None
        tenant = db.get(Tenant, tenant_id)
        assert tenant is not None
        if db.get(AgentConfig, tenant_id) is None:
            db.add(
                AgentConfig(tenant_id=tenant_id, agent_name="Leo", process_template_key="productos")
            )
        conversation = Conversation(tenant_id=tenant_id, channel="sandbox", status="open")
        db.add(conversation)
        db.flush()
        Inbox(db, tenant_id).take(conversation.id, person)
        llm = MockLLMProvider()

        result = await _pipeline(db, tenant, conversation, llm).run(
            conversation.id, InboundTurn(text="hola")
        )

        assert (result.outcome, result.gate) == ("gated", "takeover")
        assert llm.chat_calls == []
        events = (
            db.execute(select(Message).where(Message.conversation_id == conversation.id))
            .scalars()
            .all()
        )
        assert [m.msg_type for m in events] == ["event"]


class TestTheThreadCitesItsSources:
    async def test_an_agent_reply_shows_the_documents_it_used(
        self, api: AsyncClient, mail: Any, db: Session
    ) -> None:
        """Feature 006, HU-05: "respondió usando: Política de envíos"."""
        from app.models.conversation import AgentTrace
        from app.repositories.conversation import MessageRepository

        tenant_id, owner, _ = await _team(api, mail, db)
        conversation = _thread(db, tenant_id, name="Rosa", said="¿Cuánto cuesta el envío?")
        reply = MessageRepository(db, tenant_id).append(
            conversation_id=conversation.id,
            direction="outbound",
            author="agent",
            content="El envío cuesta 10 soles.",
        )
        db.add(
            AgentTrace(
                tenant_id=tenant_id,
                message_id=reply.id,
                model="mock",
                rag_sources=[
                    {
                        "tool": "consultar_conocimiento",
                        "consulta": "envío",
                        "fuentes": ["Política de envíos", "Mi web"],
                    },
                    {"tool": "consultar_conocimiento", "fuentes": ["Política de envíos"]},
                ],
            )
        )
        db.flush()

        thread = (await api.get(f"{C}/{conversation.id}/messages", headers=owner)).json()["items"]

        by_author = {m["autor"]: m for m in thread}
        assert by_author["agent"]["fuentes"] == ["Política de envíos", "Mi web"]
        assert by_author["customer"]["fuentes"] == []
