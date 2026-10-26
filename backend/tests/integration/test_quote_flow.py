"""The quote flow against a database: noted once, asked once, handed over by the tool itself.

The assistant of a water seller quoted badly in a real conversation (`evals/real_conversations.py`):
it asked again for what the customer had said and closed by mood. `anotar_cotizacion` keeps what the
customer gives in the conversation's `cotizacion` fact, says which datum to ask next and, when
nothing is missing, passes the conversation to a person with what was noted written back.
"""

from __future__ import annotations

import uuid
from dataclasses import replace
from decimal import Decimal
from typing import Any

import pytest
from sqlalchemy.orm import Session

from app.agent.pipeline import InboundTurn
from app.agent.tools.handover import customer_refused, derivar_a_humano
from app.agent.tools.quote import anotar_cotizacion
from app.agent.tools.registry import ToolContext
from app.models.business import AgentConfig, Product
from app.models.conversation import Conversation, Message
from app.models.tenant import Tenant
from app.process.facts import Cotizacion, Facts
from app.process.service import ProcessConfigCache, ProcessConfigService
from app.providers.llm import ChatResult, MockLLMProvider
from app.repositories.product import ProductRepository
from tests.integration.test_handover import _outbound
from tests.integration.test_turn_pipeline import (
    _call,
    _conversation,
    _pipeline,
    _stored,
    _tenant,
)


def _quoting_tenant(
    db: Session,
    *products: tuple[str, list[str]],
    capacidades: tuple[str, ...] = ("atender", "cotizar"),
) -> Tenant:
    tenant = _tenant(db, template="servicios")
    config = db.query(AgentConfig).filter_by(tenant_id=tenant.id).one()
    config.capacidades = list(capacidades)
    for name, asks in products:
        _product(db, tenant, name, asks)
    db.flush()
    return tenant


def _product(db: Session, tenant: Tenant, name: str, asks: list[str], **extra: Any) -> Product:
    product = Product(
        tenant_id=tenant.id,
        name=name,
        price=extra.pop("price", Decimal("0")),
        modo_venta=extra.pop("modo_venta", "cotizacion"),
        datos_a_pedir=asks,
        **extra,
    )
    db.add(product)
    db.flush()
    return product


def _says(db: Session, tenant: Tenant, conversation: Conversation, text: str) -> None:
    db.add(
        Message(
            tenant_id=tenant.id,
            conversation_id=conversation.id,
            direction="inbound",
            author="customer",
            content=text,
            msg_type="text",
        )
    )
    db.flush()


def _context(db: Session, tenant: Tenant, conversation: Conversation, **facts: Any) -> ToolContext:
    options = ProcessConfigService(db, cache=ProcessConfigCache()).effective(tenant.id).options
    return ToolContext(
        session=db,
        tenant_id=tenant.id,
        llm=MockLLMProvider(),
        conversation_id=conversation.id,
        options=options,
        facts=Facts(**facts),
    )


WATER = ("Bidón 20 L", ["cantidad", "fecha", "distrito o dirección"])


class TestWhatTheBusinessAsks:
    def test_each_datum_once_across_the_quoted_products(self, db: Session) -> None:
        tenant = _quoting_tenant(
            db, WATER, ("Dispensador", ["Cantidad", "tipo de local"]), ("Hielo", [])
        )

        fields = ProductRepository(db, tenant.id).quote_fields()

        assert fields == ("cantidad", "fecha", "distrito o dirección", "tipo de local")

    def test_products_with_a_price_a_deleted_one_and_an_off_one_ask_nothing(
        self, db: Session
    ) -> None:
        tenant = _quoting_tenant(db, WATER)
        _product(db, tenant, "Agua", ["color"], modo_venta="precio_fijo", price=Decimal("5"))
        _product(db, tenant, "Apagado", ["marca"], is_active=False)
        removed = _product(db, tenant, "Borrado", ["talla"])
        ProductRepository(db, tenant.id).soft_delete(removed.id)

        assert ProductRepository(db, tenant.id).quote_fields() == (
            "cantidad",
            "fecha",
            "distrito o dirección",
        )

    def test_another_business_s_data_never_show_up(self, db: Session) -> None:
        mine = _quoting_tenant(db, WATER)
        _quoting_tenant(db, ("Servicio ajeno", ["secreto del otro negocio"]))

        assert "secreto del otro negocio" not in ProductRepository(db, mine.id).quote_fields()

    def test_the_options_carry_them_only_for_a_business_that_quotes(self, db: Session) -> None:
        quoting = _quoting_tenant(db, WATER)
        plain = _quoting_tenant(db, WATER, capacidades=("atender", "vender"))
        service = ProcessConfigService(db, cache=ProcessConfigCache())

        on = service.effective(quoting.id).options
        off = service.effective(plain.id).options

        assert on.cotizar and on.datos_cotizacion == (
            "cantidad",
            "fecha",
            "distrito o dirección",
        )
        assert not off.cotizar and off.datos_cotizacion == ()


class TestNotingTheQuote:
    async def test_it_keeps_what_was_given_and_says_which_one_to_ask(self, db: Session) -> None:
        tenant = _quoting_tenant(db, WATER)
        conversation = _conversation(db, tenant)
        context = _context(db, tenant, conversation)

        result = await anotar_cotizacion(context, {"cantidad": "20 por semana"})

        assert result.status == "hecho" and result.texto is not None
        assert "Falta: fecha, distrito o dirección" in result.texto
        assert "no como un formulario" in result.texto
        assert context.facts_patch["cotizacion"] == {
            "producto": "Bidón 20 L",
            "producto_id": str(next(iter(_ids(db, tenant)))),
            "datos": {"cantidad": "20 por semana"},
            "pide": ["cantidad", "fecha", "distrito o dirección"],
            "preguntado": "fecha",
        }
        assert context.ending.reply is None

    async def test_several_data_in_one_message_are_noted_together(self, db: Session) -> None:
        tenant = _quoting_tenant(db, WATER)
        conversation = _conversation(db, tenant)
        context = _context(db, tenant, conversation)

        result = await anotar_cotizacion(context, {"cantidad": "5", "fecha": "el lunes"})

        assert result.texto is not None and "Falta: distrito o dirección" in result.texto
        assert context.facts_patch["cotizacion"]["datos"] == {"cantidad": "5", "fecha": "el lunes"}
        assert context.facts_patch["cotizacion"]["preguntado"] == "distrito o dirección"

    async def test_what_was_noted_in_an_earlier_turn_is_kept(self, db: Session) -> None:
        tenant = _quoting_tenant(db, WATER)
        conversation = _conversation(db, tenant)
        before = Cotizacion(
            producto="Bidón 20 L",
            datos={"cantidad": "5"},
            pide=["cantidad", "fecha", "distrito o dirección"],
            preguntado="fecha",
        )
        context = _context(db, tenant, conversation, cotizacion=before)

        await anotar_cotizacion(context, {"fecha": "mañana"})

        assert context.facts_patch["cotizacion"]["datos"] == {"cantidad": "5", "fecha": "mañana"}

    async def test_a_list_of_data_works_as_well_as_the_fields(self, db: Session) -> None:
        tenant = _quoting_tenant(db, WATER)
        conversation = _conversation(db, tenant)
        context = _context(db, tenant, conversation)

        await anotar_cotizacion(
            context,
            {"datos": [{"dato": "CANTIDAD", "valor": "12"}, {"dato": "Fecha", "valor": "hoy"}]},
        )

        assert context.facts_patch["cotizacion"]["datos"] == {"cantidad": "12", "fecha": "hoy"}

    async def test_which_product_is_noted_when_there_are_several(self, db: Session) -> None:
        tenant = _quoting_tenant(db, WATER, ("Dispensador", ["cantidad", "tipo de local"]))
        conversation = _conversation(db, tenant)
        context = _context(db, tenant, conversation)

        await anotar_cotizacion(context, {"cantidad": "3", "producto": "ambos"})

        quote = context.facts_patch["cotizacion"]
        assert quote["datos"] == {"cantidad": "3", "producto": "ambos"}
        # Nobody chose one, so the asked list is the union and the product is not asked as a datum.
        assert quote["pide"] == ["cantidad", "fecha", "distrito o dirección", "tipo de local"]

    async def test_an_answer_that_is_not_a_date_is_not_noted_as_one(self, db: Session) -> None:
        tenant = _quoting_tenant(db, WATER)
        conversation = _conversation(db, tenant)
        context = _context(db, tenant, conversation)

        result = await anotar_cotizacion(context, {"cantidad": "20", "fecha": "para negocio"})

        assert result.texto is not None and "No se anotó fecha" in result.texto
        assert context.facts_patch["cotizacion"]["datos"] == {"cantidad": "20"}
        assert context.facts_patch["cotizacion"]["preguntado"] == "fecha"

    async def test_only_a_wrong_datum_is_still_an_answer_to_correct(self, db: Session) -> None:
        tenant = _quoting_tenant(db, WATER)
        conversation = _conversation(db, tenant)
        context = _context(db, tenant, conversation)

        result = await anotar_cotizacion(context, {"fecha": "semanales"})

        assert result.status == "hecho" and result.texto is not None
        assert "No se anotó fecha" in result.texto
        assert context.facts_patch["cotizacion"]["datos"] == {}

    async def test_a_use_noted_as_the_product_is_not_kept(self, db: Session) -> None:
        tenant = _quoting_tenant(db, WATER, ("Dispensador", ["cantidad"]))
        conversation = _conversation(db, tenant)
        context = _context(db, tenant, conversation)

        result = await anotar_cotizacion(context, {"cantidad": "20", "producto": "para negocio"})

        assert result.texto is not None and "No se anotó producto" in result.texto
        assert context.facts_patch["cotizacion"]["datos"] == {"cantidad": "20"}

    async def test_an_empty_call_is_an_error(self, db: Session) -> None:
        tenant = _quoting_tenant(db, WATER)
        conversation = _conversation(db, tenant)

        result = await anotar_cotizacion(_context(db, tenant, conversation), {})

        assert result.status == "error"

    async def test_without_a_conversation_it_is_unavailable(self, db: Session) -> None:
        tenant = _quoting_tenant(db, WATER)
        context = replace(_context(db, tenant, _conversation(db, tenant)), conversation_id=None)

        result = await anotar_cotizacion(context, {"cantidad": "1"})

        assert result.status == "no_disponible"

    async def test_too_many_data_are_refused(self, db: Session) -> None:
        tenant = _quoting_tenant(db, WATER)
        conversation = _conversation(db, tenant)
        many = {f"dato {i}": "x" for i in range(9)}
        context = _context(
            db,
            tenant,
            conversation,
            cotizacion=Cotizacion(datos=many, pide=["cantidad"]),
        )

        result = await anotar_cotizacion(context, {"cantidad": "1"})

        assert result.status == "error"


class TestNoDataNeededToClose:
    async def test_the_customer_who_will_not_say_closes_that_datum(self, db: Session) -> None:
        tenant = _quoting_tenant(db, WATER)
        conversation = _conversation(db, tenant)
        before = Cotizacion(
            producto="Bidón 20 L",
            datos={"cantidad": "5", "distrito o dirección": "Surco"},
            pide=["cantidad", "fecha", "distrito o dirección"],
            preguntado="fecha",
        )
        _says(db, tenant, conversation, "todavía no sé para cuándo")
        context = _context(db, tenant, conversation, cotizacion=before)

        result = await anotar_cotizacion(context, {"fecha": "sin dato"})

        assert result.status == "hecho"
        assert context.ending.reply is not None
        assert "fecha: por definir" in context.ending.reply

    async def test_sin_dato_without_a_refusal_does_not_close_the_quote(self, db: Session) -> None:
        tenant = _quoting_tenant(db, WATER)
        conversation = _conversation(db, tenant)
        before = Cotizacion(
            producto="Bidón 20 L",
            datos={"cantidad": "5", "distrito o dirección": "Surco"},
            pide=["cantidad", "fecha", "distrito o dirección"],
            preguntado="fecha",
        )
        _says(db, tenant, conversation, "para negocio")
        context = _context(db, tenant, conversation, cotizacion=before)

        result = await anotar_cotizacion(context, {"fecha": "sin dato"})

        assert context.ending.reply is None
        assert result.texto is not None and "No anotes «sin dato» para fecha" in result.texto
        assert "fecha" not in context.facts_patch["cotizacion"]["datos"]

    def test_the_refusal_is_what_the_customer_wrote_since_the_assistant_spoke(
        self, db: Session
    ) -> None:
        tenant = _quoting_tenant(db, WATER)
        conversation = _conversation(db, tenant)
        _says(db, tenant, conversation, "no sé")
        db.add(
            Message(
                tenant_id=tenant.id,
                conversation_id=conversation.id,
                direction="outbound",
                author="agent",
                content="¿Para qué fecha?",
                msg_type="text",
            )
        )
        _says(db, tenant, conversation, "para negocio")
        db.flush()

        # «no sé» was said before the assistant's last question: it is not an answer to it.
        assert not customer_refused(_context(db, tenant, conversation))


class TestTheQuoteGoesToAPersonByItself:
    async def test_when_nothing_is_missing_it_is_handed_over_with_a_recap(
        self, db: Session
    ) -> None:
        tenant = _quoting_tenant(db, WATER)
        conversation = _conversation(db, tenant)
        before = Cotizacion(
            producto="Bidón 20 L",
            datos={"cantidad": "5", "fecha": "el lunes"},
            pide=["cantidad", "fecha", "distrito o dirección"],
            preguntado="distrito o dirección",
        )
        context = _context(db, tenant, conversation, cotizacion=before)

        result = await anotar_cotizacion(context, {"distrito_o_direccion": "Av. Larco 120"})

        assert result.data["motivo"] == "cotizacion"
        derivado = context.facts_patch["derivado"]
        assert derivado["motivo"] == "cotizacion"
        assert derivado["datos"] == {
            "cantidad": "5",
            "fecha": "el lunes",
            "distrito o dirección": "Av. Larco 120",
        }
        assert derivado["resumen"].startswith("Cotización de Bidón 20 L.")
        # The next quote starts from nothing.
        assert context.facts_patch["cotizacion"] is None
        assert context.ending.reply is not None
        assert (
            "Anoté: Bidón 20 L; cantidad: 5; fecha: el lunes; distrito o dirección: Av. Larco 120."
            in (context.ending.reply)
        )

    async def test_a_product_that_asks_for_nothing_is_not_closed_by_the_tool(
        self, db: Session
    ) -> None:
        tenant = _quoting_tenant(db, ("Visita técnica", []))
        conversation = _conversation(db, tenant)
        context = _context(db, tenant, conversation)

        result = await anotar_cotizacion(context, {"producto": "Visita técnica"})

        assert context.ending.reply is None
        assert result.texto is not None and "derivar_a_humano" in result.texto

    async def test_the_model_cannot_hand_over_an_incomplete_quote(self, db: Session) -> None:
        tenant = _quoting_tenant(db, WATER)
        conversation = _conversation(db, tenant)
        before = Cotizacion(
            producto="Bidón 20 L",
            datos={"cantidad": "5"},
            pide=["cantidad", "fecha", "distrito o dirección"],
            preguntado="fecha",
        )
        _says(db, tenant, conversation, "son 5")
        context = _context(db, tenant, conversation, cotizacion=before)

        result = await derivar_a_humano(context, {"motivo": "cotizacion", "resumen": "5 bidones"})

        assert result.status == "no_disponible" and result.decir is not None
        assert "Aún falta fecha, distrito o dirección" in result.decir
        assert context.ending.reply is None and "derivado" not in context.facts_patch

    async def test_nothing_can_be_handed_over_before_anything_is_noted(self, db: Session) -> None:
        tenant = _quoting_tenant(db, WATER)
        conversation = _conversation(db, tenant)
        _says(db, tenant, conversation, "quiero empezar a vender, ¿distribuyen por mayor?")
        context = _context(db, tenant, conversation)

        result = await derivar_a_humano(context, {"motivo": "cotizacion", "resumen": "quiere agua"})

        assert result.status == "no_disponible" and result.decir is not None
        assert "Aún no tienes ningún dato de la cotización" in result.decir
        assert context.ending.reply is None and "derivado" not in context.facts_patch

    async def test_a_customer_who_wants_no_questions_can_still_be_handed_over(
        self, db: Session
    ) -> None:
        tenant = _quoting_tenant(db, WATER)
        conversation = _conversation(db, tenant)
        _says(db, tenant, conversation, "no quiero dar datos, que me llamen")
        context = _context(db, tenant, conversation)

        await derivar_a_humano(context, {"motivo": "cotizacion", "resumen": "que lo llamen"})

        assert context.facts_patch["derivado"]["motivo"] == "cotizacion"

    async def test_it_can_when_the_customer_will_not_give_the_rest(self, db: Session) -> None:
        tenant = _quoting_tenant(db, WATER)
        conversation = _conversation(db, tenant)
        before = Cotizacion(
            producto="Bidón 20 L",
            datos={"cantidad": "5"},
            pide=["cantidad", "fecha", "distrito o dirección"],
        )
        _says(db, tenant, conversation, "no quiero dar más datos, que me llamen")
        context = _context(db, tenant, conversation, cotizacion=before)

        await derivar_a_humano(context, {"motivo": "cotizacion", "resumen": "5 bidones"})

        assert context.facts_patch["derivado"]["motivo"] == "cotizacion"
        assert context.facts_patch["derivado"]["datos"] == {"cantidad": "5"}
        assert context.ending.reply is not None and "Anoté: Bidón 20 L; cantidad: 5." in (
            context.ending.reply
        )

    async def test_the_guard_applies_to_quotes_only(self, db: Session) -> None:
        tenant = _quoting_tenant(db, WATER)
        conversation = _conversation(db, tenant)
        before = Cotizacion(datos={}, pide=["cantidad"])
        context = _context(db, tenant, conversation, cotizacion=before)

        await derivar_a_humano(context, {"motivo": "queja", "resumen": "llegó roto"})

        assert context.facts_patch["derivado"]["motivo"] == "queja"
        assert "datos" not in context.facts_patch["derivado"]


class TestInTheTurn:
    async def test_the_turn_ends_with_the_recap_once_the_last_datum_is_noted(
        self, db: Session
    ) -> None:
        tenant = _quoting_tenant(db, WATER)
        conversation = _conversation(db, tenant)
        llm = MockLLMProvider()
        llm.queue(
            ChatResult(
                content="",
                tool_calls=(
                    _call(
                        "anotar_cotizacion",
                        cantidad="5",
                        fecha="el lunes",
                        distrito_o_direccion="Av. Larco 120",
                    ),
                ),
            )
        )
        llm.queue(ChatResult(content="Lo que el modelo diría de más."))

        await _pipeline(db, tenant, conversation, llm).run(
            conversation.id,
            InboundTurn(text="5 bidones para el lunes, en Av. Larco 120"),
        )

        assert len(llm.chat_calls) == 1
        (reply,) = _outbound(db, conversation)
        assert "Anoté: " in reply and "cantidad: 5" in reply
        stored = _stored(db, tenant, conversation)
        assert stored["derivado"]["motivo"] == "cotizacion"
        assert "cotizacion" not in stored

    async def test_a_claim_of_having_noted_without_the_tool_gets_one_nudge(
        self, db: Session
    ) -> None:
        tenant = _quoting_tenant(db, WATER)
        conversation = _conversation(db, tenant)
        llm = MockLLMProvider()
        llm.queue(ChatResult(content="Listo, ya anoté tus 5 bidones. ¿Para qué fecha?"))
        llm.queue(ChatResult(content="", tool_calls=(_call("anotar_cotizacion", cantidad="5"),)))
        llm.queue(ChatResult(content="Anotado. ¿Para qué fecha lo necesitas?"))

        await _pipeline(db, tenant, conversation, llm).run(
            conversation.id, InboundTurn(text="quiero 5 bidones")
        )

        assert len(llm.chat_calls) == 3
        nudge = llm.chat_calls[1].messages[-1]
        assert nudge.role == "user" and "no llamaste a anotar_cotizacion" in nudge.content
        assert _stored(db, tenant, conversation)["cotizacion"]["datos"] == {"cantidad": "5"}
        assert _outbound(db, conversation) == ["Anotado. ¿Para qué fecha lo necesitas?"]

    async def test_the_nudge_comes_once_and_the_reply_still_goes_out(self, db: Session) -> None:
        tenant = _quoting_tenant(db, WATER)
        conversation = _conversation(db, tenant)
        llm = MockLLMProvider()
        llm.queue(ChatResult(content="Ya anoté eso."))
        llm.queue(ChatResult(content="Ya anoté eso, de verdad."))

        await _pipeline(db, tenant, conversation, llm).run(
            conversation.id, InboundTurn(text="hola, ¿cómo va?")
        )

        assert len(llm.chat_calls) == 2
        assert _outbound(db, conversation) == ["Ya anoté eso, de verdad."]

    async def test_an_answer_the_model_did_not_note_gets_one_nudge(self, db: Session) -> None:
        tenant = _quoting_tenant(db, WATER)
        conversation = _conversation(db, tenant)
        first = MockLLMProvider()
        first.queue(ChatResult(content="", tool_calls=(_call("anotar_cotizacion", cantidad="5"),)))
        first.queue(ChatResult(content="Anotado. ¿Para qué fecha lo necesitas?"))
        await _pipeline(db, tenant, conversation, first).run(
            conversation.id, InboundTurn(text="necesito 5 bidones")
        )
        second = MockLLMProvider()
        second.queue(ChatResult(content="Perfecto. ¿En qué distrito sería?"))
        second.queue(
            ChatResult(content="", tool_calls=(_call("anotar_cotizacion", fecha="el lunes"),))
        )
        second.queue(ChatResult(content="Anotado. ¿En qué distrito sería?"))

        await _pipeline(db, tenant, conversation, second).run(
            conversation.id, InboundTurn(text="desde el lunes")
        )

        assert len(second.chat_calls) == 3
        nudge = second.chat_calls[1].messages[-1]
        assert nudge.role == "user" and "respondido «fecha»" in nudge.content
        datos = _stored(db, tenant, conversation)["cotizacion"]["datos"]
        assert datos == {"cantidad": "5", "fecha": "el lunes"}
        assert _outbound(db, conversation)[-1] == "Anotado. ¿En qué distrito sería?"

    async def test_an_invented_reason_to_buy_gets_one_nudge_and_is_not_sent(
        self, db: Session
    ) -> None:
        tenant = _quoting_tenant(db, BIDON, MESA)
        conversation = _conversation(db, tenant)
        llm = MockLLMProvider()
        llm.queue(ChatResult(content="Te conviene el bidón: es el formato más usado para reventa."))
        llm.queue(ChatResult(content="Tenemos bidón de 20 L y botellas. ¿Cuál quieres cotizar?"))

        await _pipeline(db, tenant, conversation, llm).run(
            conversation.id, InboundTurn(text="quiero empezar a vender agua")
        )

        assert len(llm.chat_calls) == 2
        assert "razón de venta" in llm.chat_calls[1].messages[-1].content
        assert _outbound(db, conversation) == [
            "Tenemos bidón de 20 L y botellas. ¿Cuál quieres cotizar?"
        ]

    async def test_a_message_the_model_answered_without_noting_gets_one_nudge(
        self, db: Session
    ) -> None:
        tenant = _quoting_tenant(db, BIDON)
        conversation = _conversation(db, tenant)
        first = MockLLMProvider()
        first.queue(ChatResult(content="", tool_calls=(_call("anotar_cotizacion", cantidad="8"),)))
        first.queue(ChatResult(content="¿Para qué fecha y a qué distrito?"))
        await _pipeline(db, tenant, conversation, first).run(
            conversation.id, InboundTurn(text="quiero 8 bidones")
        )
        second = MockLLMProvider()
        second.queue(ChatResult(content="Entendido. ¿En qué distrito queda tu hogar?"))
        second.queue(ChatResult(content="", tool_calls=(_call("anotar_cotizacion", uso="hogar"),)))
        second.queue(ChatResult(content="Entendido. ¿En qué distrito queda tu hogar?"))

        await _pipeline(db, tenant, conversation, second).run(
            conversation.id, InboundTurn(text="para mi hogar")
        )

        assert len(second.chat_calls) == 3
        assert "mi hogar" in second.chat_calls[1].messages[-1].content
        datos = _stored(db, tenant, conversation)["cotizacion"]["datos"]
        assert datos["uso"] == "hogar"

    async def test_a_business_that_does_not_quote_is_never_nudged(self, db: Session) -> None:
        tenant = _quoting_tenant(db, WATER, capacidades=("atender", "vender"))
        conversation = _conversation(db, tenant)
        llm = MockLLMProvider()
        llm.queue(ChatResult(content="Ya anoté tu consulta."))

        await _pipeline(db, tenant, conversation, llm).run(
            conversation.id, InboundTurn(text="hola")
        )

        assert len(llm.chat_calls) == 1

    async def test_the_prompt_tells_the_model_what_it_has(self, db: Session) -> None:
        tenant = _quoting_tenant(db, WATER)
        conversation = _conversation(db, tenant)
        first = MockLLMProvider()
        first.queue(
            ChatResult(
                content="", tool_calls=(_call("anotar_cotizacion", cantidad="20 semanales"),)
            )
        )
        first.queue(ChatResult(content="Anotado. ¿Para qué fecha?"))
        await _pipeline(db, tenant, conversation, first).run(
            conversation.id, InboundTurn(text="necesito 20 bidones semanales")
        )

        second = MockLLMProvider()
        second.queue(ChatResult(content="¿En qué distrito?"))
        await _pipeline(db, tenant, conversation, second).run(
            conversation.id, InboundTurn(text="el lunes")
        )

        prompt = second.chat_calls[0].messages[0].content
        assert "Cotización en curso (Bidón 20 L)" in prompt
        assert "Ya dio: cantidad = 20 semanales" in prompt
        assert "Falta: fecha, distrito o dirección" in prompt
        assert "COTIZACIÓN:" in prompt
        tools = {spec.name for spec in second.chat_calls[0].tools or ()}
        assert "anotar_cotizacion" in tools


def _ids(db: Session, tenant: Tenant) -> list[uuid.UUID]:
    return [p.id for p in ProductRepository(db, tenant.id).list_active()]


@pytest.mark.parametrize("capacidades", [("atender",), ("atender", "vender")])
def test_a_business_without_the_capability_never_has_the_tool(
    db: Session, capacidades: tuple[str, ...]
) -> None:
    from app.agent.tools.registry import ToolScope, tools_of_stage
    from app.process.templates.base import Stage

    tenant = _quoting_tenant(db, WATER, capacidades=capacidades)
    config = ProcessConfigService(db, cache=ProcessConfigCache()).effective(tenant.id)

    scope = ToolScope(
        template=config.template, stage=Stage.IDENTIFICAR, facts=Facts(), options=config.options
    )

    assert "anotar_cotizacion" not in tools_of_stage(scope)


BIDON = (
    "Bidon de Agua de 20L",
    [
        "¿Cuántas unidades de agua de 20 litros necesitas?",
        "¿Para qué fecha necesitas el pedido?",
        "¿A qué distrito o dirección deseas recibirlo?",
        "¿Es para tu hogar, oficina o negocio?",
        "¿Necesitas una entrega puntual o pedidos recurrentes?",
    ],
)
MESA = (
    "Agua de Mesa de 625 ml",
    [
        "¿Cuántas botellas de 500 ml necesitas?",
        "¿Para qué fecha necesitas el pedido?",
        "¿A qué distrito o dirección se realizará la entrega?",
        "¿Es para consumo personal, un evento o tu negocio?",
    ],
)
USUAL = ["cantidad", "fecha", "distrito o dirección", "uso", "frecuencia"]


class TestWithTheOwnersQuestions:
    """Bebidas yanayacu writes questions, not names: they are noted under short ones."""

    def test_the_questions_become_the_names_the_assistant_notes_under(self, db: Session) -> None:
        tenant = _quoting_tenant(db, BIDON, MESA)

        assert ProductRepository(db, tenant.id).quote_fields() == tuple(USUAL)

    async def test_the_product_the_customer_names_decides_what_is_asked(self, db: Session) -> None:
        tenant = _quoting_tenant(db, BIDON, MESA)
        conversation = _conversation(db, tenant)
        context = _context(db, tenant, conversation)

        await anotar_cotizacion(context, {"producto": "agua de mesa de 625 ml", "cantidad": "200"})

        assert context.facts_patch["cotizacion"]["pide"] == USUAL[:4]
        assert context.facts_patch["cotizacion"]["producto"] == "Agua de Mesa de 625 ml"

    async def test_the_product_with_one_more_question_asks_it(self, db: Session) -> None:
        tenant = _quoting_tenant(db, BIDON, MESA)
        conversation = _conversation(db, tenant)
        context = _context(db, tenant, conversation)

        await anotar_cotizacion(context, {"producto": "bidón de 20 litros", "cantidad": "20"})

        assert context.facts_patch["cotizacion"]["pide"] == USUAL

    async def test_what_is_missing_comes_with_the_owner_s_words(self, db: Session) -> None:
        tenant = _quoting_tenant(db, BIDON, MESA)
        conversation = _conversation(db, tenant)
        context = _context(db, tenant, conversation)

        result = await anotar_cotizacion(
            context, {"producto": "bidón de 20 litros", "cantidad": "20"}
        )

        assert result.texto is not None
        assert "fecha (¿Para qué fecha necesitas el pedido?)" in result.texto
        # The model is told how to talk, not which question to ask next.
        assert "SOLO" not in result.texto

    async def test_a_question_worded_differently_per_product_is_not_quoted(
        self, db: Session
    ) -> None:
        tenant = _quoting_tenant(db, BIDON, MESA)
        conversation = _conversation(db, tenant)
        context = _context(db, tenant, conversation)

        # No product chosen: «cuántas unidades de 20 litros» would be asked of a 500 ml customer.
        result = await anotar_cotizacion(context, {"fecha": "el lunes"})

        assert result.texto is not None and "Falta: cantidad," in result.texto
        assert "litros" not in result.texto and "500 ml" not in result.texto

    async def test_an_answer_to_another_question_is_kept_and_what_is_missing_still_listed(
        self, db: Session
    ) -> None:
        tenant = _quoting_tenant(db, BIDON)
        conversation = _conversation(db, tenant)
        before = Cotizacion(
            producto="Bidon de Agua de 20L",
            datos={"cantidad": "12"},
            pide=USUAL,
            preguntado="fecha",
        )
        context = _context(db, tenant, conversation, cotizacion=before)

        result = await anotar_cotizacion(context, {"distrito_o_direccion": "La Victoria"})

        assert context.facts_patch["cotizacion"]["datos"] == {
            "cantidad": "12",
            "distrito o dirección": "La Victoria",
        }
        assert result.texto is not None
        assert "Falta: fecha (¿Para qué fecha necesitas el pedido?), uso" in result.texto

    async def test_with_everything_it_goes_to_a_person_with_the_short_names_in_the_recap(
        self, db: Session
    ) -> None:
        tenant = _quoting_tenant(db, BIDON)
        conversation = _conversation(db, tenant)
        context = _context(db, tenant, conversation)

        await anotar_cotizacion(
            context,
            {
                "cantidad": "15",
                "fecha": "el lunes",
                "distrito_o_direccion": "La Victoria, Chiclayo",
                "uso": "tienda",
                "frecuencia": "todas las semanas",
            },
        )

        assert context.facts_patch["derivado"]["motivo"] == "cotizacion"
        assert context.ending.reply is not None
        assert "cantidad: 15" in context.ending.reply and "uso: tienda" in context.ending.reply

    async def test_a_datum_the_customer_never_said_is_not_noted(self, db: Session) -> None:
        tenant = _quoting_tenant(db, BIDON)
        conversation = _conversation(db, tenant)
        _says(db, tenant, conversation, "quiero el bidón de 20 L, unos 15, para mi tienda")
        context = _context(db, tenant, conversation)

        result = await anotar_cotizacion(
            context, {"cantidad": "15", "uso": "tienda", "frecuencia": "recurrente"}
        )

        assert result.texto is not None and "No se anotó frecuencia" in result.texto
        datos = context.facts_patch["cotizacion"]["datos"]
        assert datos == {"cantidad": "15", "uso": "tienda"}
        assert "Falta: fecha" in result.texto

    async def test_the_answer_to_what_was_just_asked_is_taken_in_the_models_words(
        self, db: Session
    ) -> None:
        tenant = _quoting_tenant(db, BIDON)
        conversation = _conversation(db, tenant)
        _says(db, tenant, conversation, "unos 10 bidones para mi oficina")
        db.add(
            Message(
                tenant_id=tenant.id,
                conversation_id=conversation.id,
                direction="outbound",
                author="agent",
                content="Gracias. ¿Lo necesitas como entrega puntual o pedidos recurrentes?",
                msg_type="text",
            )
        )
        db.flush()
        _says(db, tenant, conversation, "compra puntual, una sola vez")
        context = _context(db, tenant, conversation)

        await anotar_cotizacion(context, {"frecuencia": "única"})

        assert context.facts_patch["cotizacion"]["datos"] == {"frecuencia": "única"}

    async def test_what_was_not_asked_is_still_checked_against_what_the_customer_said(
        self, db: Session
    ) -> None:
        tenant = _quoting_tenant(db, BIDON)
        conversation = _conversation(db, tenant)
        _says(db, tenant, conversation, "unos 10 bidones para mi oficina")
        db.add(
            Message(
                tenant_id=tenant.id,
                conversation_id=conversation.id,
                direction="outbound",
                author="agent",
                content="Perfecto. ¿Para qué fecha lo necesitas?",
                msg_type="text",
            )
        )
        db.flush()
        _says(db, tenant, conversation, "el lunes")
        context = _context(db, tenant, conversation)

        result = await anotar_cotizacion(context, {"fecha": "lunes", "frecuencia": "única"})

        assert context.facts_patch["cotizacion"]["datos"] == {"fecha": "lunes"}
        assert result.texto is not None and "No se anotó frecuencia" in result.texto

    async def test_a_use_said_for_the_address_is_noted_as_the_use(self, db: Session) -> None:
        tenant = _quoting_tenant(db, BIDON)
        conversation = _conversation(db, tenant)
        _says(db, tenant, conversation, "para mi hogar")
        before = Cotizacion(
            producto="Bidon de Agua de 20L",
            datos={"cantidad": "8"},
            pide=USUAL,
            preguntado="distrito o dirección",
        )
        context = _context(db, tenant, conversation, cotizacion=before)

        result = await anotar_cotizacion(context, {"distrito_o_direccion": "mi hogar"})

        datos = context.facts_patch["cotizacion"]["datos"]
        assert datos["uso"] == "mi hogar" and "distrito o dirección" not in datos
        assert (
            result.texto is not None and "distrito o dirección" in result.texto.split("Falta:")[1]
        )

    async def test_the_customer_not_knowing_the_only_thing_missing_closes_the_quote(
        self, db: Session
    ) -> None:
        tenant = _quoting_tenant(db, MESA)
        conversation = _conversation(db, tenant)
        before = Cotizacion(
            producto="Agua de Mesa de 625 ml",
            datos={"cantidad": "5", "distrito o dirección": "Surco", "uso": "gimnasio"},
            pide=USUAL[:4],
            preguntado="fecha",
        )
        _says(db, tenant, conversation, "5 botellas para mi gimnasio, entrega en Surco")
        _says(db, tenant, conversation, "todavía no sé la fecha, lo veo luego")
        context = _context(db, tenant, conversation, cotizacion=before)

        # The model answered «queda pendiente» and noted nothing new.
        await anotar_cotizacion(context, {"uso": "gimnasio"})

        assert context.facts_patch["derivado"]["datos"]["fecha"] == "sin dato"
        assert context.ending.reply is not None and "fecha: por definir" in context.ending.reply

    async def test_not_knowing_one_thing_does_not_close_a_quote_that_misses_two(
        self, db: Session
    ) -> None:
        tenant = _quoting_tenant(db, MESA)
        conversation = _conversation(db, tenant)
        before = Cotizacion(
            producto="Agua de Mesa de 625 ml",
            datos={"cantidad": "5", "uso": "gimnasio"},
            pide=USUAL[:4],
            preguntado="fecha",
        )
        _says(db, tenant, conversation, "todavía no sé la fecha")
        context = _context(db, tenant, conversation, cotizacion=before)

        await anotar_cotizacion(context, {"cantidad": "5"})

        assert "derivado" not in context.facts_patch and context.ending.reply is None

    async def test_saying_the_date_is_not_known_closes_it_when_asked(self, db: Session) -> None:
        tenant = _quoting_tenant(db, MESA)
        conversation = _conversation(db, tenant)
        before = Cotizacion(
            producto="Agua de Mesa de 625 ml",
            datos={"cantidad": "5", "distrito o dirección": "Surco", "uso": "gimnasio"},
            pide=USUAL[:4],
            preguntado="fecha",
        )
        _says(db, tenant, conversation, "todavía no sé la fecha, lo veo luego")
        context = _context(db, tenant, conversation, cotizacion=before)

        await anotar_cotizacion(context, {"fecha": "sin dato"})

        assert context.facts_patch["derivado"]["datos"]["fecha"] == "sin dato"
        assert context.ending.reply is not None and "fecha: por definir" in context.ending.reply
