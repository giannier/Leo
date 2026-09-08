"""The agent never sees another business's data (TASK-014).

The sharpest isolation surface in the project. Everywhere else a leak shows up
in an API response a developer reads; here it would be spoken to a stranger on
WhatsApp, in the victim's own brand voice, with their prices.
"""

from __future__ import annotations

import uuid
from decimal import Decimal

import pytest
from sqlalchemy.orm import Session

from app.agent.core import CATALOG_GUARD_REPLY, AgentCore
from app.models.business import AgentConfig, BusinessProfile, Product
from app.models.conversation import Conversation
from app.models.knowledge import KnowledgeChunk, KnowledgeSource
from app.models.tenant import Tenant
from app.providers.llm import ChatResult, MockLLMProvider, ToolCall
from app.repositories.product import ProductRepository


def _tool_call(name: str, **arguments: object) -> ToolCall:
    return ToolCall(id=f"call-{uuid.uuid4().hex[:8]}", name=name, arguments=dict(arguments))


def _business(db: Session, label: str, *, product: str, price: str) -> Tenant:
    tenant = Tenant(name=f"Negocio {label}")
    db.add(tenant)
    db.flush()
    db.add_all(
        [
            BusinessProfile(
                tenant_id=tenant.id, business_name=f"Negocio {label}", industry="restaurante"
            ),
            AgentConfig(tenant_id=tenant.id, agent_name=f"Bot {label}", objective="vender"),
            Product(tenant_id=tenant.id, name=product, price=Decimal(price), is_active=True),
        ]
    )
    db.flush()
    return tenant


@pytest.fixture
def business_a(db: Session) -> Tenant:
    return _business(db, "A", product="Lomo saltado", price="32.00")


@pytest.fixture
def business_b(db: Session) -> Tenant:
    return _business(db, "B", product="Secreto de B", price="999.00")


class TestTheAgentNeverQuotesAnotherBusiness:
    async def test_the_catalogue_tool_is_scoped(
        self, db: Session, business_a: Tenant, business_b: Tenant
    ) -> None:
        """The leak that would matter most: B's price read aloud to A's customer."""
        conversation = Conversation(tenant_id=business_a.id, channel="sandbox")
        db.add(conversation)
        db.flush()

        llm = MockLLMProvider(
            responses=[
                ChatResult(content="", tool_calls=(_tool_call("consultar_catalogo", consulta=""),)),
                ChatResult(content="ok"),
            ]
        )
        await AgentCore(db, business_a.id, llm=llm).respond(
            conversation_id=conversation.id, customer_message="que tienen?"
        )

        tool_message = next(m for m in llm.chat_calls[1].messages if m.role == "tool")
        assert "Lomo saltado" in tool_message.content
        assert "Secreto de B" not in tool_message.content
        assert "999.00" not in tool_message.content

    async def test_the_knowledge_tool_is_scoped(
        self, db: Session, business_a: Tenant, business_b: Tenant
    ) -> None:
        llm = MockLLMProvider(
            responses=[
                ChatResult(
                    content="",
                    tool_calls=(_tool_call("consultar_conocimiento", consulta="politica"),),
                ),
                ChatResult(content="ok"),
            ]
        )

        secret = "La clave del wifi de B es 12345 y el margen es 80%."
        source = KnowledgeSource(
            tenant_id=business_b.id, type="policy", title="Interno", status="indexed"
        )
        db.add(source)
        db.flush()
        db.add(
            KnowledgeChunk(
                tenant_id=business_b.id,
                source_id=source.id,
                content=secret,
                embedding=(await llm.embed([secret]))[0],
            )
        )
        db.flush()

        conversation = Conversation(tenant_id=business_a.id, channel="sandbox")
        db.add(conversation)
        db.flush()

        await AgentCore(db, business_a.id, llm=llm).respond(
            conversation_id=conversation.id, customer_message="cual es la politica?"
        )

        tool_message = next(m for m in llm.chat_calls[1].messages if m.role == "tool")
        assert "12345" not in tool_message.content
        assert "80%" not in tool_message.content

    async def test_the_system_prompt_carries_only_its_own_identity(
        self, db: Session, business_a: Tenant, business_b: Tenant
    ) -> None:
        """A prompt built from the wrong row would have the agent introduce
        itself as somebody else's business."""
        prompt = AgentCore(db, business_a.id, llm=MockLLMProvider()).system_prompt()
        assert "Negocio A" in prompt
        assert "Bot A" in prompt
        assert "Negocio B" not in prompt
        assert "Bot B" not in prompt

    async def test_history_does_not_cross_conversations(
        self, db: Session, business_a: Tenant, business_b: Tenant
    ) -> None:
        from app.models.conversation import Message

        conversation_b = Conversation(tenant_id=business_b.id, channel="sandbox")
        db.add(conversation_b)
        db.flush()
        db.add(
            Message(
                tenant_id=business_b.id,
                conversation_id=conversation_b.id,
                direction="inbound",
                author="customer",
                content="dato confidencial de B",
            )
        )
        conversation_a = Conversation(tenant_id=business_a.id, channel="sandbox")
        db.add(conversation_a)
        db.flush()

        llm = MockLLMProvider(responses=[ChatResult(content="ok")])
        # A's agent asked for B's conversation id: it must find nothing.
        await AgentCore(db, business_a.id, llm=llm).respond(
            conversation_id=conversation_b.id, customer_message="hola"
        )
        contents = " ".join(m.content for m in llm.chat_calls[0].messages)
        assert "confidencial de B" not in contents

    async def test_the_catalogue_guard_judges_against_its_own_catalogue_only(
        self, db: Session, business_a: Tenant, business_b: Tenant
    ) -> None:
        """The post-guard decides whether a product name is real. If it read
        the wrong catalogue it would get that backwards in both directions:
        blessing a name A does not sell because B does, and blocking a name B
        does sell because A does not.

        The same sentence is put to both agents. It is a lie for one of them
        and the truth for the other, and only a correctly scoped guard can
        tell which is which.
        """
        db.add(
            Product(
                tenant_id=business_b.id,
                name="Lomo saltado premium",
                price=Decimal("55.00"),
                is_active=True,
            )
        )
        db.flush()

        claim = "Tenemos Lomo saltado premium a S/ 55.00."

        # A does not sell it, so A's agent may not say it. The retry offends
        # again, which is what makes the reply the guarded fallback.
        conversation_a = Conversation(tenant_id=business_a.id, channel="sandbox")
        db.add(conversation_a)
        db.flush()
        llm_a = MockLLMProvider(responses=[ChatResult(content=claim), ChatResult(content=claim)])
        reply_a = await AgentCore(db, business_a.id, llm=llm_a).respond(
            conversation_id=conversation_a.id, customer_message="que tienen?"
        )
        assert reply_a.content == CATALOG_GUARD_REPLY
        assert reply_a.catalog_guard == {
            "status": "blocked",
            "invented": ["lomo saltado premium"],
            "still_invented": ["lomo saltado premium"],
        }

        # B does sell it. A guard that had leaked A's catalogue into B's turn
        # would replace a correct answer with an apology.
        conversation_b = Conversation(tenant_id=business_b.id, channel="sandbox")
        db.add(conversation_b)
        db.flush()
        llm_b = MockLLMProvider(responses=[ChatResult(content=claim)])
        reply_b = await AgentCore(db, business_b.id, llm=llm_b).respond(
            conversation_id=conversation_b.id, customer_message="que tienen?"
        )
        assert reply_b.content == claim
        assert reply_b.catalog_guard is None

    async def test_the_names_the_guard_reads_are_scoped(
        self, db: Session, business_a: Tenant, business_b: Tenant
    ) -> None:
        """The read behind the guard, at the repository layer where the
        Constitution puts the filter."""
        names_a = ProductRepository(db, business_a.id).active_names(limit=100)
        names_b = ProductRepository(db, business_b.id).active_names(limit=100)

        assert names_a == ["Lomo saltado"]
        assert names_b == ["Secreto de B"]
        assert "Secreto de B" not in names_a

    async def test_traces_are_written_under_the_acting_tenant(
        self, db: Session, business_a: Tenant, business_b: Tenant
    ) -> None:
        from app.models.conversation import Message
        from app.repositories.conversation import AgentTraceRepository

        conversation = Conversation(tenant_id=business_a.id, channel="sandbox")
        db.add(conversation)
        db.flush()

        llm = MockLLMProvider(responses=[ChatResult(content="hola")])
        core = AgentCore(db, business_a.id, llm=llm)
        reply = await core.respond(conversation_id=conversation.id, customer_message="hola")

        message = Message(
            tenant_id=business_a.id,
            conversation_id=conversation.id,
            direction="outbound",
            author="agent",
            content=reply.content,
        )
        db.add(message)
        db.flush()
        core.persist_trace(message=message, reply=reply)
        db.flush()

        assert len(AgentTraceRepository(db, business_a.id).for_message(message.id)) == 1
        assert AgentTraceRepository(db, business_b.id).for_message(message.id) == []
