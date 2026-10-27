"""The quote flow, without a database: what the business asks for, noted once, asked once.

A real conversation (`evals/real_conversations.py`) showed the assistant saying «Sí, contamos con…»
in every message, asking again for what the customer had said and closing the quote by mood. The
pieces that fix it are checked here one by one; `tests/integration/test_quote_flow.py` runs them
together against a database and `evals/` against the real model.
"""

from __future__ import annotations

from dataclasses import replace
from typing import Any

import pytest

from app.agent.core import (
    ADD_NOTE,
    ADDED_CLAIM_NOTE,
    NEXT_STEP_NOTE,
    PAY_NOTE,
    PERMISSION_NOTE,
    PITCH_NOTE,
    PROMISE_NOTE,
    TOO_MANY_NOTE,
    UNBACKED_CLAIMS,
    UNNOTED_NOTE,
    _missed_note,
    _unbacked_claim,
    accepted_an_offer_to_add,
)
from app.agent.prompts.stage import (
    QUOTE_QUALIFIES,
    QUOTE_RULES,
    SECTION_CAPS,
    _quote_line,
    _StateLimits,
    build_stage_prompt,
    estimate_tokens,
)
from app.agent.tools.definitions import default_catalog, registry_for_stage
from app.agent.tools.handover import recap_line
from app.agent.tools.quote import (
    _DATE,
    _QUANTITY,
    NO_DATA,
    _given,
    _grounded,
    _is_a_use,
    _names_a_product,
    quote_key,
    unnoted_answer,
)
from app.agent.tools.registry import ToolContext, ToolScope, tools_of_stage
from app.process.facts import Cotizacion, Facts, Pedido, PedidoItem
from app.process.templates import QUOTE_TOOL_NAMES, TEMPLATES
from app.process.templates.base import EffectiveOptions, Stage
from app.process.text import plain
from tests.unit.test_stage_prompt import NOW, make_config

SERVICES = TEMPLATES["servicios"]
DATA = ("cantidad", "fecha", "distrito o dirección")


def _options(**extra: Any) -> EffectiveOptions:
    return replace(SERVICES.resolve(), cotizar=True, datos_cotizacion=DATA, **extra)


def _specs(options: EffectiveOptions, stage: Stage = Stage.IDENTIFICAR) -> dict[str, Any]:
    registry = registry_for_stage(SERVICES, stage, Facts(), options)
    return {spec.name: spec for spec in registry.specs()}


class TestWhatCountsAsAnAnswer:
    """A date and a quantity have to look like one; «para negocio» is not a date."""

    @pytest.mark.parametrize(
        "said",
        ["el lunes", "para mañana", "15 de enero", "esta semana", "lo antes posible", "fin de mes"],
    )
    def test_these_are_dates(self, said: str) -> None:
        assert _DATE.search(plain(said))

    @pytest.mark.parametrize("said", ["para negocio", "semanales", "para mi tienda", "sí, gracias"])
    def test_these_are_not_dates(self, said: str) -> None:
        assert not _DATE.search(plain(said))

    @pytest.mark.parametrize("said", ["20", "veinte", "una docena", "5 bidones", "varios"])
    def test_these_are_quantities(self, said: str) -> None:
        assert _QUANTITY.search(plain(said))

    @pytest.mark.parametrize("said", ["para negocio", "el lunes", "no sé", "en mi casa"])
    def test_these_are_not_quantities(self, said: str) -> None:
        assert not _QUANTITY.search(plain(said))


class TestWhatTheModelMayNote:
    REQUIRED = ["cantidad", "fecha", "distrito o dirección"]

    def _note(
        self, items: list[dict[str, str]], *, asked: str | None = None, refused: bool = False
    ) -> tuple[dict[str, str], list[str], list[str]]:
        return _given(items, self.REQUIRED, asked, refused_by_customer=refused)

    def test_the_business_s_spelling_wins_over_the_model_s(self) -> None:
        data, _, _ = self._note([{"dato": "Distrito o Dirección", "valor": "Miraflores"}])

        assert data == {"distrito o dirección": "Miraflores"}

    def test_a_date_that_is_not_one_is_left_out_and_reported(self) -> None:
        data, refused, unclear = self._note([{"dato": "fecha", "valor": "para negocio"}])

        assert (data, refused, unclear) == ({}, [], ["fecha"])

    def test_a_quantity_that_is_not_one_is_left_out_and_reported(self) -> None:
        data, _, unclear = self._note([{"dato": "cantidad", "valor": "para mi tienda"}])

        assert (data, unclear) == ({}, ["cantidad"])

    def test_a_datum_of_the_business_without_a_rule_is_taken_as_said(self) -> None:
        data, _, unclear = self._note([{"dato": "distrito o dirección", "valor": "Av. Larco 120"}])

        assert data == {"distrito o dirección": "Av. Larco 120"} and unclear == []

    def test_sin_dato_is_refused_for_something_the_product_does_not_ask(self) -> None:
        data, refused, _ = self._note(
            [{"dato": "color", "valor": NO_DATA}], asked=None, refused=True
        )

        assert (data, refused) == ({}, ["color"])

    def test_sin_dato_closes_any_missing_datum_the_customer_refused(self) -> None:
        # Not only the one asked last: a person is told «no sé la fecha» whatever was asked.
        data, refused, _ = self._note(
            [{"dato": "fecha", "valor": NO_DATA}], asked="cantidad", refused=True
        )

        assert (data, refused) == ({"fecha": NO_DATA}, [])

    def test_only_one_sin_dato_per_call_so_a_quote_is_not_closed_early(self) -> None:
        data, refused, _ = self._note(
            [{"dato": "fecha", "valor": NO_DATA}, {"dato": "cantidad", "valor": NO_DATA}],
            asked="fecha",
            refused=True,
        )

        assert data == {"fecha": NO_DATA} and refused == ["cantidad"]

    def test_sin_dato_is_refused_when_the_customer_did_not_refuse(self) -> None:
        data, refused, _ = self._note(
            [{"dato": "fecha", "valor": NO_DATA}], asked="fecha", refused=False
        )

        assert (data, refused) == ({}, ["fecha"])

    def test_sin_dato_closes_the_datum_just_asked_when_the_customer_refused(self) -> None:
        data, refused, _ = self._note(
            [{"dato": "FECHA", "valor": "sin dato (no sabe)"}], asked="fecha", refused=True
        )

        assert (data, refused) == ({"fecha": "sin dato (no sabe)"}, [])

    def test_empty_names_and_values_and_odd_items_are_dropped(self) -> None:
        data, refused, unclear = _given(
            [{"dato": "", "valor": "x"}, {"dato": "fecha", "valor": ""}, "texto", None],  # type: ignore[list-item]
            self.REQUIRED,
            None,
            refused_by_customer=False,
        )

        assert (data, refused, unclear) == ({}, [], [])

    def test_long_names_and_values_are_cut(self) -> None:
        data, _, _ = self._note([{"dato": "x" * 90, "valor": "y" * 400}])

        ((name, value),) = data.items()
        assert len(name) == 40 and len(value) == 120


class TestADatumIsWhatTheCustomerSaid:
    HEARD = "quiero el bidon de 20 l, unos 12 para empezar. en tienda, quiero vender. para el lunes"

    @pytest.mark.parametrize(
        "value", ["tienda", "en tienda, para vender", "Tienda y reventa", "lunes", "12 unidades"]
    )
    def test_what_echoes_the_customer_is_grounded(self, value: str) -> None:
        assert _grounded(value, self.HEARD)

    @pytest.mark.parametrize("value", ["recurrente", "mensual", "oficina", "San Isidro", "99"])
    def test_what_the_customer_never_said_is_not(self, value: str) -> None:
        assert not _grounded(value, self.HEARD)

    def test_a_value_with_nothing_to_check_passes(self) -> None:
        assert _grounded("no", self.HEARD)

    def test_a_free_datum_nobody_said_is_left_out_and_reported(self) -> None:
        data, _, unclear = _given(
            [{"dato": "frecuencia", "valor": "recurrente"}, {"dato": "uso", "valor": "tienda"}],
            ["uso", "frecuencia"],
            None,
            refused_by_customer=False,
            heard=self.HEARD,
        )

        assert data == {"uso": "tienda"} and unclear == ["frecuencia"]

    def test_the_datum_just_asked_is_taken_in_the_models_words(self) -> None:
        # «compra puntual, una sola vez» answered «¿puntual o recurrente?»; the model wrote «única».
        data, _, unclear = _given(
            [{"dato": "frecuencia", "valor": "única"}],
            ["frecuencia"],
            "frecuencia",
            refused_by_customer=False,
            heard="compra puntual, una sola vez",
        )

        assert data == {"frecuencia": "única"} and unclear == []

    @pytest.mark.parametrize("heard", ["solo esa vez", "por esta vez nomás", "es ocasional"])
    def test_a_one_off_said_another_way_is_a_frequency(self, heard: str) -> None:
        # «solo esa vez» answered «¿puntual o recurrente?» and was not noted: the quote never closed
        data, _, unclear = _given(
            [{"dato": "frecuencia", "valor": "única"}],
            ["frecuencia"],
            "frecuencia",
            refused_by_customer=False,
            heard=heard,
        )

        assert data == {"frecuencia": "única"} and unclear == []

    def test_two_asked_and_one_answered_does_not_let_the_other_be_invented(self) -> None:
        # «¿Para qué uso? ¿Puntual o recurrente?» – «para mi restaurante»: gpt-4.1-mini noted
        # «frecuencia: recurrente» too and the quote went to a person with a datum nobody gave.
        data, _, unclear = _given(
            [
                {"dato": "uso", "valor": "restaurante"},
                {"dato": "frecuencia", "valor": "recurrente"},
            ],
            ["uso", "frecuencia"],
            ["uso", "frecuencia"],
            refused_by_customer=False,
            heard="para mi restaurante",
        )

        assert data == {"uso": "restaurante"} and unclear == ["frecuencia"]

    @pytest.mark.parametrize(
        ("heard", "kept"),
        [
            ("compra puntual, una sola vez", True),
            ("serian pedidos todas las semanas", True),
            ("necesitamos unos 40 al mes", True),
            ("para mi gimnasio", False),
        ],
    )
    def test_how_often_needs_the_customer_to_have_said_it(self, heard: str, kept: bool) -> None:
        data, _, _ = _given(
            [{"dato": "frecuencia", "valor": "recurrente" if "semanas" in heard else "única"}],
            ["frecuencia"],
            "frecuencia",
            refused_by_customer=False,
            heard=heard,
        )

        assert ("frecuencia" in data) is kept

    def test_the_date_and_the_quantity_keep_their_own_checks(self) -> None:
        data, _, unclear = _given(
            [{"dato": "fecha", "valor": "2026-10-27"}, {"dato": "cantidad", "valor": "doce"}],
            ["fecha", "cantidad"],
            None,
            refused_by_customer=False,
            heard=self.HEARD,
        )

        assert data == {"fecha": "2026-10-27", "cantidad": "doce"} and unclear == []

    def test_sin_dato_is_not_grounded_in_words(self) -> None:
        data, _, _ = _given(
            [{"dato": "frecuencia", "valor": "sin dato"}],
            ["frecuencia"],
            "frecuencia",
            refused_by_customer=True,
            heard=self.HEARD,
        )

        assert data == {"frecuencia": "sin dato"}


class TestWhatIsSaidAboutTheUseIsNotAnAddress:
    @pytest.mark.parametrize(
        "said", ["mi hogar", "para mi hogar", "hogar", "el negocio", "mi tienda", "casa", "oficina"]
    )
    def test_these_say_what_it_is_for(self, said: str) -> None:
        assert _is_a_use(said)

    @pytest.mark.parametrize(
        "said", ["Chorrillos", "Av. Larco 123", "mi casa en Surco", "", "para el lunes"]
    )
    def test_these_do_not(self, said: str) -> None:
        assert not _is_a_use(said)

    def test_a_use_given_for_the_address_is_filed_as_the_use(self) -> None:
        data, _, unclear = _given(
            [{"dato": "distrito o dirección", "valor": "mi hogar"}],
            ["cantidad", "distrito o dirección", "uso"],
            "distrito o dirección",
            refused_by_customer=False,
        )

        assert data == {"uso": "mi hogar"} and unclear == []

    def test_without_a_use_to_ask_it_is_not_an_address_either(self) -> None:
        data, _, unclear = _given(
            [{"dato": "distrito o dirección", "valor": "mi hogar"}],
            ["cantidad", "distrito o dirección"],
            None,
            refused_by_customer=False,
        )

        assert data == {} and unclear == ["distrito o dirección"]

    def test_a_real_address_stays_where_it_was_said(self) -> None:
        data, _, _ = _given(
            [{"dato": "distrito o dirección", "valor": "Chorrillos"}],
            ["distrito o dirección", "uso"],
            None,
            refused_by_customer=False,
        )

        assert data == {"distrito o dirección": "Chorrillos"}


class TestTheProductIsAProduct:
    PRODUCTS = ["Bidón de agua de mesa 20 L", "Agua de mesa 500 ml", "Agua de mesa 7 L"]

    @pytest.mark.parametrize(
        "said",
        [
            "Bidón de agua de mesa 20 L",
            "bidones",
            "agua por mayor",
            "botellas de agua",
            "todas las presentaciones",
            "ambos",
            "los tres",
        ],
    )
    def test_these_say_the_product_or_all_of_them(self, said: str) -> None:
        assert _names_a_product(said, self.PRODUCTS)

    @pytest.mark.parametrize("said", ["para negocio", "10 de cada una", "20 semanales", "el lunes"])
    def test_these_are_something_else(self, said: str) -> None:
        assert not _names_a_product(said, self.PRODUCTS)

    def test_a_use_noted_as_the_product_is_left_out_and_reported(self) -> None:
        data, _, unclear = _given(
            [{"dato": "producto", "valor": "para negocio"}, {"dato": "cantidad", "valor": "20"}],
            ["cantidad", "fecha"],
            None,
            refused_by_customer=False,
            products=self.PRODUCTS,
        )

        assert data == {"cantidad": "20"} and unclear == ["producto"]

    def test_without_products_to_compare_with_the_product_is_taken_as_said(self) -> None:
        data, _, unclear = _given(
            [{"dato": "producto", "valor": "lo que sea"}],
            ["cantidad"],
            None,
            refused_by_customer=False,
        )

        assert data == {"producto": "lo que sea"} and unclear == []


class TestTheFormFields:
    @pytest.mark.parametrize(
        ("name", "key"),
        [
            ("cantidad", "cantidad"),
            ("distrito o dirección", "distrito_o_direccion"),
            ("  Para Cuándo  ", "para_cuando"),
            ("¿¿??", "dato"),
        ],
    )
    def test_a_datum_becomes_a_field_name(self, name: str, key: str) -> None:
        assert quote_key(name) == key

    def test_a_field_per_datum_and_the_product(self) -> None:
        spec = _specs(_options())["anotar_cotizacion"]

        assert set(spec.parameters["properties"]) == {
            "cantidad",
            "fecha",
            "distrito_o_direccion",
            "producto",
        }
        assert "required" not in spec.parameters or not spec.parameters["required"]

    def test_the_labels_that_are_not_obvious_say_what_goes_there(self) -> None:
        options = replace(SERVICES.resolve(), cotizar=True, datos_cotizacion=("uso", "fecha"))

        properties = _specs(options)["anotar_cotizacion"].parameters["properties"]

        assert properties["uso"]["description"] and "description" not in properties["fecha"]

    def test_without_data_names_it_is_a_list(self) -> None:
        options = replace(SERVICES.resolve(), cotizar=True, datos_cotizacion=())

        spec = _specs(options)["anotar_cotizacion"]

        assert set(spec.parameters["properties"]) == {"datos"}
        assert spec.parameters["required"] == ["datos"]

    def test_the_handover_does_not_repeat_the_list_when_the_form_exists(self) -> None:
        with_form = _specs(_options())["derivar_a_humano"].parameters["properties"]
        without = _specs(SERVICES.resolve())["derivar_a_humano"].parameters["properties"]

        assert "datos" not in with_form
        assert "datos" in without


class TestTheToolIsOfferedOnlyToABusinessThatQuotes:
    def test_the_capability_turns_it_on(self) -> None:
        on = tools_of_stage(
            ToolScope(template=SERVICES, stage=Stage.IDENTIFICAR, facts=Facts(), options=_options())
        )
        off = tools_of_stage(
            ToolScope(
                template=SERVICES,
                stage=Stage.IDENTIFICAR,
                facts=Facts(),
                options=SERVICES.resolve(),
            )
        )

        assert set(QUOTE_TOOL_NAMES) <= set(on)
        assert not set(QUOTE_TOOL_NAMES) & set(off)

    def test_it_is_in_the_catalogue_with_a_short_description(self) -> None:
        definition = default_catalog().get("anotar_cotizacion")

        assert len(definition.description) < 120


class TestTheQuoteBlockOfThePrompt:
    def _text(self, stage: Stage, **options: Any) -> str:
        config = make_config("servicios")
        config = replace(config, options=replace(config.options, **options))
        return build_stage_prompt(config, Facts(), stage, now=NOW).text

    def test_a_business_that_quotes_gets_the_rules_where_it_sells(self) -> None:
        text = self._text(Stage.IDENTIFICAR, cotizar=True, datos_cotizacion=DATA)

        assert QUOTE_RULES.splitlines()[0] in text

    def test_a_business_that_does_not_quote_never_sees_them(self) -> None:
        assert QUOTE_RULES.splitlines()[0] not in self._text(Stage.IDENTIFICAR)

    def test_they_are_left_out_where_the_assistant_is_not_selling(self) -> None:
        text = self._text(Stage.DERIVADO, cotizar=True, datos_cotizacion=DATA)

        assert QUOTE_RULES.splitlines()[0] not in text

    def test_a_business_that_also_qualifies_leads_is_told_that_a_quote_is_high_interest(
        self,
    ) -> None:
        both = self._text(Stage.IDENTIFICAR, cotizar=True, datos_cotizacion=DATA, captar=True)
        only_quotes = self._text(Stage.IDENTIFICAR, cotizar=True, datos_cotizacion=DATA)

        assert QUOTE_QUALIFIES.strip() in both
        assert QUOTE_QUALIFIES.strip() not in only_quotes

    def test_the_block_fits_its_share_with_and_without_the_extra_line(self) -> None:
        assert estimate_tokens(QUOTE_RULES) <= SECTION_CAPS["cotizacion"]
        assert estimate_tokens(QUOTE_RULES + QUOTE_QUALIFIES) <= SECTION_CAPS["cotizacion"]

    def test_the_state_shows_what_was_given_and_what_is_missing(self) -> None:
        quote = Cotizacion(
            producto="Bidón 20 L",
            datos={"cantidad": "20 semanales"},
            pide=list(DATA),
            preguntado="fecha",
        )
        config = make_config("servicios")
        config = replace(
            config, options=replace(config.options, cotizar=True, datos_cotizacion=DATA)
        )

        text = build_stage_prompt(config, Facts(cotizacion=quote), Stage.IDENTIFICAR, now=NOW).text

        assert "Cotización en curso (Bidón 20 L)" in text
        assert "Ya dio: cantidad = 20 semanales" in text
        assert "Falta: fecha, distrito o dirección" in text

    def test_a_product_that_asks_for_nothing_says_so(self) -> None:
        line = _quote_line(Cotizacion(producto="Visita técnica"), _StateLimits(text=60))

        assert "este producto no pide datos" in line

    def test_a_complete_quote_says_nothing_is_missing(self) -> None:
        quote = Cotizacion(datos={"fecha": "lunes"}, pide=["fecha"])

        assert "Falta: nada" in _quote_line(quote, _StateLimits(text=60))
        assert "producto sin elegir" in _quote_line(quote, _StateLimits(text=60))


class TestTheQuoteFact:
    def test_what_is_missing_is_what_the_product_asks_and_was_not_given(self) -> None:
        quote = Cotizacion(
            datos={"Cantidad": "5", "producto": "Bidón"}, pide=["cantidad", "Fecha", "Distrito"]
        )

        assert quote.falta == ["Fecha", "Distrito"]

    def test_accents_and_case_do_not_make_a_datum_missing(self) -> None:
        quote = Cotizacion(datos={"dirección": "Av. Lima 1"}, pide=["Direccion"])

        assert quote.falta == []

    def test_it_is_stored_and_read_back(self) -> None:
        quote = Cotizacion(
            producto="Bidón",
            producto_id="abc",
            datos={"cantidad": "5"},
            pide=["cantidad", "fecha"],
            preguntado="fecha",
        )

        stored = Facts(cotizacion=quote).to_stored()
        again = Facts.from_stored(stored)

        assert again.cotizacion == quote
        assert again.cotizacion is not None and again.cotizacion.falta == ["fecha"]

    def test_no_quote_stores_nothing(self) -> None:
        assert "cotizacion" not in Facts().to_stored()


class TestTheRecap:
    def test_the_customer_reads_back_what_was_passed_on(self) -> None:
        text = recap_line({"cantidad": "5", "fecha": "lunes"}, "Bidón 20 L")

        assert text == "Anoté: Bidón 20 L; cantidad: 5; fecha: lunes."

    def test_the_product_is_not_said_twice(self) -> None:
        text = recap_line({"producto": "Bidón 20 L", "cantidad": "5"}, "Bidón 20 L")

        assert text == "Anoté: Bidón 20 L; cantidad: 5."

    def test_a_datum_the_customer_would_not_give_reads_as_to_be_defined(self) -> None:
        text = recap_line({"fecha": "sin dato (no sabe)"})

        assert text == "Anoté: fecha: por definir."

    def test_nothing_noted_says_nothing(self) -> None:
        assert recap_line({}) == ""


class TestAClaimWithoutTheTool:
    OFFERED = ("buscar_productos", "anotar_cotizacion")

    @pytest.mark.parametrize(
        "content",
        ["Listo, ya anoté tu pedido.", "Quedó anotado para el lunes.", "Anotadas las 20 unidades."],
    )
    def test_saying_it_noted_without_calling_the_tool_is_caught(self, content: str) -> None:
        assert _unbacked_claim(content, [], self.OFFERED) == "anotar_cotizacion"

    def test_calling_the_tool_backs_the_claim(self) -> None:
        assert _unbacked_claim("Ya anoté eso.", ["anotar_cotizacion"], self.OFFERED) is None

    def test_a_business_without_the_tool_is_never_nudged(self) -> None:
        assert _unbacked_claim("Ya anoté eso.", [], ("buscar_productos",)) is None

    @pytest.mark.parametrize(
        "content", ["¿Para qué fecha lo necesitas?", "Una nota: llegamos hoy."]
    )
    def test_ordinary_replies_are_left_alone(self, content: str) -> None:
        assert _unbacked_claim(content, [], self.OFFERED) is None

    def test_the_claims_are_about_tools_that_exist(self) -> None:
        assert set(UNBACKED_CLAIMS) <= set(default_catalog().names())


def _context(quote: Cotizacion | None) -> ToolContext:
    from typing import cast
    from uuid import uuid4

    from sqlalchemy.orm import Session

    from app.providers.llm import MockLLMProvider

    return ToolContext(
        session=cast(Session, None),
        tenant_id=uuid4(),
        llm=MockLLMProvider(),
        facts=Facts(cotizacion=quote),
    )


class TestAnAnswerThatWasNotNoted:
    ASKING_DATE = Cotizacion(
        datos={"cantidad": "5"}, pide=["cantidad", "fecha"], preguntado="fecha"
    )

    @pytest.mark.parametrize(
        "said", ["desde el lunes", "para mañana", "el 26 de octubre", "todavía no sé cuándo"]
    )
    def test_an_answer_to_the_date_just_asked_is_recognised(self, said: str) -> None:
        assert unnoted_answer(_context(self.ASKING_DATE), said) == "fecha"

    @pytest.mark.parametrize("said", ["gracias", "ok", "¿cuánto cuesta?", "dale, listo"])
    def test_an_acknowledgement_or_a_question_is_not_taken_for_an_answer(self, said: str) -> None:
        assert unnoted_answer(_context(self.ASKING_DATE), said) is None

    def test_anything_else_may_be_an_answer_the_model_did_not_note(self) -> None:
        # «para mi hogar» to a question about the address: understood, answered, never noted.
        asking = Cotizacion(pide=["distrito", "uso"], preguntado="distrito")

        assert unnoted_answer(_context(asking), "para mi hogar") == "distrito, uso"

    def test_a_datum_that_is_not_recognised_without_a_model_still_reports_what_is_missing(
        self,
    ) -> None:
        asking = Cotizacion(pide=["distrito"], preguntado="distrito")

        assert unnoted_answer(_context(asking), "en Miraflores") == "distrito"

    def test_nothing_is_reported_when_nothing_is_missing(self) -> None:
        done = Cotizacion(datos={"fecha": "lunes"}, pide=["fecha"], preguntado=None)

        assert unnoted_answer(_context(done), "el lunes") is None

    def test_without_a_quote_in_progress_there_is_nothing_to_note(self) -> None:
        assert unnoted_answer(_context(None), "desde el lunes") is None
        assert unnoted_answer(_context(Cotizacion(pide=["fecha"])), "desde el lunes") is None

    def test_the_model_is_told_which_datum_when_it_skipped_the_tool(self) -> None:
        note = _missed_note(
            "Perfecto, ¿en qué distrito?",
            [],
            ("anotar_cotizacion",),
            _context(self.ASKING_DATE),
            "desde el lunes",
        )

        assert note == UNNOTED_NOTE.format(dato="fecha")

    def test_a_call_to_the_tool_means_nothing_was_missed(self) -> None:
        note = _missed_note(
            "Anotado. ¿Y en qué distrito?",
            ["anotar_cotizacion"],
            ("anotar_cotizacion",),
            _context(self.ASKING_DATE),
            "desde el lunes",
        )

        assert note is None

    def test_a_business_without_the_tool_is_never_told(self) -> None:
        note = _missed_note(
            "Ok", [], ("buscar_productos",), _context(self.ASKING_DATE), "desde el lunes"
        )

        assert note is None


class TestAReasonNobodyGave:
    OFFERED = ("buscar_productos",)

    @pytest.mark.parametrize(
        "content",
        [
            "Los formatos más usados para reventa son el bidón y la caja.",
            "El bidón de 20 L es el más pedido por las tiendas.",
            "Es el más vendido de la línea.",
            "Tiene alta rotación en bodegas.",
        ],
    )
    def test_an_invented_reason_to_buy_is_caught(self, content: str) -> None:
        note = _missed_note(content, [], self.OFFERED, _context(None), "hola")

        assert note == PITCH_NOTE

    @pytest.mark.parametrize(
        "content",
        [
            "Tenemos bidón de 20 L, caja de 20 L y botellas. ¿Cuál quieres cotizar?",
            "Necesito algo más: ¿para qué fecha lo necesitas?",
            "El precio lo confirma una persona del negocio.",
        ],
    )
    def test_ordinary_replies_are_left_alone(self, content: str) -> None:
        assert _missed_note(content, [], self.OFFERED, _context(None), "hola") is None


class TestAReplyThatAsksNothing:
    OFFERED = ("anotar_cotizacion",)
    OPEN = Cotizacion(datos={"cantidad": "5"}, pide=["cantidad", "fecha"], preguntado="fecha")

    def test_a_quote_that_misses_data_and_a_reply_without_a_question_is_caught(self) -> None:
        note = _missed_note(
            "Perfecto, para el lunes ya queda claro.", [], self.OFFERED, _context(self.OPEN), "ok"
        )

        assert note == NEXT_STEP_NOTE

    def test_a_reply_that_asks_is_left_alone(self) -> None:
        note = _missed_note(
            "Perfecto. ¿A qué distrito te lo llevamos?", [], self.OFFERED, _context(self.OPEN), "ok"
        )

        assert note is None

    def test_a_complete_quote_needs_no_question(self) -> None:
        done = Cotizacion(datos={"cantidad": "5"}, pide=["cantidad"])

        assert _missed_note("Listo.", [], self.OFFERED, _context(done), "x") is None

    def test_a_business_with_data_to_ask_and_nothing_noted_yet_is_nudged_too(self) -> None:
        context = _context(None)
        context = replace(context, options=_options())

        note = _missed_note("Hola, soy Nayu 😊", [], self.OFFERED, context, "precio del bidón?")

        assert note == NEXT_STEP_NOTE

    def test_a_business_that_asks_no_data_is_never_nudged_for_it(self) -> None:
        assert _missed_note("Hola 😊", [], self.OFFERED, _context(None), "hola") is None

    def test_a_business_without_the_tool_is_never_nudged_for_it(self) -> None:
        context = replace(_context(None), options=_options())

        assert _missed_note("Hola 😊", [], ("buscar_productos",), context, "hola") is None


class TestWhatGpt41MiniSkipped:
    """The nudges for what gpt-4.1-mini skipped in the sales flows (2026-10-27)."""

    SELLING = ("buscar_productos", "gestionar_pedido")

    @pytest.mark.parametrize(
        "said", ["Sí, agrégala", "Quiero el paquete de 5 certificaciones", "me lo llevo"]
    )
    def test_wanting_it_without_adding_it_gets_the_add_note(self, said: str) -> None:
        note = _missed_note("¿Me das tu DNI?", [], self.SELLING, _context(None), said)

        assert note == ADD_NOTE

    def test_adding_it_or_quoting_it_is_enough(self) -> None:
        for called in (["gestionar_pedido"], ["anotar_cotizacion"], ["derivar_a_humano"]):
            note = _missed_note(
                "Listo. ¿Algo más?", called, self.SELLING, _context(None), "lo quiero"
            )
            assert note != ADD_NOTE

    @pytest.mark.parametrize("said", ["quiero el precio del bidón", "quiero saber más"])
    def test_asking_is_not_wanting_it(self, said: str) -> None:
        assert (
            _missed_note("Cuesta S/ 10. ¿Lo agrego?", [], self.SELLING, _context(None), said)
            is None
        )

    def test_how_to_pay_without_the_methods_tool_gets_the_pay_note(self) -> None:
        offered = ("enviar_metodos_pago", "gestionar_pedido")

        note = _missed_note("Puedes pagar por Yape.", [], offered, _context(None), "¿Cómo pago?")

        assert note == PAY_NOTE

    def test_wanting_it_with_an_order_already_made_is_left_to_the_model(self) -> None:
        """«Lo quiero para mañana» in the data stage is about the order made, not a new line."""
        context = _context(None)
        ordered = replace(
            context,
            facts=Facts(pedido=Pedido(items=[PedidoItem(nombre="Polo básico")])),
        )

        note = _missed_note(
            "¿A qué dirección te lo enviamos?", [], self.SELLING, ordered, "lo quiero para mañana"
        )

        assert note is None

    def test_how_to_pay_after_the_methods_went_is_left_to_the_model(self) -> None:
        """The charge stage tells the customer to look at the message already sent."""
        offered = ("enviar_metodos_pago", "gestionar_pedido")
        sent = replace(_context(None), facts=Facts(metodos_pago_enviados=True))

        assert _missed_note("Revisa el mensaje anterior.", [], offered, sent, "¿Cómo pago?") is None

    def test_how_to_pay_where_the_tool_is_not_offered_is_left_to_the_model(self) -> None:
        assert (
            _missed_note("Con Yape o Plin.", [], self.SELLING, _context(None), "¿cómo pago?")
            is None
        )

    @pytest.mark.parametrize(
        "content",
        ["¿Quieres que te cuente más detalles?", "¿Deseas que te envíe los datos para el pago?"],
    )
    def test_asking_permission_gets_the_permission_note(self, content: str) -> None:
        assert _missed_note(content, [], self.SELLING, _context(None), "hola") == PERMISSION_NOTE

    @pytest.mark.parametrize("said", ["sí, los dos", "dale", "ok, ese", "Sí por favor"])
    def test_yes_to_an_offer_to_add_is_wanting_it(self, said: str) -> None:
        offer = "Tenemos el hosting anual y el mantenimiento. ¿Te los agrego al pedido?"

        assert accepted_an_offer_to_add(offer, said)
        note = _missed_note(
            "¿Quieres que lo haga ahora?", [], self.SELLING, _context(None), said, accepted=True
        )
        assert note == ADD_NOTE

    def test_yes_to_another_question_is_not_wanting_it(self) -> None:
        assert not accepted_an_offer_to_add("¿Es para tu hogar o tu negocio?", "sí, negocio")
        assert not accepted_an_offer_to_add("¿Te lo agrego?", "no, todavía no")

    def test_yes_to_an_offer_adds_even_with_an_order(self) -> None:
        """The offer named what to add: «¿te agrego también la gorra?» – «sí»."""
        ordered = replace(
            _context(None), facts=Facts(pedido=Pedido(items=[PedidoItem(nombre="Polo básico")]))
        )

        assert _missed_note("Perfecto.", [], self.SELLING, ordered, "sí", accepted=True) == ADD_NOTE

    @pytest.mark.parametrize(
        "content",
        [
            "Te envío los datos para el pago por Yape y así puedas avanzar.",
            "Voy a buscar las opciones de automatización y te las muestro enseguida.",
            "En un momento te confirmo el precio.",
        ],
    )
    def test_promising_what_it_did_not_do_gets_the_promise_note(self, content: str) -> None:
        assert _missed_note(content, [], self.SELLING, _context(None), "ok") == PROMISE_NOTE

    def test_saying_what_a_tool_just_sent_is_not_a_promise(self) -> None:
        content = "Te envío los datos para el pago. Cuando pagues, mándame la foto del comprobante."
        called = ["enviar_metodos_pago"]

        assert _missed_note(content, called, self.SELLING, _context(None), "¿cómo pago?") is None

    def test_a_person_writing_soon_is_not_a_promise_of_its_own(self) -> None:
        content = (
            "Listo, ya le pasé a nuestro equipo lo que necesitas. Una persona te escribe en breve."
        )

        assert (
            _missed_note(content, ["derivar_a_humano"], self.SELLING, _context(None), "x") is None
        )

    def test_asking_three_quote_data_at_once_gets_the_too_many_note(self) -> None:
        quoting = ("buscar_productos", "anotar_cotizacion")
        content = (
            "Tenemos bidones de 20 L. ¿Cuántos bidones necesitas, para cuándo y en qué distrito "
            "sería la entrega?"
        )

        quote = Cotizacion(datos={}, pide=["cantidad", "fecha", "distrito"])

        assert _missed_note(content, [], quoting, _context(quote), "bidones") == TOO_MANY_NOTE

    def test_two_that_go_together_are_fine(self) -> None:
        quoting = ("buscar_productos", "anotar_cotizacion")
        content = "Perfecto, 8 bidones. ¿Para qué fecha y en qué distrito sería la entrega?"
        quote = Cotizacion(datos={"cantidad": "8"}, pide=["cantidad", "fecha", "distrito"])

        assert _missed_note(content, ["anotar_cotizacion"], quoting, _context(quote), "8") is None

    @pytest.mark.parametrize(
        "said", ["me llevo un polo básico, ¿cómo te pago?", "quiero contratar el mantenimiento"]
    )
    def test_other_ways_of_wanting_it(self, said: str) -> None:
        note = _missed_note("¿Te lo agrego?", [], self.SELLING, _context(None), said)

        assert note == ADD_NOTE

    def test_saying_it_was_added_without_the_tool_gets_the_claim_note(self) -> None:
        content = "Perfecto, te agrego el mantenimiento web mensual por S/ 150.00 a tu pedido."

        note = _missed_note(content, [], self.SELLING, _context(None), "lo necesito")

        assert note == ADDED_CLAIM_NOTE

    def test_saying_it_is_registered_without_the_tool_is_a_claim(self) -> None:
        content = "Listo, Luz: ya quedó registrada la *Certificación individual SIAF*."

        note = _missed_note(content, [], self.SELLING, _context(None), "quiero la de SIAF")

        assert note == ADDED_CLAIM_NOTE

    def test_a_return_recorded_is_not_an_order_claim(self) -> None:
        content = "Listo, tu solicitud de cambio quedó registrada. Una persona la revisa."

        note = _missed_note(
            content, ["solicitar_devolucion"], self.SELLING, _context(None), "quiero cambiarlo"
        )

        assert note is None

    def test_offering_to_add_is_not_a_claim(self) -> None:
        content = "Cuesta S/ 35. ¿Te agrego el polo al pedido?"

        assert _missed_note(content, [], self.SELLING, _context(None), "hola") is None

    def test_a_failed_order_call_does_not_excuse_a_promise(self) -> None:
        content = (
            "Lucía, ya tengo tus datos. Voy a agregar el mantenimiento a tu pedido. Un momento."
        )

        note = _missed_note(
            content, ["gestionar_pedido"], self.SELLING, _context(None), "Soy Lucía", done=[]
        )

        assert note == PROMISE_NOTE

    @pytest.mark.parametrize(
        "content",
        [
            "Voy a derivarte con una persona para que te lo confirme. Un momento, por favor.",
            "Te envío la información sobre el proceso para certificar SIAF.",
        ],
    )
    def test_other_promises(self, content: str) -> None:
        assert _missed_note(content, [], self.SELLING, _context(None), "hola") == PROMISE_NOTE

    def test_offering_to_add_it_is_the_closing_question_not_asking_permission(self) -> None:
        """After a recommendation «¿te lo agrego?» waits for the customer to say yes."""
        content = "El de 5 te conviene. ¿Quieres que te agregue el paquete de 5?"

        assert _missed_note(content, [], self.SELLING, _context(None), "¿cuál me conviene?") is None

    def test_a_question_about_the_customer_s_choice_is_not_asking_permission(self) -> None:
        content = "¿Prefieres que te lo envíe a domicilio o lo recoges?"

        assert _missed_note(content, [], self.SELLING, _context(None), "hola") is None
