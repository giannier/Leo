"""What a tool already sent is not sent again in the model's words (2026-10-27).

The text of a reply was dropped only when it was the same as the body of the buttons or the list. In
other words it went out: gpt-4.1-mini sent the list of packages («Elige el que prefieras») and then
«ya puedes elegir el paquete que prefieras. ¿Cuál te interesa?».
"""

from __future__ import annotations

from decimal import Decimal

import pytest
from sqlalchemy.orm import Session

from app.agent.pipeline import InboundTurn, _repeats
from app.models.business import Product
from app.models.conversation import Message
from app.providers.llm import ChatResult, MockLLMProvider
from tests.integration.test_turn_pipeline import _call, _conversation, _pipeline, _tenant

METHODS = (
    "Estos son nuestros métodos de pago:\n\n• *Yape*\n"
    "Yapea al 987 654 321 a nombre de Tienda Demo SAC"
)


class TestWhatCountsAsRepeating:
    @pytest.mark.parametrize(
        "bubble",
        [
            "Yapea al 987 654 321 a nombre de Tienda Demo SAC.",
            "Estos son nuestros métodos de pago: Yape al 987 654 321 a nombre de Tienda Demo SAC",
        ],
    )
    def test_the_payment_methods_again_are_a_repetition(self, bubble: str) -> None:
        assert _repeats(bubble, [METHODS], after_options=False)

    def test_the_total_and_the_receipt_are_new(self) -> None:
        bubble = (
            "El total es S/ 120.00. Cuando pagues, envíame la captura del comprobante por aquí."
        )

        assert not _repeats(bubble, [METHODS], after_options=False)

    @pytest.mark.parametrize(
        "bubble",
        [
            "Luis, ya puedes elegir el paquete que prefieras. ¿Cuál te interesa?",
            "Elige el que más te guste.",
        ],
    )
    def test_inviting_to_choose_again_after_options_is_a_repetition(self, bubble: str) -> None:
        assert _repeats(
            bubble, ["Estos son los paquetes. Elige el que prefieras."], after_options=True
        )

    def test_the_same_invitation_without_options_is_kept(self) -> None:
        assert not _repeats("¿Cuál te interesa?", [], after_options=False)

    def test_a_price_after_options_is_kept(self) -> None:
        bubble = "El paquete de 5 cuesta S/ 450. ¿Cuál eliges?"

        assert not _repeats(bubble, ["Estos son los paquetes."], after_options=True)


class TestInTheTurn:
    async def test_after_a_list_the_reworded_invitation_is_not_sent(self, db: Session) -> None:
        tenant = _tenant(db, template="productos")
        for name in ("Polo básico", "Gorra urbana"):
            db.add(Product(tenant_id=tenant.id, name=name, price=Decimal("25")))
        db.flush()
        conversation = _conversation(db, tenant)
        llm = MockLLMProvider()
        llm.queue(
            ChatResult(
                content="",
                tool_calls=(
                    _call(
                        "mostrar_opciones", tipo="productos", texto="Estos son nuestros productos."
                    ),
                ),
            )
        )
        llm.queue(ChatResult(content="Ya puedes elegir el que prefieras. ¿Cuál te interesa?"))

        await _pipeline(db, tenant, conversation, llm).run(
            conversation.id, InboundTurn(text="¿qué tienen?")
        )

        sent = (
            db.query(Message)
            .filter(Message.conversation_id == conversation.id, Message.direction == "outbound")
            .all()
        )
        assert [m.msg_type for m in sent] == ["interactive"]

    async def test_a_second_menu_in_the_same_turn_is_not_sent(self, db: Session) -> None:
        """Two option menus in one turn: two messages to read and two places to tap."""
        tenant = _tenant(db, template="productos")
        for name in ("Polo básico", "Gorra urbana"):
            db.add(Product(tenant_id=tenant.id, name=name, price=Decimal("25")))
        db.flush()
        conversation = _conversation(db, tenant)
        llm = MockLLMProvider()
        llm.queue(
            ChatResult(
                content="",
                tool_calls=(_call("mostrar_opciones", tipo="productos", texto="Estos son."),),
            )
        )
        llm.queue(
            ChatResult(
                content="",
                tool_calls=(_call("mostrar_opciones", tipo="productos", texto="O estos."),),
            )
        )
        llm.queue(ChatResult(content=""))

        await _pipeline(db, tenant, conversation, llm).run(
            conversation.id, InboundTurn(text="¿qué tienen?")
        )

        sent = (
            db.query(Message)
            .filter(Message.conversation_id == conversation.id, Message.direction == "outbound")
            .all()
        )
        assert [m.msg_type for m in sent] == ["interactive"]
        assert "Ya le enviaste opciones" in str(llm.chat_calls[-1].messages[-1].content)
