"""The agent loop, driven with a scripted model (TASK-014).

A mock rather than a real provider, because these assert what the *loop* does
with a model's output - calls the tool it asked for, feeds the result back,
bounds itself, records the trace. Whether a particular model chooses to call a
tool is the evals' job in TASK-016, not this file's.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from decimal import Decimal

import pytest
from sqlalchemy.orm import Session

from app.agent.core import (
    CATALOG_GUARD_REPLY,
    FALLBACK_REPLY,
    AgentCore,
    _usable_answer,
    whatsapp_text,
)
from app.agent.tools.registry import ToolRegistry
from app.models.business import AgentConfig, BusinessProfile, Product
from app.models.conversation import Conversation, Message
from app.models.knowledge import KnowledgeChunk, KnowledgeSource
from app.models.tenant import Tenant
from app.providers.llm import ChatResult, LLMError, MockLLMProvider, ToolCall
from app.repositories.conversation import AgentTraceRepository
from app.repositories.product import ProductRepository


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

    async def test_a_short_answer_is_delivered_not_treated_as_a_failure(
        self, db: Session, tenant: Tenant, conversation: Conversation
    ) -> None:
        """A customer who says "ok" gets a short reply back. Replacing it with
        an apology, and marking the turn degraded, blames the model for a
        failure that never happened."""
        llm = MockLLMProvider(responses=[ChatResult(content="¡De nada! 👍")])
        reply = await AgentCore(db, tenant.id, llm=llm).respond(
            conversation_id=conversation.id, customer_message="ok"
        )
        assert reply.content == "¡De nada! 👍"
        assert reply.degraded is False


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


class TestRepeatedToolCalls:
    """A model asking the same thing twice must not be billed for it twice.

    `consultar_catalogo` came back seven times for the same term in a single
    conversation. Every repeat was another database round trip and another
    second of a customer waiting for an answer that could not have changed.
    """

    @staticmethod
    def _counting_registry(counter: list[int]) -> ToolRegistry:
        registry = ToolRegistry()

        async def count(context: object, arguments: dict[str, object]) -> str:
            counter.append(1)
            return f"resultado para {arguments.get('consulta')}"

        registry.register(
            name="consultar_catalogo",
            description="",
            parameters={"type": "object"},
            handler=count,
        )
        return registry

    async def test_the_same_call_twice_runs_the_tool_once(
        self, db: Session, tenant: Tenant, conversation: Conversation
    ) -> None:
        runs: list[int] = []
        llm = MockLLMProvider(
            responses=[
                ChatResult(
                    content="",
                    tool_calls=(_tool_call("consultar_catalogo", consulta="lomo"),),
                ),
                ChatResult(
                    content="",
                    tool_calls=(_tool_call("consultar_catalogo", consulta="lomo"),),
                ),
                ChatResult(content="El lomo saltado cuesta S/ 32.00."),
            ]
        )
        reply = await AgentCore(
            db, tenant.id, llm=llm, registry=self._counting_registry(runs)
        ).respond(conversation_id=conversation.id, customer_message="cuanto cuesta el lomo?")

        assert len(runs) == 1
        # Both repeats stay in the trace: hiding them would hide the fact
        # that the model asked seven times.
        assert len(reply.tools_called) == 2
        assert reply.tools_called[0]["cached"] is False
        assert reply.tools_called[1]["cached"] is True

        # The model still got an answer for its second call, not an empty one.
        tool_messages = [m for m in llm.chat_calls[2].messages if m.role == "tool"]
        assert len(tool_messages) == 2
        assert tool_messages[0].content == tool_messages[1].content

    async def test_different_arguments_are_a_different_call(
        self, db: Session, tenant: Tenant, conversation: Conversation
    ) -> None:
        """The cache keys on the arguments, so a second question is asked."""
        runs: list[int] = []
        llm = MockLLMProvider(
            responses=[
                ChatResult(
                    content="",
                    tool_calls=(
                        _tool_call("consultar_catalogo", consulta="lomo"),
                        _tool_call("consultar_catalogo", consulta="ceviche"),
                    ),
                ),
                ChatResult(content="Tenemos ambos."),
            ]
        )
        reply = await AgentCore(
            db, tenant.id, llm=llm, registry=self._counting_registry(runs)
        ).respond(conversation_id=conversation.id, customer_message="que tienen?")

        assert len(runs) == 2
        assert [entry["cached"] for entry in reply.tools_called] == [False, False]

    async def test_a_failure_is_retried_rather_than_remembered(
        self, db: Session, tenant: Tenant, conversation: Conversation
    ) -> None:
        """An error is the one result worth trying again: the second attempt
        is how a transient database blip stops being the customer's problem."""
        attempts: list[int] = []
        registry = ToolRegistry()

        async def flaky(context: object, arguments: dict[str, object]) -> str:
            attempts.append(1)
            if len(attempts) == 1:
                raise RuntimeError("boom")
            return "Lomo saltado - S/ 32.00"

        registry.register(
            name="consultar_catalogo",
            description="",
            parameters={"type": "object"},
            handler=flaky,
        )

        llm = MockLLMProvider(
            responses=[
                ChatResult(
                    content="", tool_calls=(_tool_call("consultar_catalogo", consulta="lomo"),)
                ),
                ChatResult(
                    content="", tool_calls=(_tool_call("consultar_catalogo", consulta="lomo"),)
                ),
                ChatResult(content="Cuesta S/ 32.00."),
            ]
        )
        reply = await AgentCore(db, tenant.id, llm=llm, registry=registry).respond(
            conversation_id=conversation.id, customer_message="cuanto cuesta el lomo?"
        )

        assert len(attempts) == 2
        assert [entry["ok"] for entry in reply.tools_called] == [False, True]
        assert [entry["cached"] for entry in reply.tools_called] == [False, False]

    async def test_the_cache_does_not_outlive_the_turn(
        self, db: Session, tenant: Tenant, conversation: Conversation
    ) -> None:
        """A catalogue edited between two customer messages must be seen on
        the next one, so the memo is scoped to a single `respond`."""
        runs: list[int] = []
        one_turn = [
            ChatResult(content="", tool_calls=(_tool_call("consultar_catalogo", consulta="lomo"),)),
            ChatResult(content="Cuesta S/ 32.00."),
        ]
        llm = MockLLMProvider(responses=[*one_turn, *one_turn])
        core = AgentCore(db, tenant.id, llm=llm, registry=self._counting_registry(runs))

        await core.respond(conversation_id=conversation.id, customer_message="precio del lomo?")
        await core.respond(conversation_id=conversation.id, customer_message="y ahora?")

        assert len(runs) == 2


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


class TestToolSyntaxNeverReachesTheCustomer:
    """A model sometimes writes a tool call as prose instead of emitting it.

    It happens in the final round, where no tools are offered in order to
    force an answer: a model that still wants one has nowhere to put the call,
    so it writes it out. Caught in CI against NVIDIA, on the angry-customer
    case of all things - somebody writing a complaint received:

        <function=consultar_conocimiento>
        <parameter=consulta>reclamo queja</parameter>
        </function>

    which is worse than silence. It looks broken *and* it ignores them.
    """

    def test_the_leak_that_actually_happened(self) -> None:
        raw = (
            "<function=consultar_conocimiento>\n"
            "<parameter=consulta>\nreclamo queja\n</parameter>\n"
            "</function>\n</tool_call>"
        )

        content, unusable = _usable_answer(raw)

        assert unusable is True
        assert content == FALLBACK_REPLY
        assert "<function" not in content

    @pytest.mark.parametrize(
        "raw",
        [
            "<tool_call>{}</tool_call>",
            "<|python_tag|>",
            "<parameter=x>y</parameter>",
            "",
            "   \n  ",
        ],
    )
    def test_anything_without_a_real_answer_falls_back(self, raw: str) -> None:
        content, unusable = _usable_answer(raw)

        assert unusable is True
        assert content == FALLBACK_REPLY

    def test_a_real_answer_is_left_exactly_as_written(self) -> None:
        """The guard must not touch the ordinary case. Rewriting a good reply
        would be a far more common failure than the one being prevented."""
        raw = "Lo siento mucho. Te derivo con una persona ahora mismo."

        content, unusable = _usable_answer(raw)

        assert content == raw
        assert unusable is False

    def test_prose_survives_alongside_stray_markup(self) -> None:
        """Some models emit a sentence *and* a call. Dropping the sentence
        would throw away the part the customer needed."""
        raw = '<tool_call>{"a":1}</tool_call> El pan ciabatta cuesta S/ 8.50 en total.'

        content, unusable = _usable_answer(raw)

        assert unusable is False
        assert "tool_call" not in content
        assert "S/ 8.50" in content

    @pytest.mark.parametrize(
        "raw",
        [
            "¡De nada! 👍",
            "Gracias",
            "👍",
            "Si.",
            "S/ 32.00",
        ],
    )
    def test_a_short_reply_without_markup_is_a_real_reply(self, raw: str) -> None:
        """The length rule guards against a *fragment left by the strip*, not
        against brevity. A customer who says "ok" gets "¡De nada! 👍", and
        answering that with an apology for a failure that never happened is
        the worse of the two errors."""
        content, unusable = _usable_answer(raw)

        assert content == raw
        assert unusable is False

    def test_a_short_fragment_left_by_the_strip_still_falls_back(self) -> None:
        """The case the rule exists for: the markup went, and what is left is
        a shard of the call rather than something to send anybody."""
        raw = "<function=consultar_catalogo>pan</function>"

        content, unusable = _usable_answer(raw)

        assert content == FALLBACK_REPLY
        assert unusable is True

    def test_a_less_than_sign_in_ordinary_prose_is_not_stripped(self) -> None:
        """The guard keys on tool-call *names*, not on angle brackets. A reply
        that happens to contain one is not a leak."""
        raw = "Te toma menos de <5 minutos preparar el pedido, avisame."

        content, unusable = _usable_answer(raw)

        assert content == raw
        assert unusable is False

    async def test_the_reply_is_marked_degraded_so_the_trace_says_so(
        self, db: Session, tenant: Tenant, conversation: Conversation
    ) -> None:
        """`degraded` is what tells the tenant it was the model that failed
        and not their configuration - the sandbox panel says so in words."""
        leak = "<function=consultar_catalogo>\n<parameter=q>pan</parameter>\n</function>"
        llm = MockLLMProvider(responses=[ChatResult(content=leak, model="test")])

        reply = await AgentCore(db, tenant.id, llm=llm).respond(
            conversation_id=conversation.id, customer_message="hola"
        )

        assert reply.degraded is True
        assert reply.content == FALLBACK_REPLY


class TestTheCatalogueGuard:
    """A product name the catalogue does not have never reaches a customer.

    The catalogue said "Leche"; the agent offered "Leche pausterizada". Prompt
    rule 1 already forbade that and did not stop it, so the finished reply is
    checked against the tenant's real catalogue before it is sent. On a hit
    the model gets one retry with the exact names it may use; if it offends
    again the invented name is dropped rather than delivered.

    The tenant fixture sells exactly one thing, "Lomo saltado", which is what
    makes "Lomo saltado criollo" a product that does not exist.
    """

    async def test_an_invented_name_is_retried_and_the_correction_is_delivered(
        self, db: Session, tenant: Tenant, conversation: Conversation
    ) -> None:
        llm = MockLLMProvider(
            responses=[
                ChatResult(content="Claro, tenemos Lomo saltado criollo a S/ 32.00."),
                ChatResult(content="Claro, tenemos Lomo saltado a S/ 32.00."),
            ]
        )

        reply = await AgentCore(db, tenant.id, llm=llm).respond(
            conversation_id=conversation.id, customer_message="que tienen?"
        )

        assert reply.content == "Claro, tenemos Lomo saltado a S/ 32.00."
        assert "criollo" not in reply.content
        assert reply.degraded is False
        assert reply.catalog_guard == {
            "status": "corrected",
            "invented": ["lomo saltado criollo"],
            "still_invented": [],
        }
        assert len(llm.chat_calls) == 2

    async def test_the_correction_hands_the_model_the_real_names(
        self, db: Session, tenant: Tenant, conversation: Conversation
    ) -> None:
        """ "No inventes productos" is already rule 1 and the model broke it.
        Repeating it changes nothing; the exact strings it may copy do."""
        llm = MockLLMProvider(
            responses=[
                ChatResult(content="Claro, tenemos Lomo saltado criollo a S/ 32.00."),
                ChatResult(content="Claro, tenemos Lomo saltado a S/ 32.00."),
            ]
        )
        await AgentCore(db, tenant.id, llm=llm).respond(
            conversation_id=conversation.id, customer_message="que tienen?"
        )

        correction = llm.chat_calls[1].messages[-1]
        assert correction.role == "user"
        assert "«lomo saltado criollo»" in correction.content
        assert "- Lomo saltado" in correction.content
        # The offending answer is replayed, or the model is being corrected
        # for something it cannot see.
        assert any(
            m.role == "assistant" and "criollo" in m.content for m in llm.chat_calls[1].messages
        )
        # No tools on the retry: this turn is over, it needs an answer.
        assert llm.chat_calls[1].tools == ()

    async def test_a_clean_reply_is_untouched_and_costs_no_retry(
        self, db: Session, tenant: Tenant, conversation: Conversation
    ) -> None:
        """The common case, and the one a false alarm would ruin. An ordinary
        answer must come out byte-for-byte and cost exactly one model call."""
        answer = "El Lomo saltado viene con papas fritas y arroz, cuesta S/ 32.00."
        llm = MockLLMProvider(responses=[ChatResult(content=answer)])

        reply = await AgentCore(db, tenant.id, llm=llm).respond(
            conversation_id=conversation.id, customer_message="que trae el lomo?"
        )

        assert reply.content == answer
        assert reply.catalog_guard is None
        assert reply.degraded is False
        assert len(llm.chat_calls) == 1

    async def test_a_second_offence_falls_back_instead_of_sending_the_name(
        self, db: Session, tenant: Tenant, conversation: Conversation
    ) -> None:
        """Told the real names and it invented one again. Two attempts is
        where this stops: a third is more seconds of "typing..." for an answer
        that is not going to arrive."""
        llm = MockLLMProvider(
            responses=[
                ChatResult(content="Claro, tenemos Lomo saltado criollo a S/ 32.00."),
                ChatResult(content="Perdon, es el Lomo saltado especial a S/ 32.00."),
            ]
        )

        reply = await AgentCore(db, tenant.id, llm=llm).respond(
            conversation_id=conversation.id, customer_message="que tienen?"
        )

        assert reply.content == CATALOG_GUARD_REPLY
        assert "criollo" not in reply.content
        assert "especial" not in reply.content
        assert reply.degraded is True
        assert reply.catalog_guard == {
            "status": "blocked",
            "invented": ["lomo saltado criollo"],
            "still_invented": ["lomo saltado especial"],
        }
        assert len(llm.chat_calls) == 2

    async def test_a_provider_failure_during_the_correction_blocks(
        self, db: Session, tenant: Tenant, conversation: Conversation
    ) -> None:
        """The retry is the only thing that could have rescued the answer. If
        it never happens the original still names a product that does not
        exist, so it still may not be sent."""
        llm = MockLLMProvider(
            responses=[ChatResult(content="Claro, tenemos Lomo saltado criollo a S/ 32.00.")]
        )

        reply = await AgentCore(db, tenant.id, llm=llm).respond(
            conversation_id=conversation.id, customer_message="que tienen?"
        )

        assert reply.content == CATALOG_GUARD_REPLY
        assert reply.degraded is True
        assert reply.catalog_guard == {
            "status": "blocked",
            "invented": ["lomo saltado criollo"],
            "still_invented": [],
        }

    async def test_the_retry_is_metered(
        self, db: Session, tenant: Tenant, conversation: Conversation
    ) -> None:
        """A correction is a real model call on a real bill. A trace that
        hides it under-reports the cost of a prompt that keeps slipping."""
        llm = MockLLMProvider(
            responses=[
                ChatResult(
                    content="Tenemos Lomo saltado criollo.", input_tokens=100, output_tokens=10
                ),
                ChatResult(content="Tenemos Lomo saltado.", input_tokens=140, output_tokens=8),
            ]
        )

        reply = await AgentCore(db, tenant.id, llm=llm).respond(
            conversation_id=conversation.id, customer_message="que tienen?"
        )

        assert reply.input_tokens == 240
        assert reply.output_tokens == 18

    async def test_an_empty_catalogue_never_fires(
        self, db: Session, conversation: Conversation
    ) -> None:
        """A tenant mid-onboarding has no products yet. Judging an answer
        against nothing and finding it wanting would replace every reply with
        an apology on the day they are still setting up."""
        bare = Tenant(name="Sin catalogo")
        db.add(bare)
        db.flush()
        db.add(BusinessProfile(tenant_id=bare.id, business_name="Sin catalogo"))
        db.flush()
        empty_conversation = Conversation(tenant_id=bare.id, channel="sandbox")
        db.add(empty_conversation)
        db.flush()

        answer = "Tenemos Inca Kola fresca y Lomo saltado criollo."
        llm = MockLLMProvider(responses=[ChatResult(content=answer)])

        reply = await AgentCore(db, bare.id, llm=llm).respond(
            conversation_id=empty_conversation.id, customer_message="que tienen?"
        )

        assert reply.content == answer
        assert reply.catalog_guard is None
        assert len(llm.chat_calls) == 1

    async def test_a_switched_off_product_does_not_bless_the_name(
        self, db: Session, tenant: Tenant, conversation: Conversation
    ) -> None:
        """A product the owner switched off is one they stopped selling.

        If the guard read deleted or inactive rows it would treat this name as
        legitimate and stay quiet, and the customer would order something the
        kitchen no longer makes - the same damage as an invented name, with a
        row in the database to make it look correct.
        """
        db.add_all(
            [
                Product(
                    tenant_id=tenant.id,
                    name="Lomo saltado criollo",
                    price=Decimal("35.00"),
                    is_active=False,
                ),
                Product(
                    tenant_id=tenant.id,
                    name="Lomo saltado marino",
                    price=Decimal("35.00"),
                    is_active=True,
                    deleted_at=datetime.now(UTC),
                ),
            ]
        )
        db.flush()

        assert _guard_names(db, tenant.id) == ["Lomo saltado"]

        llm = MockLLMProvider(
            responses=[
                ChatResult(content="Tenemos Lomo saltado criollo a S/ 35.00."),
                ChatResult(content="Tenemos Lomo saltado a S/ 32.00."),
            ]
        )
        reply = await AgentCore(db, tenant.id, llm=llm).respond(
            conversation_id=conversation.id, customer_message="que tienen?"
        )

        assert reply.content == "Tenemos Lomo saltado a S/ 32.00."
        assert reply.catalog_guard is not None
        assert reply.catalog_guard["invented"] == ["lomo saltado criollo"]

    async def test_the_catalogue_is_read_once_per_turn(
        self, db: Session, tenant: Tenant, conversation: Conversation, monkeypatch
    ) -> None:
        """The guard checks the first answer and then the retried one, and
        both go through the same read. Latency is already the agent's worst
        number; a guard that queried per check would make it worse for the
        turns that are going badly anyway."""
        reads: list[int] = []
        original = ProductRepository.active_names

        def counted(self, **kwargs):  # type: ignore[no-untyped-def]
            reads.append(1)
            return original(self, **kwargs)

        monkeypatch.setattr(ProductRepository, "active_names", counted)

        llm = MockLLMProvider(
            responses=[
                ChatResult(content="Tenemos Lomo saltado criollo."),
                ChatResult(content="Tenemos Lomo saltado."),
            ]
        )
        await AgentCore(db, tenant.id, llm=llm).respond(
            conversation_id=conversation.id, customer_message="que tienen?"
        )

        assert len(reads) == 1

    async def test_the_read_does_not_outlive_the_turn(
        self, db: Session, tenant: Tenant, conversation: Conversation
    ) -> None:
        """A product added between two customer messages must be sayable on
        the next one. The guard is rebuilt per turn for the same reason the
        tool cache is."""
        invented = ChatResult(content="Tenemos Lomo saltado criollo.")
        # Two for the first turn - the offence and a retry that offends again
        # - and one for the second, which by then is telling the truth.
        llm = MockLLMProvider(responses=[invented, invented, invented])
        core = AgentCore(db, tenant.id, llm=llm)

        blocked = await core.respond(
            conversation_id=conversation.id, customer_message="que tienen?"
        )
        assert blocked.content == CATALOG_GUARD_REPLY

        db.add(
            Product(
                tenant_id=tenant.id,
                name="Lomo saltado criollo",
                price=Decimal("35.00"),
                is_active=True,
            )
        )
        db.flush()

        allowed = await core.respond(conversation_id=conversation.id, customer_message="y ahora?")
        assert allowed.content == "Tenemos Lomo saltado criollo."
        assert allowed.catalog_guard is None

    async def test_a_catalogue_too_large_to_hold_makes_the_guard_stand_down(
        self, db: Session, tenant: Tenant, conversation: Conversation, monkeypatch
    ) -> None:
        """Judging on a partial list would flag real products as invented.
        Standing down is a miss; firing on a correct answer is a wrong
        apology plus a tenant who switches the guard off."""
        monkeypatch.setattr("app.agent.core.MAX_GUARDED_CATALOG_NAMES", 0)

        answer = "Tenemos Lomo saltado criollo."
        llm = MockLLMProvider(responses=[ChatResult(content=answer)])

        reply = await AgentCore(db, tenant.id, llm=llm).respond(
            conversation_id=conversation.id, customer_message="que tienen?"
        )

        assert reply.content == answer
        assert reply.catalog_guard is None

    async def test_the_trace_records_what_the_guard_did(
        self, db: Session, tenant: Tenant, conversation: Conversation
    ) -> None:
        """When the owner asks why their agent apologised instead of
        answering, this row is the only thing that can tell them."""
        llm = MockLLMProvider(
            responses=[
                ChatResult(content="Tenemos Lomo saltado criollo.", model="test-model"),
                ChatResult(content="Tenemos Lomo saltado.", model="test-model"),
            ]
        )
        core = AgentCore(db, tenant.id, llm=llm)
        reply = await core.respond(conversation_id=conversation.id, customer_message="que tienen?")

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

        trace = AgentTraceRepository(db, tenant.id).for_message(message.id)[0]
        assert trace.catalog_guard == {
            "status": "corrected",
            "invented": ["lomo saltado criollo"],
            "still_invented": [],
        }

    async def test_an_ordinary_turn_leaves_the_trace_column_null(
        self, db: Session, tenant: Tenant, conversation: Conversation
    ) -> None:
        """Null on the turns that went fine, so a non-null value is itself the
        signal rather than something to filter out."""
        llm = MockLLMProvider(responses=[ChatResult(content="El Lomo saltado cuesta S/ 32.00.")])
        core = AgentCore(db, tenant.id, llm=llm)
        reply = await core.respond(
            conversation_id=conversation.id, customer_message="cuanto cuesta?"
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

        trace = AgentTraceRepository(db, tenant.id).for_message(message.id)[0]
        assert trace.catalog_guard is None


def _guard_names(db: Session, tenant_id: uuid.UUID) -> list[str]:
    return ProductRepository(db, tenant_id).active_names(limit=100)


class TestWhatsAppMarkup:
    """WhatsApp is not Markdown. `**negrita**` arrives with the asterisks
    showing, which a customer reads as a broken bot - and the model defaults to
    Markdown no matter what the prompt asks for.
    """

    def test_double_asterisks_become_the_single_pair_whatsapp_renders(self) -> None:
        assert whatsapp_text("Total: **S/ 9.00**") == "Total: *S/ 9.00*"

    def test_underscore_bold_becomes_asterisks(self) -> None:
        assert whatsapp_text("Total: __S/ 9.00__") == "Total: *S/ 9.00*"

    def test_double_tildes_become_one(self) -> None:
        assert whatsapp_text("~~agotado~~") == "~agotado~"

    def test_headings_lose_the_marker_and_keep_the_text(self) -> None:
        """WhatsApp has no headings at all, so the hashes are noise."""
        assert whatsapp_text("### Pedido\nLeche") == "Pedido\nLeche"

    def test_single_underscore_italics_are_left_alone(self) -> None:
        """It means the same thing in both, so touching it would be a
        regression rather than a fix."""
        assert whatsapp_text("_importante_") == "_importante_"

    def test_ordinary_prose_is_untouched(self) -> None:
        raw = "Son 3 litros de leche a S/ 3.00 c/u, en total S/ 9.00."
        assert whatsapp_text(raw) == raw

    def test_arithmetic_is_not_mistaken_for_emphasis(self) -> None:
        r"""`(?=\S)` is what keeps the delimiters glued to real text."""
        assert whatsapp_text("2 ** 3 son 8") == "2 ** 3 son 8"

    def test_a_reply_is_converted_on_the_way_out(self) -> None:
        content, unusable = _usable_answer("Te separo **3 litros** de leche.")

        assert unusable is False
        assert content == "Te separo *3 litros* de leche."


class TestReplyLayout:
    def test_a_price_list_keeps_one_product_per_line(self) -> None:
        """This arrived as a single run-on sentence with dashes in it, because
        the cleanup collapsed every newline. On a phone it is unreadable."""
        raw = "Tenemos:\n- Leche: S/ 3.00\n- Queso: S/ 16.00"

        content, unusable = _usable_answer(raw)

        assert unusable is False
        assert content == raw

    def test_runs_of_blank_lines_are_capped(self) -> None:
        content, _ = _usable_answer("Hola, buenas tardes.\n\n\n\nTe ayudo con eso.")

        assert content == "Hola, buenas tardes.\n\nTe ayudo con eso."

    def test_trailing_spaces_are_dropped(self) -> None:
        content, _ = _usable_answer("Leche: S/ 3.00   \nQueso: S/ 16.00")

        assert content == "Leche: S/ 3.00\nQueso: S/ 16.00"
