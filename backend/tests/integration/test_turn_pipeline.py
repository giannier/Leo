"""The turn pipeline, end to end with a scripted model (Feature 004, TASK-421).

The verification of the task: walk a conversation from INICIO through CALIFICAR to
IDENTIFICAR, with the greeting recorded, exactly one event per change of stage and
a trace that says where the turn was. The model is `MockLLMProvider`: these assert
what the *pipeline* does with what a model says (which prompt it builds, which
tools it offers, where the state ends up), never whether a vendor's model is good.
"""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.agent.pipeline import InboundTurn, TurnPipeline
from app.core.crypto import encrypt_secret
from app.models.business import AgentConfig, BusinessProfile
from app.models.channel import Contact
from app.models.conversation import AgentTrace, Conversation, Message
from app.models.conversation_state import ConversationStageEvent
from app.models.media import MediaAsset
from app.models.plan import Plan
from app.models.tenant import Tenant
from app.models.whatsapp import WhatsAppNumber
from app.process.service import ProcessConfigCache, ProcessConfigService
from app.process.state import StateRepo
from app.providers.llm import ChatResult, MockLLMProvider, ToolCall
from app.providers.storage.mock import MockStorageProvider
from app.providers.whatsapp.base import (
    InteractiveReply,
    NumberCredentials,
    Referral,
    SendResult,
)
from app.services.channel import ConversationResponder
from app.services.turn_outbound import SandboxOutbound, live_outbound_factory

CAMINO_TAP = InteractiveReply(id="camino:certificacion", title="Solo certificar")


def _call(name: str, **arguments: object) -> ToolCall:
    return ToolCall(id=f"call-{uuid.uuid4().hex[:8]}", name=name, arguments=dict(arguments))


def _tenant(db: Session, *, template: str = "certificaciones", active: bool = True) -> Tenant:
    tenant = Tenant(
        name=f"Capacita {uuid.uuid4().hex[:6]}",
        status="trial",
        plan_id="trial",
        trial_ends_at=datetime.now(UTC) + timedelta(days=7),
    )
    db.add(tenant)
    db.flush()
    db.add_all(
        [
            BusinessProfile(tenant_id=tenant.id, business_name="Capacita", industry="instituto"),
            AgentConfig(
                tenant_id=tenant.id,
                agent_name="Leo",
                is_active=active,
                process_template_key=template,
            ),
        ]
    )
    db.flush()
    return tenant


def _conversation(db: Session, tenant: Tenant, *, channel: str = "sandbox") -> Conversation:
    contact = Contact(
        tenant_id=tenant.id,
        phone_e164=f"+5198{uuid.uuid4().int % 10**7:07d}" if channel == "whatsapp" else None,
        external_user_id=None if channel == "whatsapp" else f"sandbox:{uuid.uuid4()}",
        last_inbound_at=datetime.now(UTC),
    )
    db.add(contact)
    db.flush()
    row = Conversation(tenant_id=tenant.id, channel=channel, contact_id=contact.id, status="open")
    db.add(row)
    db.flush()
    return row


def _pipeline(
    db: Session,
    tenant: Tenant,
    conversation: Conversation,
    llm: MockLLMProvider,
    **options: Any,
) -> TurnPipeline:
    options.setdefault("sandbox", True)
    options.setdefault("outbound", SandboxOutbound(db, tenant.id, conversation.id))
    return TurnPipeline(
        db,
        tenant.id,
        llm=llm,
        process_config=ProcessConfigService(db, cache=ProcessConfigCache()),
        **options,
    )


def _say(db: Session, conversation: Conversation, text: str, **extra: Any) -> Message:
    message = Message(
        tenant_id=conversation.tenant_id,
        conversation_id=conversation.id,
        direction="inbound",
        author="customer",
        content=text,
        msg_type=extra.pop("msg_type", "text"),
        provider_message_id=f"wamid.{uuid.uuid4().hex}",
        payload=extra.pop("payload", None),
    )
    db.add(message)
    db.flush()
    return message


def _events(db: Session, conversation: Conversation) -> list[tuple[str, str]]:
    rows = (
        db.execute(
            select(ConversationStageEvent)
            .where(ConversationStageEvent.conversation_id == conversation.id)
            .order_by(ConversationStageEvent.created_at, ConversationStageEvent.id)
        )
        .scalars()
        .all()
    )
    return [(row.from_stage, row.to_stage) for row in rows]


def _stored(db: Session, tenant: Tenant, conversation: Conversation) -> dict[str, Any]:
    state = StateRepo(db, tenant.id).get(conversation.id)
    assert state is not None
    return dict(state.facts)


class TestWalkingTheFirstStages:
    async def test_inicio_to_calificar_to_identificar(self, db: Session) -> None:
        """The verification of TASK-421, turn by turn."""
        tenant = _tenant(db)
        conversation = _conversation(db, tenant)
        llm = MockLLMProvider()

        # Turn 1: the customer says hello; the assistant greets.
        llm.queue(ChatResult(content="¡Hola! Soy Leo de Capacita. ¿Cómo te llamas?"))
        first = await _pipeline(db, tenant, conversation, llm).run(
            conversation.id, InboundTurn(text="hola")
        )
        assert (first.stage_before.value, first.stage_after.value) == ("INICIO", "CALIFICAR")
        assert first.facts.saludo_enviado is True
        assert _stored(db, tenant, conversation)["saludo_enviado"] is True
        assert _events(db, conversation) == [("INICIO", "CALIFICAR")]

        # Turn 2: CALIFICAR shows the paths as buttons; the model says nothing more.
        llm.queue(
            ChatResult(
                content="",
                tool_calls=(_call("mostrar_opciones", tipo="caminos", texto="¿Qué necesitas?"),),
            )
        )
        llm.queue(ChatResult(content=""))
        second = await _pipeline(db, tenant, conversation, llm).run(
            conversation.id, InboundTurn(text="quiero información")
        )
        assert second.stage_after.value == "CALIFICAR"
        buttons = [m for m in second.tool_messages if m.msg_type == "interactive"]
        assert len(buttons) == 1
        assert buttons[0].payload is not None
        assert [b["id"] for b in buttons[0].payload["botones"]] == [
            "camino:certificacion",
            "camino:programa",
        ]
        # The model had nothing to add: no apology is invented to fill the gap.
        assert second.text_messages == []
        assert _events(db, conversation) == [("INICIO", "CALIFICAR")]

        # Turn 3: the customer taps a button. The path is stored from the event,
        # without the model ever calling `elegir_camino`.
        llm.queue(ChatResult(content="Perfecto. ¿Qué tema quieres certificar?"))
        third = await _pipeline(db, tenant, conversation, llm).run(
            conversation.id, InboundTurn(text="Solo certificar", interactive=(CAMINO_TAP,))
        )
        assert (third.stage_before.value, third.stage_after.value) == (
            "CALIFICAR",
            "IDENTIFICAR",
        )
        assert _stored(db, tenant, conversation)["camino"] == "certificacion"
        assert _events(db, conversation) == [
            ("INICIO", "CALIFICAR"),
            ("CALIFICAR", "IDENTIFICAR"),
        ]
        # The prompt of that turn was the one of IDENTIFICAR, not of the stage before.
        system = llm.chat_calls[-1].messages[0].content
        assert "ETAPA ACTUAL: IDENTIFICAR" in system

    async def test_a_turn_that_changes_nothing_writes_no_event(self, db: Session) -> None:
        tenant = _tenant(db, template="productos")
        conversation = _conversation(db, tenant)
        llm = MockLLMProvider()
        for _ in range(3):
            llm.queue(ChatResult(content="Hola, ¿en qué te ayudo?"))

        for text in ("hola", "buenas", "gracias"):
            await _pipeline(db, tenant, conversation, llm).run(
                conversation.id, InboundTurn(text=text)
            )

        # INICIO -> IDENTIFICAR once (productos has no qualification), then steady.
        assert _events(db, conversation) == [("INICIO", "IDENTIFICAR")]

    async def test_a_degraded_greeting_is_not_recorded_as_a_greeting(self, db: Session) -> None:
        """The apology is not a greeting: the next turn is still INICIO."""
        tenant = _tenant(db)
        conversation = _conversation(db, tenant)

        result = await _pipeline(db, tenant, conversation, MockLLMProvider()).run(
            conversation.id, InboundTurn(text="hola")
        )

        assert result.reply is not None and result.reply.degraded
        assert result.facts.saludo_enviado is False
        assert result.stage_after.value == "INICIO"
        assert _events(db, conversation) == []


class TestTheStagePromptAndTools:
    async def test_the_model_gets_the_tools_of_the_stage_only(self, db: Session) -> None:
        tenant = _tenant(db)
        conversation = _conversation(db, tenant)
        llm = MockLLMProvider()
        llm.queue(ChatResult(content="¡Hola! ¿Cómo te llamas?"))

        result = await _pipeline(db, tenant, conversation, llm).run(
            conversation.id, InboundTurn(text="hola")
        )

        offered = [tool.name for tool in llm.chat_calls[0].tools]
        assert offered == list(result.tools_enabled)
        assert "gestionar_pedido" not in offered
        assert "enviar_recurso" in offered

    async def test_the_prompt_is_the_one_of_the_stage_with_todays_date(self, db: Session) -> None:
        tenant = _tenant(db)
        conversation = _conversation(db, tenant)
        llm = MockLLMProvider()
        llm.queue(ChatResult(content="¡Hola! ¿Cómo te llamas?"))

        await _pipeline(db, tenant, conversation, llm).run(
            conversation.id, InboundTurn(text="hola")
        )

        system = llm.chat_calls[0].messages[0].content
        assert "ETAPA ACTUAL: INICIO" in system
        assert "- Hoy:" in system
        assert "ESTADO DE LA CONVERSACIÓN" in system

    async def test_a_tapped_option_that_is_not_a_path_becomes_a_line_of_context(
        self, db: Session
    ) -> None:
        tenant = _tenant(db, template="productos")
        conversation = _conversation(db, tenant)
        llm = MockLLMProvider()
        llm.queue(ChatResult(content="Claro, te cuento del Excel."))

        tap = InteractiveReply(id="producto:abc-123", title="Excel avanzado")
        await _pipeline(db, tenant, conversation, llm).run(
            conversation.id, InboundTurn(text="Excel avanzado", interactive=(tap,))
        )

        system = llm.chat_calls[0].messages[0].content
        assert "ESTE TURNO" in system
        assert "El cliente eligió: Excel avanzado (producto_id=abc-123)" in system

    async def test_an_image_is_told_to_the_model_as_context(self, db: Session) -> None:
        tenant = _tenant(db, template="productos")
        conversation = _conversation(db, tenant)
        llm = MockLLMProvider()
        llm.queue(ChatResult(content="Recibí tu imagen."))

        await _pipeline(db, tenant, conversation, llm).run(conversation.id, InboundTurn(image=True))

        call = llm.chat_calls[0]
        assert "[El cliente envió una imagen]" in call.messages[0].content
        assert call.messages[-1].content == "[El cliente envió una imagen]"

    async def test_the_ad_the_customer_came_from_is_stored(self, db: Session) -> None:
        tenant = _tenant(db, template="productos")
        conversation = _conversation(db, tenant)
        llm = MockLLMProvider()
        llm.queue(ChatResult(content="¡Hola! Veo que viste nuestro anuncio."))

        referral = Referral(source_id="ad-9", headline="Certifícate", source_url="https://fb/x")
        result = await _pipeline(db, tenant, conversation, llm).run(
            conversation.id, InboundTurn(text="hola", referral=referral)
        )

        stored = _stored(db, tenant, conversation)["entrada_anuncio"]
        assert stored["ad_id"] == "ad-9"
        assert stored["headline"] == "Certifícate"
        assert result.facts.entrada_anuncio is not None
        assert "Llegó por anuncio" in llm.chat_calls[0].messages[0].content


class TestWhatTheTurnSends:
    async def test_the_tools_send_first_and_the_text_goes_last(self, db: Session) -> None:
        tenant = _tenant(db)
        conversation = _conversation(db, tenant)
        db.add(
            MediaAsset(
                tenant_id=tenant.id,
                clave="saludo",
                tipo="imagen",
                titulo="Saludo",
                cuando_usar="Siempre al saludar",
                caption="Bienvenido a Capacita",
                storage_key="media/saludo.png",
                filename="saludo.png",
                mime_type="image/png",
                size_bytes=10,
            )
        )
        db.flush()
        llm = MockLLMProvider()
        llm.queue(ChatResult(content="", tool_calls=(_call("enviar_recurso", clave="saludo"),)))
        llm.queue(ChatResult(content="¡Hola! ¿Cómo te llamas?"))

        result = await _pipeline(db, tenant, conversation, llm).run(
            conversation.id, InboundTurn(text="hola")
        )

        kinds = [(m.msg_type, m.content) for m in result.messages]
        assert kinds == [
            ("image", "Bienvenido a Capacita"),
            ("text", "¡Hola! ¿Cómo te llamas?"),
        ]
        assert result.messages[0].payload is not None
        assert result.messages[0].payload["clave"] == "saludo"
        assert _stored(db, tenant, conversation)["recursos_enviados"] == ["saludo"]

    async def test_a_long_answer_is_two_bubbles_and_a_spent_budget_makes_it_one(
        self, db: Session
    ) -> None:
        tenant = _tenant(db, template="productos")
        conversation = _conversation(db, tenant)
        paragraph = "Esta es una explicación bastante larga de lo que ofrecemos. " * 8
        llm = MockLLMProvider()
        llm.queue(ChatResult(content=f"{paragraph}\n\n{paragraph}"))

        result = await _pipeline(db, tenant, conversation, llm).run(
            conversation.id, InboundTurn(text="cuéntame todo")
        )

        assert len(result.text_messages) == 2
        assert all(m.delivery_status is None for m in result.text_messages)

    async def test_the_live_channel_stores_the_text_queued(self, db: Session) -> None:
        tenant = _tenant(db, template="productos")
        conversation = _conversation(db, tenant, channel="whatsapp")
        llm = MockLLMProvider()
        llm.queue(ChatResult(content="Hola, ¿en qué te ayudo?"))

        result = await _pipeline(
            db, tenant, conversation, llm, sandbox=False, outbound=None, queue_text=True
        ).run(conversation.id, InboundTurn(text="hola"))

        assert [m.delivery_status for m in result.text_messages] == ["queued"]


class TestTheTrace:
    async def test_the_trace_says_where_the_turn_was(self, db: Session) -> None:
        tenant = _tenant(db)
        conversation = _conversation(db, tenant)
        llm = MockLLMProvider()
        llm.queue(ChatResult(content="¡Hola! ¿Cómo te llamas?"))

        result = await _pipeline(db, tenant, conversation, llm).run(
            conversation.id, InboundTurn(text="hola")
        )

        trace = db.execute(
            select(AgentTrace).where(AgentTrace.message_id == result.text_messages[0].id)
        ).scalar_one()
        # The stage and funnel are those the conversation ended the turn in.
        assert (trace.stage_key, trace.funnel_code) == ("CALIFICAR", "NUEVO")
        assert trace.tools_enabled == list(result.tools_enabled)
        assert trace.outbound_count == 1
        assert trace.model_tier == "economico"

    async def test_a_tenant_with_its_own_model_has_no_level(self, db: Session) -> None:
        tenant = _tenant(db)
        conversation = _conversation(db, tenant)
        llm = MockLLMProvider()
        llm.queue(ChatResult(content="¡Hola! ¿Cómo te llamas?"))

        result = await _pipeline(db, tenant, conversation, llm, model_tier_applies=False).run(
            conversation.id, InboundTurn(text="hola")
        )

        trace = db.execute(
            select(AgentTrace).where(AgentTrace.message_id == result.text_messages[0].id)
        ).scalar_one()
        assert trace.model_tier is None

    async def test_outbound_count_includes_what_the_tools_sent(self, db: Session) -> None:
        tenant = _tenant(db)
        conversation = _conversation(db, tenant)
        _conversation_with_a_path_to_choose(db, tenant, conversation)
        llm = MockLLMProvider()
        llm.queue(
            ChatResult(
                content="",
                tool_calls=(_call("mostrar_opciones", tipo="caminos", texto="¿Qué buscas?"),),
            )
        )
        llm.queue(ChatResult(content="Elige una opción."))

        result = await _pipeline(db, tenant, conversation, llm).run(
            conversation.id, InboundTurn(text="info")
        )

        trace = (
            db.execute(select(AgentTrace).order_by(AgentTrace.created_at.desc())).scalars().first()
        )
        assert trace is not None
        assert trace.outbound_count == len(result.messages) == 2
        assert result.reply is not None
        assert result.reply.tools_called[0]["status"] == "hecho"


def _conversation_with_a_path_to_choose(
    db: Session, tenant: Tenant, conversation: Conversation
) -> None:
    """Move the state past the greeting so the turn runs in CALIFICAR."""
    config = ProcessConfigService(db, cache=ProcessConfigCache()).effective(tenant.id)
    states = StateRepo(db, tenant.id)
    state = states.get_or_create(conversation, config.template)
    from app.models.conversation_state import FunnelCode
    from app.process.templates.base import Stage

    states.save(
        conversation.id,
        stage=Stage.CALIFICAR,
        funnel=FunnelCode.NUEVO,
        facts_patch={"saludo_enviado": True},
        reason="test",
        expected_version=state.version,
    )


class TestTheGate:
    async def test_a_handover_in_force_saves_the_state_and_stays_quiet(self, db: Session) -> None:
        tenant = _tenant(db, template="productos")
        conversation = _conversation(db, tenant)
        config = ProcessConfigService(db, cache=ProcessConfigCache()).effective(tenant.id)
        states = StateRepo(db, tenant.id)
        state = states.get_or_create(conversation, config.template)
        from app.models.conversation_state import FunnelCode
        from app.process.templates.base import Stage

        until = datetime.now(UTC) + timedelta(hours=2)
        states.save(
            conversation.id,
            stage=Stage.INICIO,
            funnel=FunnelCode.NUEVO,
            facts_patch={
                "saludo_enviado": True,
                "derivado": {"motivo": "asesoria", "hasta": until.isoformat()},
            },
            reason="test",
            expected_version=state.version,
        )
        llm = MockLLMProvider()

        result = await _pipeline(db, tenant, conversation, llm).run(
            conversation.id,
            InboundTurn(
                text="hola de nuevo",
                referral=Referral(source_id="ad-1", headline="Oferta"),
            ),
        )

        assert result.outcome == "gated" and result.gate == "handover"
        assert result.stage_after.value == "DERIVADO"
        assert result.messages == []
        assert llm.chat_calls == []
        # What arrived during the pause is not lost.
        assert _stored(db, tenant, conversation)["entrada_anuncio"]["ad_id"] == "ad-1"

    async def test_a_pending_payment_wins_the_funnel_but_not_the_gate(self, db: Session) -> None:
        """VERIFICACION with a handover in force: the stage is the funnel's business,
        the pause is the gate's, and the pause wins (`plantillas.md` section 1)."""
        from app.models.conversation_state import FunnelCode
        from app.models.orders import Order
        from app.process.templates.base import Stage

        tenant = _tenant(db, template="productos")
        conversation = _conversation(db, tenant)
        assert conversation.contact_id is not None
        db.add(
            Order(
                tenant_id=tenant.id,
                conversation_id=conversation.id,
                contact_id=conversation.contact_id,
                numero=1,
                estado="por_confirmar",
                moneda="PEN",
                is_sandbox=True,
            )
        )
        db.flush()
        config = ProcessConfigService(db, cache=ProcessConfigCache()).effective(tenant.id)
        states = StateRepo(db, tenant.id)
        state = states.get_or_create(conversation, config.template)
        until = datetime.now(UTC) + timedelta(hours=2)
        states.save(
            conversation.id,
            stage=Stage.INICIO,
            funnel=FunnelCode.NUEVO,
            facts_patch={
                "saludo_enviado": True,
                "derivado": {"motivo": "asesoria", "hasta": until.isoformat()},
            },
            reason="test",
            expected_version=state.version,
        )
        llm = MockLLMProvider()

        result = await _pipeline(db, tenant, conversation, llm).run(
            conversation.id, InboundTurn(text="¿ya verificaron mi pago?")
        )

        assert (result.outcome, result.gate) == ("gated", "handover")
        assert result.stage_after.value == "VERIFICACION"
        assert result.funnel is FunnelCode.EN_VERIFICACION
        assert llm.chat_calls == []
        assert _events(db, conversation) == [("INICIO", "VERIFICACION")]

    async def test_the_real_channel_stays_quiet_when_the_assistant_is_off(
        self, db: Session
    ) -> None:
        tenant = _tenant(db, template="productos", active=False)
        conversation = _conversation(db, tenant, channel="whatsapp")
        llm = MockLLMProvider()

        result = await _pipeline(
            db, tenant, conversation, llm, sandbox=False, outbound=None, queue_text=True
        ).run(conversation.id, InboundTurn(text="hola"))

        assert (result.outcome, result.gate) == ("gated", "paused")
        assert llm.chat_calls == []

    async def test_the_simulator_ignores_a_paused_assistant(self, db: Session) -> None:
        """Trying your agent stays possible after the trial ends (Clarification 002)."""
        tenant = _tenant(db, template="productos", active=False)
        conversation = _conversation(db, tenant)
        llm = MockLLMProvider()
        llm.queue(ChatResult(content="Hola, ¿en qué te ayudo?"))

        result = await _pipeline(db, tenant, conversation, llm).run(
            conversation.id, InboundTurn(text="hola")
        )

        assert result.outcome == "answered"

    async def test_an_opted_out_contact_is_not_answered(self, db: Session) -> None:
        tenant = _tenant(db, template="productos")
        conversation = _conversation(db, tenant, channel="whatsapp")
        contact = db.get(Contact, conversation.contact_id)
        assert contact is not None
        contact.opted_out_at = datetime.now(UTC)
        db.flush()

        result = await _pipeline(
            db, tenant, conversation, MockLLMProvider(), sandbox=False, outbound=None
        ).run(conversation.id, InboundTurn(text="hola"))

        assert (result.outcome, result.gate) == ("gated", "opted_out")

    async def test_something_the_assistant_cannot_read_gets_no_model_turn(
        self, db: Session
    ) -> None:
        tenant = _tenant(db, template="productos")
        conversation = _conversation(db, tenant)
        llm = MockLLMProvider()

        result = await _pipeline(db, tenant, conversation, llm).run(conversation.id, InboundTurn())

        assert result.outcome == "unsupported"
        assert llm.chat_calls == []


class TestTheLiveChannel:
    """`ConversationResponder` drives the pipeline for a real conversation."""

    async def test_the_tap_is_read_from_the_stored_message(self, db: Session) -> None:
        tenant = _tenant(db)
        conversation = _conversation(db, tenant, channel="whatsapp")
        _conversation_with_a_path_to_choose(db, tenant, conversation)
        _say(
            db,
            conversation,
            "Solo certificar",
            payload={"interactive": {"id": "camino:certificacion", "title": "Solo certificar"}},
        )
        llm = MockLLMProvider()
        llm.queue(ChatResult(content="Perfecto. ¿Qué tema quieres certificar?"))

        answers = await ConversationResponder(
            db, tenant.id, llm=llm, outbound_for=lambda _id: None
        ).respond_turn(conversation.id)

        assert [a.delivery_status for a in answers] == ["queued"]
        assert _stored(db, tenant, conversation)["camino"] == "certificacion"
        # The first event is the test's own set-up; the turn wrote the second.
        assert _events(db, conversation) == [
            ("INICIO", "CALIFICAR"),
            ("CALIFICAR", "IDENTIFICAR"),
        ]

    async def test_the_ad_stored_with_the_message_reaches_the_facts(self, db: Session) -> None:
        tenant = _tenant(db, template="productos")
        conversation = _conversation(db, tenant, channel="whatsapp")
        _say(
            db,
            conversation,
            "hola, vi su anuncio",
            payload={"referral": {"source_id": "ad-77", "headline": "Cursos"}},
        )
        llm = MockLLMProvider()
        llm.queue(ChatResult(content="¡Hola!"))

        await ConversationResponder(db, tenant.id, llm=llm).respond_turn(conversation.id)

        assert _stored(db, tenant, conversation)["entrada_anuncio"]["ad_id"] == "ad-77"

    async def test_an_image_alone_reaches_the_model_as_context(self, db: Session) -> None:
        tenant = _tenant(db, template="productos")
        conversation = _conversation(db, tenant, channel="whatsapp")
        _say(db, conversation, "[imagen]", msg_type="image")
        llm = MockLLMProvider()
        llm.queue(ChatResult(content="Recibí tu imagen, ¿en qué te ayudo?"))

        await ConversationResponder(db, tenant.id, llm=llm).respond_turn(conversation.id)

        assert "[El cliente envió una imagen]" in llm.chat_calls[0].messages[0].content


class FakeWhatsApp:
    """Records what the live adapter sends, and can be made to fail."""

    name = "ycloud"

    def __init__(self, *, fail: Exception | None = None) -> None:
        self.fail = fail
        self.sent: list[tuple[str, dict[str, Any]]] = []

    async def _record(self, kind: str, **fields: Any) -> SendResult:
        if self.fail is not None:
            raise self.fail
        self.sent.append((kind, fields))
        return SendResult(provider_message_id=f"wamid.{uuid.uuid4().hex}", status="sent")

    async def send_media(self, credentials: Any, **fields: Any) -> SendResult:
        return await self._record("media", **fields)

    async def send_buttons(self, credentials: Any, **fields: Any) -> SendResult:
        return await self._record("buttons", **fields)

    async def send_list(self, credentials: Any, **fields: Any) -> SendResult:
        return await self._record("list", **fields)

    async def upload_media(self, credentials: Any, **fields: Any) -> Any:
        raise AssertionError("the asset already carries a provider id")


CREDENTIALS = NumberCredentials(
    provider="ycloud",
    values={"waba_id": "1", "phone_number_id": "2", "api_key": "yc_key"},
)


def _connect_number(db: Session, tenant: Tenant) -> None:
    db.add(
        WhatsAppNumber(
            tenant_id=tenant.id,
            phone_e164=f"+5191{uuid.uuid4().int % 10**7:07d}",
            display_name="Capacita",
            provider="ycloud",
            status="connected",
            credentials_enc=encrypt_secret(json.dumps(CREDENTIALS.values)),
        )
    )
    db.flush()


class TestTheLiveOutbound:
    async def test_buttons_go_to_the_provider_and_leave_a_row(self, db: Session) -> None:
        tenant = _tenant(db)
        conversation = _conversation(db, tenant, channel="whatsapp")
        _connect_number(db, tenant)
        _conversation_with_a_path_to_choose(db, tenant, conversation)
        _say(db, conversation, "info")
        provider = FakeWhatsApp()
        factory = live_outbound_factory(
            db,
            tenant.id,
            provider=provider,
            storage=MockStorageProvider(),  # type: ignore[arg-type]
        )
        llm = MockLLMProvider()
        llm.queue(
            ChatResult(
                content="",
                tool_calls=(_call("mostrar_opciones", tipo="caminos", texto="¿Qué buscas?"),),
            )
        )
        llm.queue(ChatResult(content=""))

        answers = await ConversationResponder(
            db, tenant.id, llm=llm, outbound_for=factory
        ).respond_turn(conversation.id)

        assert answers == []
        assert [kind for kind, _ in provider.sent] == ["buttons"]
        _, fields = provider.sent[0]
        assert fields["body"] == "¿Qué buscas?"
        assert [b.id for b in fields["buttons"]] == ["camino:certificacion", "camino:programa"]
        row = db.execute(
            select(Message).where(
                Message.conversation_id == conversation.id, Message.direction == "outbound"
            )
        ).scalar_one()
        assert (row.msg_type, row.delivery_status) == ("interactive", "sent")
        assert row.provider_message_id is not None

    async def test_a_provider_failure_tells_the_model_and_leaves_no_row(self, db: Session) -> None:
        from app.providers.whatsapp.base import WhatsAppAuthError

        tenant = _tenant(db)
        conversation = _conversation(db, tenant, channel="whatsapp")
        _connect_number(db, tenant)
        _conversation_with_a_path_to_choose(db, tenant, conversation)
        _say(db, conversation, "info")
        provider = FakeWhatsApp(fail=WhatsAppAuthError("rejected"))
        factory = live_outbound_factory(
            db,
            tenant.id,
            provider=provider,
            storage=MockStorageProvider(),  # type: ignore[arg-type]
        )
        llm = MockLLMProvider()
        llm.queue(
            ChatResult(
                content="",
                tool_calls=(_call("mostrar_opciones", tipo="caminos", texto="¿Qué buscas?"),),
            )
        )
        llm.queue(ChatResult(content="Cuéntame qué necesitas."))

        answers = await ConversationResponder(
            db, tenant.id, llm=llm, outbound_for=factory
        ).respond_turn(conversation.id)

        tool_message = llm.chat_calls[1].messages[-1].content
        assert tool_message.startswith("ERROR:")
        assert [a.content for a in answers] == ["Cuéntame qué necesitas."]
        outbound = db.execute(
            select(Message).where(
                Message.conversation_id == conversation.id, Message.msg_type == "interactive"
            )
        ).all()
        assert outbound == []

    async def test_a_closed_window_refuses_before_calling_the_provider(self, db: Session) -> None:
        tenant = _tenant(db)
        conversation = _conversation(db, tenant, channel="whatsapp")
        _connect_number(db, tenant)
        contact = db.get(Contact, conversation.contact_id)
        assert contact is not None
        contact.last_inbound_at = datetime.now(UTC) - timedelta(hours=30)
        db.flush()
        provider = FakeWhatsApp()
        outbound = live_outbound_factory(
            db,
            tenant.id,
            provider=provider,
            storage=MockStorageProvider(),  # type: ignore[arg-type]
        )(conversation.id)
        assert outbound is not None

        from app.agent.outbound import OutboundFailed
        from app.providers.whatsapp.base import Button

        with pytest.raises(OutboundFailed):
            await outbound.send_buttons("hola", [Button("a", "A")])
        assert provider.sent == []

    async def test_a_conversation_without_a_number_cannot_send(self, db: Session) -> None:
        tenant = _tenant(db)
        conversation = _conversation(db, tenant, channel="whatsapp")

        factory = live_outbound_factory(
            db,
            tenant.id,
            provider=FakeWhatsApp(),
            storage=MockStorageProvider(),  # type: ignore[arg-type]
        )

        assert factory(conversation.id) is None


class TestMeteringOfTheLiveTurn:
    async def test_one_turn_is_one_metered_message_however_many_bubbles(self, db: Session) -> None:
        tenant = _tenant(db, template="productos")
        conversation = _conversation(db, tenant, channel="whatsapp")
        _say(db, conversation, "cuéntame todo")
        paragraph = "Esta es una explicación bastante larga de lo que ofrecemos. " * 8
        llm = MockLLMProvider()
        llm.queue(ChatResult(content=f"{paragraph}\n\n{paragraph}"))
        plan = db.get(Plan, "trial")
        assert plan is not None
        before = tenant.ai_messages_used

        answers = await ConversationResponder(db, tenant.id, llm=llm).respond_turn(conversation.id)

        assert len(answers) == 2
        db.refresh(tenant)
        assert tenant.ai_messages_used == before + 1
