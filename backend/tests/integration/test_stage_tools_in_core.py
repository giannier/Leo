"""The agent loop with a stage registry (TASK-417).

`AgentCore` takes a registry; the pipeline (TASK-421) will hand it the one
`for_stage` builds. These tests drive the loop with that registry to prove the
parts the loop owns: the envelope reaches the model as text, the old catalogue
tool name is still served, and the trace and the sources read the status.
"""

from __future__ import annotations

import uuid
from decimal import Decimal

import pytest
from sqlalchemy.orm import Session

from app.agent.core import AgentCore
from app.agent.tools.definitions import registry_for_stage
from app.agent.tools.registry import ToolRegistry
from app.models.business import AgentConfig, BusinessProfile, Product
from app.models.conversation import Conversation
from app.models.tenant import Tenant
from app.process.facts import Facts
from app.process.templates import TEMPLATES
from app.process.templates.base import Stage
from app.providers.llm import ChatResult, MockLLMProvider, ToolCall

SHOP = TEMPLATES["productos"]


@pytest.fixture
def tenant(db: Session) -> Tenant:
    row = Tenant(name="Tienda Quispe")
    db.add(row)
    db.flush()
    db.add_all(
        [
            BusinessProfile(tenant_id=row.id, business_name="Tienda Quispe", industry="retail"),
            AgentConfig(tenant_id=row.id, agent_name="Leo"),
            Product(tenant_id=row.id, name="Lomo saltado", price=Decimal("32.00")),
        ]
    )
    db.flush()
    return row


@pytest.fixture
def conversation(db: Session, tenant: Tenant) -> Conversation:
    row = Conversation(tenant_id=tenant.id, channel="sandbox")
    db.add(row)
    db.flush()
    return row


def _registry(stage: Stage = Stage.IDENTIFICAR) -> ToolRegistry:
    return registry_for_stage(SHOP, stage, Facts(), SHOP.resolve())


def _call(name: str, **arguments: object) -> ToolCall:
    return ToolCall(id=f"call-{uuid.uuid4().hex[:8]}", name=name, arguments=dict(arguments))


async def _run(
    db: Session,
    tenant: Tenant,
    conversation: Conversation,
    llm: MockLLMProvider,
    registry: ToolRegistry,
) -> object:
    return await AgentCore(db, tenant.id, llm=llm, registry=registry).respond(
        conversation_id=conversation.id, customer_message="hola"
    )


class TestTheEnvelopeReachesTheModel:
    async def test_a_search_result_is_text_with_its_status(
        self, db: Session, tenant: Tenant, conversation: Conversation
    ) -> None:
        llm = MockLLMProvider(
            responses=[
                ChatResult(content="", tool_calls=(_call("buscar_productos", consulta="lomo"),)),
                ChatResult(content="El lomo cuesta S/ 32.00."),
            ]
        )

        reply = await AgentCore(db, tenant.id, llm=llm, registry=_registry()).respond(
            conversation_id=conversation.id, customer_message="cuánto cuesta el lomo"
        )

        tool_message = next(m for m in llm.chat_calls[1].messages if m.role == "tool")
        assert tool_message.content.startswith("estado: hecho\nProductos disponibles:")
        assert "Lomo saltado" in tool_message.content
        assert reply.tools_called[0]["ok"] is True

    async def test_the_model_is_offered_the_new_names_only(
        self, db: Session, tenant: Tenant, conversation: Conversation
    ) -> None:
        llm = MockLLMProvider(responses=[ChatResult(content="Hola")])

        await _run(db, tenant, conversation, llm, _registry())

        offered = {spec.name for spec in llm.chat_calls[0].tools}
        assert "buscar_productos" in offered
        assert "consultar_catalogo" not in offered

    async def test_a_stub_answers_not_available_and_is_not_a_failure(
        self, db: Session, tenant: Tenant, conversation: Conversation
    ) -> None:
        llm = MockLLMProvider(
            responses=[
                ChatResult(
                    content="",
                    tool_calls=(_call("derivar_a_humano", motivo="otro", resumen="x"),),
                ),
                ChatResult(content="Sigo con tu consulta."),
            ]
        )

        reply = await AgentCore(db, tenant.id, llm=llm, registry=_registry()).respond(
            conversation_id=conversation.id, customer_message="mi pedido"
        )

        tool_message = next(m for m in llm.chat_calls[1].messages if m.role == "tool")
        assert tool_message.content.startswith("estado: no_disponible")
        assert reply.tools_called[0]["ok"] is True

    async def test_an_error_status_is_recorded_as_a_failed_call(
        self, db: Session, tenant: Tenant, conversation: Conversation
    ) -> None:
        llm = MockLLMProvider(
            responses=[
                ChatResult(content="", tool_calls=(_call("ver_producto", producto_id="nope"),)),
                ChatResult(content="No encontré ese producto."),
            ]
        )

        reply = await AgentCore(db, tenant.id, llm=llm, registry=_registry()).respond(
            conversation_id=conversation.id, customer_message="ese"
        )

        assert reply.tools_called[0]["ok"] is False


class TestTheOldCatalogueToolStillWorks:
    async def test_a_call_to_the_old_name_is_served_by_the_search(
        self, db: Session, tenant: Tenant, conversation: Conversation
    ) -> None:
        llm = MockLLMProvider(
            responses=[
                ChatResult(content="", tool_calls=(_call("consultar_catalogo", consulta="lomo"),)),
                ChatResult(content="El lomo cuesta S/ 32.00."),
            ]
        )

        reply = await AgentCore(db, tenant.id, llm=llm, registry=_registry()).respond(
            conversation_id=conversation.id, customer_message="cuánto cuesta el lomo"
        )

        tool_message = next(m for m in llm.chat_calls[1].messages if m.role == "tool")
        assert "Lomo saltado" in tool_message.content
        assert reply.tools_called[0]["tool"] == "consultar_catalogo"
        assert reply.tools_called[0]["ok"] is True

    async def test_the_v0_registry_is_unchanged(
        self, db: Session, tenant: Tenant, conversation: Conversation
    ) -> None:
        """The agent as it runs today (no registry given) still offers the old tool."""
        llm = MockLLMProvider(responses=[ChatResult(content="Hola")])

        await AgentCore(db, tenant.id, llm=llm).respond(
            conversation_id=conversation.id, customer_message="hola"
        )

        assert [spec.name for spec in llm.chat_calls[0].tools] == [
            "consultar_catalogo",
            "consultar_conocimiento",
        ]


class TestKnowledgeSources:
    async def test_nothing_found_records_no_source(
        self, db: Session, tenant: Tenant, conversation: Conversation
    ) -> None:
        llm = MockLLMProvider(
            responses=[
                ChatResult(content="", tool_calls=(_call("consultar_conocimiento", consulta="x"),)),
                ChatResult(content="No tengo esa información."),
            ]
        )

        reply = await AgentCore(db, tenant.id, llm=llm, registry=_registry()).respond(
            conversation_id=conversation.id, customer_message="hola"
        )

        assert reply.rag_sources == []
