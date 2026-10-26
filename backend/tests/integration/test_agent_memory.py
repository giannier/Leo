"""What the agent remembers: the whole conversation, each message once (Giannier, 2026-10-26).

The model used to see the last twenty messages, and the customer's message of the turn twice: it is
stored before the model is called, so it came in the history and again as the message to answer.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from app.agent.core import (
    AGENT_TEMPERATURE,
    DIGEST_TOKEN_BUDGET,
    HISTORY_TOKEN_BUDGET,
    AgentCore,
)
from app.agent.pipeline import InboundTurn
from app.agent.tools.catalog import buscar_productos
from app.agent.tools.registry import ToolContext
from app.models.conversation import Conversation, Message
from app.models.tenant import Tenant
from app.providers.llm import ChatResult, MockLLMProvider
from tests.integration.test_quote_flow import _product, _quoting_tenant
from tests.integration.test_turn_pipeline import _conversation, _pipeline, _tenant


def _message(
    db: Session, tenant: Tenant, conversation: Conversation, author: str, text: str, **extra: Any
) -> Message:
    row = Message(
        tenant_id=tenant.id,
        conversation_id=conversation.id,
        direction="inbound" if author == "customer" else "outbound",
        author=author,
        content=text,
        msg_type=extra.pop("msg_type", "text"),
        **extra,
    )
    db.add(row)
    db.flush()
    return row


def _said(call: Any, text: str) -> int:
    return sum(text in m.content for m in call.messages if m.role == "user")


class TestTheMessageOfTheTurnGoesOnce:
    async def test_a_stored_message_is_not_repeated_in_the_history(self, db: Session) -> None:
        tenant = _tenant(db, template="productos")
        conversation = _conversation(db, tenant)
        stored = _message(db, tenant, conversation, "customer", "quiero 5 bidones")
        llm = MockLLMProvider()
        llm.queue(ChatResult(content="¿Para qué fecha?"))

        await _pipeline(db, tenant, conversation, llm).run(
            conversation.id, InboundTurn(text="quiero 5 bidones", message_ids=(stored.id,))
        )

        assert _said(llm.chat_calls[0], "quiero 5 bidones") == 1
        assert llm.chat_calls[0].messages[-1].content == "quiero 5 bidones"

    async def test_a_burst_of_several_messages_goes_once_as_one(self, db: Session) -> None:
        tenant = _tenant(db, template="productos")
        conversation = _conversation(db, tenant)
        first = _message(db, tenant, conversation, "customer", "hola")
        second = _message(db, tenant, conversation, "customer", "quiero 5 bidones")
        llm = MockLLMProvider()
        llm.queue(ChatResult(content="¿Para qué fecha?"))

        await _pipeline(db, tenant, conversation, llm).run(
            conversation.id,
            InboundTurn(text="hola\nquiero 5 bidones", message_ids=(first.id, second.id)),
        )

        assert _said(llm.chat_calls[0], "quiero 5 bidones") == 1

    async def test_what_the_customer_wrote_while_the_bot_was_paused_stays(
        self, db: Session
    ) -> None:
        tenant = _tenant(db, template="productos")
        conversation = _conversation(db, tenant)
        _message(db, tenant, conversation, "agent", "Te paso con una persona.")
        _message(db, tenant, conversation, "customer", "soy de Surco, por si acaso")
        current = _message(db, tenant, conversation, "customer", "¿siguen ahí?")
        llm = MockLLMProvider()
        llm.queue(ChatResult(content="Sí, aquí estoy."))

        await _pipeline(db, tenant, conversation, llm).run(
            conversation.id, InboundTurn(text="¿siguen ahí?", message_ids=(current.id,))
        )

        assert _said(llm.chat_calls[0], "soy de Surco") == 1
        assert _said(llm.chat_calls[0], "¿siguen ahí?") == 1

    def test_a_caller_that_does_not_name_the_message_still_gets_it_once(self, db: Session) -> None:
        tenant = _tenant(db, template="productos")
        conversation = _conversation(db, tenant)
        _message(db, tenant, conversation, "agent", "¿Cuántos?")
        _message(db, tenant, conversation, "customer", "quiero 5   bidones")

        history = AgentCore(db, tenant.id, llm=MockLLMProvider()).history(
            conversation.id, current_text="quiero 5 bidones"
        )

        assert [m.content for m in history] == ["¿Cuántos?"]


class TestTheWholeConversation:
    def test_media_is_said_in_words(self, db: Session) -> None:
        tenant = _tenant(db, template="productos")
        conversation = _conversation(db, tenant)
        _message(db, tenant, conversation, "customer", "[imagen]", msg_type="image")
        _message(db, tenant, conversation, "customer", "[audio]", msg_type="audio")

        history = AgentCore(db, tenant.id, llm=MockLLMProvider()).history(conversation.id)

        assert [m.content for m in history] == [
            "[El cliente envió una imagen]",
            "[El cliente envió un audio]",
        ]

    def test_a_very_long_thread_keeps_what_the_customer_said_first_as_a_digest(
        self, db: Session
    ) -> None:
        tenant = _tenant(db, template="productos")
        conversation = _conversation(db, tenant)
        _message(db, tenant, conversation, "customer", "para recoger en Bagua, soy Rosa")
        filler = "x" * 400
        for index in range(HISTORY_TOKEN_BUDGET // 100):
            author = "agent" if index % 2 else "customer"
            _message(db, tenant, conversation, author, f"{index} {filler}")
        _message(db, tenant, conversation, "customer", "¿cuánto es el total?")

        history = AgentCore(db, tenant.id, llm=MockLLMProvider()).history(conversation.id)

        assert history[0].role == "system"
        assert "para recoger en Bagua, soy Rosa" in history[0].content
        assert history[-1].content == "¿cuánto es el total?"
        total = sum(len(m.content) // 4 + 4 for m in history)
        assert total <= HISTORY_TOKEN_BUDGET + DIGEST_TOKEN_BUDGET

    def test_a_normal_conversation_has_no_digest(self, db: Session) -> None:
        tenant = _tenant(db, template="productos")
        conversation = _conversation(db, tenant)
        for index in range(60):
            author = "agent" if index % 2 else "customer"
            _message(db, tenant, conversation, author, f"mensaje {index}")

        history = AgentCore(db, tenant.id, llm=MockLLMProvider()).history(conversation.id)

        assert len(history) == 60 and all(m.role != "system" for m in history)

    async def test_the_agent_answers_at_a_low_temperature(self, db: Session) -> None:
        tenant = _tenant(db, template="productos")
        conversation = _conversation(db, tenant)
        llm = MockLLMProvider()
        llm.queue(ChatResult(content="Hola, ¿en qué te ayudo?"))

        await _pipeline(db, tenant, conversation, llm).run(
            conversation.id, InboundTurn(text="hola")
        )

        assert llm.chat_calls[0].temperature == AGENT_TEMPERATURE == 0.0


class TestTheProductTheCustomerNamed:
    async def test_the_search_says_which_one_when_the_customer_named_one(self, db: Session) -> None:
        tenant = _quoting_tenant(db)
        _product(db, tenant, "Bidon de Agua de 20L", ["cantidad"])
        _product(db, tenant, "Caja de Agua de 20L", ["cantidad"])
        conversation = _conversation(db, tenant)
        _message(db, tenant, conversation, "customer", "bidón 20L")
        context = ToolContext(
            session=db, tenant_id=tenant.id, llm=MockLLMProvider(), conversation_id=conversation.id
        )

        result = await buscar_productos(context, {"query": "20L"})

        assert result.texto is not None
        assert "El cliente ya nombró «Bidon de Agua de 20L»" in result.texto

    async def test_nothing_is_said_when_the_customer_named_neither(self, db: Session) -> None:
        tenant = _quoting_tenant(db)
        _product(db, tenant, "Bidon de Agua de 20L", ["cantidad"])
        _product(db, tenant, "Caja de Agua de 20L", ["cantidad"])
        conversation = _conversation(db, tenant)
        _message(db, tenant, conversation, "customer", "agua de 20 litros")
        context = ToolContext(
            session=db, tenant_id=tenant.id, llm=MockLLMProvider(), conversation_id=conversation.id
        )

        result = await buscar_productos(context, {"query": "20L"})

        assert result.texto is not None and "ya nombró" not in result.texto
