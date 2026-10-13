"""The history the model reads hides a DNI already registered (TASK-451, decision 18)."""

from __future__ import annotations

from sqlalchemy.orm import Session

from app.agent.pipeline import InboundTurn
from app.models.conversation import Message
from app.providers.llm import ChatResult, MockLLMProvider
from tests.integration.test_proof_pipeline import World


def _say(world: World, text: str) -> None:
    world.db.add(
        Message(
            tenant_id=world.tenant.id,
            conversation_id=world.conversation.id,
            direction="inbound",
            author="customer",
            content=text,
        )
    )
    world.db.flush()


async def test_a_registered_dni_never_reaches_the_model_again(db: Session) -> None:
    world = World(db)
    _say(world, "Mi DNI es 12345678")
    world.contact.custom_data = {"nombres": "Ana", "dni": "12345678"}
    db.flush()
    llm = MockLLMProvider(responses=[ChatResult(content="Gracias, Ana.")])

    await world.pipeline(llm).run(world.conversation.id, InboundTurn(text="¿Y ahora qué sigue?"))

    sent = "\n".join(m.content for m in llm.chat_calls[0].messages)
    assert "12345678" not in sent
    assert "[DNI registrado]" in sent


async def test_a_dni_not_yet_registered_is_still_there(db: Session) -> None:
    """Before `registrar_datos_cliente` stores it the model needs the number."""
    world = World(db)
    _say(world, "Mi DNI es 87654321")
    llm = MockLLMProvider(responses=[ChatResult(content="Lo registro.")])

    await world.pipeline(llm).run(world.conversation.id, InboundTurn(text="¿Lo anotaste?"))

    assert "87654321" in "\n".join(m.content for m in llm.chat_calls[0].messages)
