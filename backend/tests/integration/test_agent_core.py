"""The agent loop, driven with a scripted model (TASK-014).

A mock rather than a real provider, because these assert what the *loop* does
with a model's output - calls the tool it asked for, feeds the result back,
bounds itself, records the trace. Whether a particular model chooses to call a
tool is the evals' job in TASK-016, not this file's.
"""

from __future__ import annotations

import uuid
from decimal import Decimal

import pytest
from sqlalchemy.orm import Session

from app.agent.core import FALLBACK_REPLY, AgentCore
from app.agent.tools.registry import ToolRegistry
from app.models.business import AgentConfig, BusinessProfile, Product
from app.models.conversation import Conversation, Message
from app.models.knowledge import KnowledgeChunk, KnowledgeSource
from app.models.tenant import Tenant
from app.providers.llm import ChatResult, LLMError, MockLLMProvider, ToolCall


@pytest.fixture
def tenant(db: Session) -> Tenant:
    row = Tenant(name="Panaderia Quispe")
    db.add(row)
    db.flush()
    db.add_all(
        [
            BusinessProfile(
                tenant_id=row.id, business_name="Panaderia Quispe", industry="restaurante"
            ),
            AgentConfig(tenant_id=row.id, agent_name="Leo", tone="cercano", objective="ambos"),
            Product(
                tenant_id=row.id,
                name="Lomo saltado",
                price=Decimal("32.00"),
                category="Platos",
                is_active=True,
            ),
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


def _tool_call(name: str, **arguments: object) -> ToolCall:
    return ToolCall(id=f"call-{uuid.uuid4().hex[:8]}", name=name, arguments=dict(arguments))


class TestPlainAnswer:
    async def test_a_model_that_answers_directly_is_relayed(
        self, db: Session, tenant: Tenant, conversation: Conversation
    ) -> None:
        llm = MockLLMProvider(responses=[ChatResult(content="¡Hola! ¿En qué te ayudo?")])
        reply = await AgentCore(db, tenant.id, llm=llm).respond(
            conversation_id=conversation.id, customer_message="hola"
        )
        assert reply.content == "¡Hola! ¿En qué te ayudo?"
        assert reply.tools_called == []

    async def test_the_system_prompt_carries_the_tenants_configuration(
        self, db: Session, tenant: Tenant, conversation: Conversation
    ) -> None:
        llm = MockLLMProvider(responses=[ChatResult(content="ok")])
        await AgentCore(db, tenant.id, llm=llm).respond(
            conversation_id=conversation.id, customer_message="hola"
        )
        system = llm.chat_calls[0].messages[0]
        assert system.role == "system"
        assert "Panaderia Quispe" in system.content
        assert "Nunca inventes" in system.content

    async def test_an_empty_answer_becomes_the_fallback(
        self, db: Session, tenant: Tenant, conversation: Conversation
    ) -> None:
        """An empty bubble gives the customer no next step."""
        llm = MockLLMProvider(responses=[ChatResult(content="   ")])
        reply = await AgentCore(db, tenant.id, llm=llm).respond(
            conversation_id=conversation.id, customer_message="hola"
        )
        assert reply.content == FALLBACK_REPLY


class TestToolLoop:
    async def test_a_tool_call_is_executed_and_fed_back(
        self, db: Session, tenant: Tenant, conversation: Conversation
    ) -> None:
        llm = MockLLMProvider(
            responses=[
                ChatResult(
                    content="", tool_calls=(_tool_call("consultar_catalogo", consulta="lomo"),)
                ),
                ChatResult(content="El lomo saltado cuesta S/ 32.00."),
            ]
        )
        reply = await AgentCore(db, tenant.id, llm=llm).respond(
            conversation_id=conversation.id, customer_message="cuanto cuesta el lomo?"
        )

        assert "32.00" in reply.content
        assert reply.tools_called[0]["tool"] == "consultar_catalogo"
        assert reply.tools_called[0]["ok"] is True

        # The tool output reached the model on the second call.
        second_call = llm.chat_calls[1].messages
        assert any(m.role == "tool" and "Lomo saltado" in m.content for m in second_call)

    async def test_the_catalogue_result_carries_the_real_price(
        self, db: Session, tenant: Tenant, conversation: Conversation
    ) -> None:
        """The whole point of the tool: the model quotes the database, not
        its own recollection."""
        llm = MockLLMProvider(
            responses=[
                ChatResult(
                    content="",
                    tool_calls=(_tool_call("consultar_catalogo", consulta="x"),),
                ),
                ChatResult(content="ok"),
            ]
        )
        await AgentCore(db, tenant.id, llm=llm).respond(
            conversation_id=conversation.id, customer_message="hola"
        )
        tool_message = next(m for m in llm.chat_calls[1].messages if m.role == "tool")
        assert "S/ 32.00" in tool_message.content

    async def test_the_loop_is_bounded(
        self, db: Session, tenant: Tenant, conversation: Conversation
    ) -> None:
        """A model that keeps calling tools bills per iteration and leaves the
        customer waiting. The last round offers no tools, forcing an answer."""
        forever = [
            ChatResult(content="", tool_calls=(_tool_call("consultar_catalogo", consulta="x"),))
            for _ in range(5)
        ]
        forever.append(ChatResult(content="Ya te respondo."))
        llm = MockLLMProvider(responses=forever)

        reply = await AgentCore(db, tenant.id, llm=llm, max_tool_rounds=2).respond(
            conversation_id=conversation.id, customer_message="hola"
        )
        # 2 tool rounds + 1 forced answer.
        assert len(llm.chat_calls) == 3
        assert llm.chat_calls[-1].tools == ()
        assert reply.content

    async def test_an_unknown_tool_is_reported_not_fatal(
        self, db: Session, tenant: Tenant, conversation: Conversation
    ) -> None:
        """Models do hallucinate tool names. Telling the model beats crashing
        the turn, and it is a real signal in the trace."""
        llm = MockLLMProvider(
            responses=[
                ChatResult(content="", tool_calls=(_tool_call("consultar_horoscopo"),)),
                ChatResult(content="Perdón, no puedo con eso."),
            ]
        )
        reply = await AgentCore(db, tenant.id, llm=llm).respond(
            conversation_id=conversation.id, customer_message="hola"
        )
        assert reply.tools_called[0]["ok"] is False
        assert reply.content == "Perdón, no puedo con eso."

    async def test_a_failing_tool_is_reported_not_fatal(
        self, db: Session, tenant: Tenant, conversation: Conversation
    ) -> None:
        registry = ToolRegistry()

        async def explode(context: object, arguments: dict[str, object]) -> str:
            raise RuntimeError("boom")

        registry.register(
            name="romper", description="", parameters={"type": "object"}, handler=explode
        )

        llm = MockLLMProvider(
            responses=[
                ChatResult(content="", tool_calls=(_tool_call("romper"),)),
                ChatResult(content="Disculpa, tuve un problema."),
            ]
        )
        reply = await AgentCore(db, tenant.id, llm=llm, registry=registry).respond(
            conversation_id=conversation.id, customer_message="hola"
        )
        assert reply.tools_called[0]["ok"] is False
        assert reply.content == "Disculpa, tuve un problema."


class TestKnowledgeTool:
    async def test_retrieval_reaches_the_model(
        self, db: Session, tenant: Tenant, conversation: Conversation
    ) -> None:
        source = KnowledgeSource(
            tenant_id=tenant.id, type="faq", title="Horarios", status="indexed"
        )
        db.add(source)
        db.flush()

        llm = MockLLMProvider(
            responses=[
                ChatResult(
                    content="",
                    tool_calls=(_tool_call("consultar_conocimiento", consulta="horario"),),
                ),
                ChatResult(content="Abrimos de 9 a 6."),
            ]
        )
        # The mock's embeddings are deterministic and the right width, so the
        # stored chunk and the query embed consistently.
        vector = (await llm.embed(["Abrimos de lunes a sabado de 9 a 6."]))[0]
        db.add(
            KnowledgeChunk(
                tenant_id=tenant.id,
                source_id=source.id,
                content="Abrimos de lunes a sabado de 9 a 6.",
                embedding=vector,
            )
        )
        db.flush()

        reply = await AgentCore(db, tenant.id, llm=llm).respond(
            conversation_id=conversation.id, customer_message="a que hora abren?"
        )
        tool_message = next(m for m in llm.chat_calls[1].messages if m.role == "tool")
        assert "9 a 6" in tool_message.content
        assert reply.rag_sources

    async def test_nothing_found_says_so_instead_of_returning_empty(
        self, db: Session, tenant: Tenant, conversation: Conversation
    ) -> None:
        """An empty result is an absence the model will fill. A sentence
        saying "there is nothing" is a fact it can relay."""
        llm = MockLLMProvider(
            responses=[
                ChatResult(
                    content="", tool_calls=(_tool_call("consultar_conocimiento", consulta="x"),)
                ),
                ChatResult(content="No tengo esa información."),
            ]
        )
        await AgentCore(db, tenant.id, llm=llm).respond(
            conversation_id=conversation.id, customer_message="hola"
        )
        tool_message = next(m for m in llm.chat_calls[1].messages if m.role == "tool")
        assert "No hay información" in tool_message.content
        assert "No inventes" in tool_message.content


class TestProviderFailure:
    async def test_a_provider_error_degrades_instead_of_raising(
        self, db: Session, tenant: Tenant, conversation: Conversation
    ) -> None:
        """A customer waiting in a chat gets an apology, not a 500."""

        class BrokenProvider(MockLLMProvider):
            async def chat(self, messages, **kwargs):  # type: ignore[no-untyped-def]
                raise LLMError("provider is down")

        reply = await AgentCore(db, tenant.id, llm=BrokenProvider()).respond(
            conversation_id=conversation.id, customer_message="hola"
        )
        assert reply.content == FALLBACK_REPLY
        assert reply.degraded is True


class TestHistory:
    async def test_previous_turns_are_replayed(
        self, db: Session, tenant: Tenant, conversation: Conversation
    ) -> None:
        db.add_all(
            [
                Message(
                    tenant_id=tenant.id,
                    conversation_id=conversation.id,
                    direction="inbound",
                    author="customer",
                    content="tienen lomo?",
                ),
                Message(
                    tenant_id=tenant.id,
                    conversation_id=conversation.id,
                    direction="outbound",
                    author="agent",
                    content="Si, S/ 32.00.",
                ),
            ]
        )
        db.flush()

        llm = MockLLMProvider(responses=[ChatResult(content="ok")])
        await AgentCore(db, tenant.id, llm=llm).respond(
            conversation_id=conversation.id, customer_message="y cuanto era?"
        )
        contents = [m.content for m in llm.chat_calls[0].messages]
        assert "tienen lomo?" in contents
        assert "Si, S/ 32.00." in contents


class TestTrace:
    async def test_every_reply_leaves_a_trace(
        self, db: Session, tenant: Tenant, conversation: Conversation
    ) -> None:
        """When a customer says the bot quoted the wrong price, the trace is
        the only way to tell a bad catalogue from a bad retrieval from an
        invented answer."""
        llm = MockLLMProvider(
            responses=[
                ChatResult(
                    content="",
                    tool_calls=(_tool_call("consultar_catalogo", consulta="lomo"),),
                ),
                ChatResult(
                    content="S/ 32.00", model="test-model", input_tokens=120, output_tokens=15
                ),
            ]
        )
        core = AgentCore(db, tenant.id, llm=llm)
        reply = await core.respond(
            conversation_id=conversation.id, customer_message="precio del lomo?"
        )

        message = Message(
            tenant_id=tenant.id,
            conversation_id=conversation.id,
            direction="outbound",
            author="agent",
            content=reply.content,
        )
        db.add(message)
        db.flush()

        core.persist_trace(message=message, reply=reply)
        db.flush()

        from app.repositories.conversation import AgentTraceRepository

        traces = AgentTraceRepository(db, tenant.id).for_message(message.id)
        assert len(traces) == 1
        trace = traces[0]
        assert trace.model == "test-model"
        assert trace.tools_called[0]["tool"] == "consultar_catalogo"
        assert trace.input_tokens == 120
        assert trace.output_tokens == 15
        assert trace.latency_ms is not None

    async def test_tool_arguments_are_summarised_not_stored_whole(
        self, db: Session, tenant: Tenant, conversation: Conversation
    ) -> None:
        """A trace is not the place to accumulate a second copy of the
        conversation."""
        llm = MockLLMProvider(
            responses=[
                ChatResult(
                    content="",
                    tool_calls=(_tool_call("consultar_catalogo", consulta="x" * 500),),
                ),
                ChatResult(content="ok"),
            ]
        )
        reply = await AgentCore(db, tenant.id, llm=llm).respond(
            conversation_id=conversation.id, customer_message="hola"
        )
        assert len(str(reply.tools_called[0]["args_resumen"])) <= 120
