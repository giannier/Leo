"""Handover with a pause (Feature 004, TASK-443, C-14).

`derivar_a_humano` ends the turn with the business's message, pauses the bot for
the business's hours and tells the business with the summary. While paused nothing
reaches a model and what the customer writes is kept. When the pause runs out (the
`handoff.expire` job) or a person gives the bot back (`resume-bot`), the stage is
recomputed; after an `asesoria` handover ASESORIA does not hand over again on its own.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from httpx import AsyncClient
from sqlalchemy.orm import Session

from app.agent.pipeline import InboundTurn
from app.models.business import AgentConfig
from app.models.conversation import Conversation, Message
from app.models.conversation_state import FunnelCode
from app.models.tenant import Tenant
from app.process.service import ProcessConfigCache, ProcessConfigService
from app.process.state import StateRepo
from app.process.templates.base import Stage
from app.providers.llm import ChatResult, MockLLMProvider
from app.services.handoff import expire_handoffs, expired_handoffs
from tests.integration.test_assistant_config_api import _account
from tests.integration.test_turn_pipeline import (
    _call,
    _conversation,
    _events,
    _pipeline,
    _stored,
    _tenant,
)

DEFAULT_TEXT = (
    "Listo 🙌 Ya le pasé a nuestro equipo lo que necesitas, para que no tengas que repetirlo. "
    "Una persona te escribe por aquí en breve."
)


class RecordingNotices:
    def __init__(self) -> None:
        self.handoffs: list[dict[str, Any]] = []

    def handoff(self, conversation_id: uuid.UUID, *, motivo: str, resumen: str | None) -> None:
        self.handoffs.append(
            {"conversation_id": conversation_id, "motivo": motivo, "resumen": resumen}
        )

    def new_proof(self, *args: Any) -> None: ...

    def cash_order(self, *args: Any) -> None: ...

    def customer_not_notified(self, *args: Any) -> None: ...


def _handover_llm(
    motivo: str = "pide_humano", resumen: str = "Quiere hablar con alguien"
) -> MockLLMProvider:
    llm = MockLLMProvider()
    llm.queue(
        ChatResult(
            content="Claro, ya te paso",
            tool_calls=(_call("derivar_a_humano", motivo=motivo, resumen=resumen),),
        )
    )
    # What a second round would say. The turn must end before it is asked for.
    llm.queue(ChatResult(content="Y además te cuento que..."))
    return llm


def _outbound(db: Session, conversation: Conversation) -> list[str]:
    rows = (
        db.query(Message)
        .filter(Message.conversation_id == conversation.id, Message.direction == "outbound")
        .order_by(Message.created_at)
        .all()
    )
    return [row.content for row in rows]


def _set_facts(db: Session, tenant: Tenant, conversation: Conversation, **facts: Any) -> None:
    config = ProcessConfigService(db, cache=ProcessConfigCache()).effective(tenant.id)
    states = StateRepo(db, tenant.id)
    state = states.get_or_create(conversation, config.template)
    states.save(
        conversation.id,
        stage=Stage(state.stage_key),
        funnel=FunnelCode(state.funnel_code),
        facts_patch=facts,
        reason="test",
        expected_version=state.version,
    )


class TestTheToolEndsTheTurn:
    async def test_the_customer_gets_the_business_message_and_nothing_else(
        self, db: Session
    ) -> None:
        tenant = _tenant(db, template="productos")
        conversation = _conversation(db, tenant)
        llm = _handover_llm()

        result = await _pipeline(db, tenant, conversation, llm).run(
            conversation.id, InboundTurn(text="quiero hablar con una persona")
        )

        assert len(llm.chat_calls) == 1
        assert _outbound(db, conversation) == [DEFAULT_TEXT]
        assert result.stage_after is Stage.DERIVADO
        derivado = _stored(db, tenant, conversation)["derivado"]
        assert derivado["motivo"] == "pide_humano"
        assert derivado["resumen"] == "Quiere hablar con alguien"
        hasta = datetime.fromisoformat(derivado["hasta"])
        assert timedelta(hours=5, minutes=59) < hasta - datetime.now(UTC) <= timedelta(hours=6)

    async def test_the_pause_and_the_message_are_the_business_s(self, db: Session) -> None:
        tenant = _tenant(db, template="productos")
        config = db.query(AgentConfig).filter_by(tenant_id=tenant.id).one()
        config.derivacion_pausa_horas = 2
        config.system_messages = {"derivacion": "Un asesor le escribe en breve."}
        db.flush()
        conversation = _conversation(db, tenant)

        await _pipeline(db, tenant, conversation, _handover_llm()).run(
            conversation.id, InboundTurn(text="tengo una queja")
        )

        assert _outbound(db, conversation) == ["Un asesor le escribe en breve."]
        hasta = datetime.fromisoformat(_stored(db, tenant, conversation)["derivado"]["hasta"])
        assert hasta - datetime.now(UTC) <= timedelta(hours=2)

    async def test_the_business_is_told_on_the_real_channel_only(self, db: Session) -> None:
        tenant = _tenant(db, template="productos")
        live = _conversation(db, tenant, channel="whatsapp")
        simulated = _conversation(db, tenant)
        notices = RecordingNotices()

        await _pipeline(
            db,
            tenant,
            live,
            _handover_llm(),
            sandbox=False,
            outbound=None,
            queue_text=True,
            notices=notices,
        ).run(live.id, InboundTurn(text="quiero un humano"))
        await _pipeline(db, tenant, simulated, _handover_llm(), notices=notices).run(
            simulated.id, InboundTurn(text="quiero un humano")
        )

        assert notices.handoffs == [
            {
                "conversation_id": live.id,
                "motivo": "pide_humano",
                "resumen": "Quiere hablar con alguien",
            }
        ]

    async def test_while_paused_no_model_is_called_and_messages_are_kept(self, db: Session) -> None:
        tenant = _tenant(db, template="productos")
        conversation = _conversation(db, tenant)
        await _pipeline(db, tenant, conversation, _handover_llm()).run(
            conversation.id, InboundTurn(text="quiero un humano")
        )
        quiet = MockLLMProvider()

        result = await _pipeline(db, tenant, conversation, quiet).run(
            conversation.id, InboundTurn(text="¿hola? ¿siguen ahí?")
        )

        assert (result.outcome, result.gate) == ("gated", "handover")
        assert quiet.chat_calls == []
        assert _outbound(db, conversation) == [DEFAULT_TEXT]


class TestThePauseRunsOut:
    async def test_the_job_clears_it_and_recomputes_the_stage(self, db: Session) -> None:
        tenant = _tenant(db, template="productos")
        conversation = _conversation(db, tenant)
        await _pipeline(db, tenant, conversation, _handover_llm()).run(
            conversation.id, InboundTurn(text="quiero un humano")
        )
        later = datetime.now(UTC) + timedelta(hours=7)
        db.commit = db.flush  # type: ignore[method-assign] - stay in the test's transaction

        assert expire_handoffs(db, now=later) >= 1

        assert "derivado" not in _stored(db, tenant, conversation)
        assert _events(db, conversation)[-1] == ("DERIVADO", "IDENTIFICAR")
        state = StateRepo(db, tenant.id).get(conversation.id)
        assert state is not None and state.stage_key == "IDENTIFICAR"

    async def test_a_pause_still_running_is_left_alone(self, db: Session) -> None:
        tenant = _tenant(db, template="productos")
        conversation = _conversation(db, tenant)
        await _pipeline(db, tenant, conversation, _handover_llm()).run(
            conversation.id, InboundTurn(text="quiero un humano")
        )

        pending = expired_handoffs(db, now=datetime.now(UTC) + timedelta(hours=1))

        assert (tenant.id, conversation.id) not in pending
        assert "derivado" in _stored(db, tenant, conversation)

    async def test_after_an_asesoria_handover_asesoria_does_not_hand_over_again(
        self, db: Session
    ) -> None:
        tenant = _tenant(db)
        conversation = _conversation(db, tenant)
        _set_facts(db, tenant, conversation, saludo_enviado=True, camino="programa")
        await _pipeline(
            db, tenant, conversation, _handover_llm("asesoria", "Programa de gestión pública")
        ).run(conversation.id, InboundTurn(text="quiero el programa completo"))
        assert "asesoria_derivada_en" in _stored(db, tenant, conversation)
        db.commit = db.flush  # type: ignore[method-assign]
        expire_handoffs(db, now=datetime.now(UTC) + timedelta(hours=7))

        llm = MockLLMProvider()
        llm.queue(ChatResult(content="Con gusto le respondo."))
        result = await _pipeline(db, tenant, conversation, llm).run(
            conversation.id, InboundTurn(text="una duda más")
        )

        assert result.outcome == "answered"
        assert result.stage_after is Stage.ASESORIA
        prompt = llm.chat_calls[0].messages[0].content
        assert "asesoría ya derivada" in prompt
        assert "asesoria_derivada_en" in _stored(db, tenant, conversation)


class TestTheApi:
    async def _handed_over(
        self, db: Session, api: AsyncClient, mail: Any
    ) -> tuple[Conversation, dict[str, str]]:
        tenant_id, headers = await _account(api, mail)
        tenant = db.get(Tenant, tenant_id)
        assert tenant is not None
        config = db.query(AgentConfig).filter_by(tenant_id=tenant.id).one_or_none()
        if config is None:
            db.add(
                AgentConfig(tenant_id=tenant.id, agent_name="Leo", process_template_key="productos")
            )
            db.flush()
        conversation = _conversation(db, tenant)
        await _pipeline(db, tenant, conversation, _handover_llm()).run(
            conversation.id, InboundTurn(text="quiero un humano")
        )
        return conversation, headers

    async def test_the_state_shows_the_handover(
        self, db: Session, api: AsyncClient, mail: Any
    ) -> None:
        conversation, headers = await self._handed_over(db, api, mail)

        r = await api.get(f"/api/v1/tenant/conversations/{conversation.id}/state", headers=headers)

        assert r.status_code == 200, r.text
        body = r.json()
        assert body["etapa"] == "DERIVADO"
        assert body["derivado"]["motivo"] == "pide_humano"
        assert body["derivado"]["resumen"] == "Quiere hablar con alguien"
        assert body["eventos"][-1]["a"] == "DERIVADO"
        # The facts carry no personal data (C-20).
        assert "nombre_cliente" not in body["hechos"]

    async def test_resume_bot_gives_it_back_with_an_event(
        self, db: Session, api: AsyncClient, mail: Any
    ) -> None:
        conversation, headers = await self._handed_over(db, api, mail)

        r = await api.post(
            f"/api/v1/tenant/conversations/{conversation.id}/resume-bot", headers=headers
        )

        assert r.status_code == 200, r.text
        body = r.json()
        assert body["derivado"] is None
        assert body["etapa"] == "IDENTIFICAR"
        assert body["eventos"][-1]["motivo"] == "operador:reanudar"

    async def test_an_unknown_conversation_is_a_404(self, api: AsyncClient, mail: Any) -> None:
        _, headers = await _account(api, mail)
        r = await api.get(f"/api/v1/tenant/conversations/{uuid.uuid4()}/state", headers=headers)

        assert r.status_code == 404
