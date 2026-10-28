"""The three process templates (Feature 004, TASK-409).

A template is data a reviewer reads in a PR, so these tests pin it against
`plantillas.md`: every stage has its text and funnel column, every tool a stage
names exists, every placeholder in a text is one the prompt builder fills in, and
the tools per stage match the table in section 2 cell by cell.
"""

from __future__ import annotations

import re

import pytest
from pydantic import ValidationError

from app.models.conversation_state import FunnelCode
from app.process.templates import TEMPLATES, get_template
from app.process.templates.base import (
    DEFAULT_SYSTEM_MESSAGES,
    GLOBAL_TOOLS,
    KNOWN_PLACEHOLDERS,
    SYSTEM_MESSAGE_PLACEHOLDERS,
    TOOL_NAMES,
    Stage,
    Template,
    is_known_placeholder,
    placeholders_in,
)
from app.schemas.process_options import Calificacion, Camino, ProcessOptions

# `tool_name(` as the default texts write a call.
CALL = re.compile(r"(?<![a-z_])([a-z_]+)\(")

# The three templates of feature 004 and the universal process of feature 020, which replaces them.
TEMPLATE_KEYS = ["productos", "certificaciones", "servicios", "universal"]
ALL_TEMPLATES = [TEMPLATES[key] for key in TEMPLATE_KEYS]

SEARCH = ("buscar_productos", "ver_producto", "mostrar_opciones")
SEARCH_AND_ORDER = (*SEARCH, "gestionar_pedido")

# plantillas.md section 2, the columns that are identical in every template.
COMMON_TOOLS = {
    Stage.CALIFICAR: (*SEARCH_AND_ORDER, "elegir_camino"),
    Stage.ANUNCIO: (*SEARCH_AND_ORDER, "elegir_camino"),
    Stage.IDENTIFICAR: (*SEARCH_AND_ORDER, "elegir_camino"),
    Stage.PEDIDO: ("gestionar_pedido", "buscar_productos", "ver_producto", "mostrar_opciones"),
    Stage.DATOS: ("gestionar_pedido", "buscar_productos"),
    Stage.COBRO: ("enviar_metodos_pago", "gestionar_pedido", "buscar_productos"),
    Stage.VERIFICACION: ("gestionar_pedido",),
    Stage.CERRADO: ("buscar_productos", "ver_producto", "gestionar_pedido"),
    Stage.ASESORIA: (*SEARCH, "elegir_camino"),
    Stage.DERIVADO: (),
}
INICIO_TOOLS = {
    "productos": SEARCH_AND_ORDER,
    "certificaciones": (),
    "servicios": SEARCH,
    "universal": SEARCH_AND_ORDER,
}


class TestTheToolVocabulary:
    def test_there_are_ten_distinct_tools(self) -> None:
        assert len(TOOL_NAMES) == 10
        assert len(set(TOOL_NAMES)) == 10

    def test_the_global_tools_are_part_of_it(self) -> None:
        assert set(GLOBAL_TOOLS) <= set(TOOL_NAMES)
        assert set(GLOBAL_TOOLS) == {
            "consultar_conocimiento",
            "enviar_recurso",
            "derivar_a_humano",
            "registrar_datos_cliente",
        }

    def test_the_tools_of_contracts_api_section_7_are_all_named(self) -> None:
        assert set(TOOL_NAMES) == {
            "buscar_productos",
            "ver_producto",
            "consultar_conocimiento",
            "enviar_recurso",
            "mostrar_opciones",
            "gestionar_pedido",
            "registrar_datos_cliente",
            "enviar_metodos_pago",
            "elegir_camino",
            "derivar_a_humano",
        }


class TestRegistry:
    def test_the_three_templates_are_registered_by_key(self) -> None:
        assert sorted(TEMPLATES) == sorted(TEMPLATE_KEYS)
        assert all(TEMPLATES[key].key == key for key in TEMPLATE_KEYS)

    def test_an_unknown_key_says_what_exists(self) -> None:
        with pytest.raises(KeyError, match="certificaciones"):
            get_template("inventada")

    def test_get_template_returns_the_registered_one(self) -> None:
        assert get_template("servicios") is TEMPLATES["servicios"]

    @pytest.mark.parametrize("template", ALL_TEMPLATES, ids=TEMPLATE_KEYS)
    def test_every_template_starts_at_version_one(self, template: Template) -> None:
        assert template.version == 1

    @pytest.mark.parametrize("template", ALL_TEMPLATES, ids=TEMPLATE_KEYS)
    def test_every_template_has_a_name_and_a_description(self, template: Template) -> None:
        assert template.nombre.strip()
        assert template.descripcion.strip()


@pytest.mark.parametrize("template", ALL_TEMPLATES, ids=TEMPLATE_KEYS)
class TestEveryStage:
    def test_all_eleven_stages_are_defined(self, template: Template) -> None:
        assert set(template.stages) == set(Stage)
        assert len(template.stages) == 11

    def test_each_stage_has_instructions_and_a_funnel_column(self, template: Template) -> None:
        for stage, spec in template.stages.items():
            assert spec.stage is stage
            assert spec.instrucciones.strip(), f"{stage} has no text"
            assert isinstance(spec.funnel, FunnelCode), f"{stage} has no funnel column"
            assert spec.funnel is not FunnelCode.PERDIDO, "PERDIDO is reserved for Feature 010"

    def test_the_tools_of_every_stage_exist(self, template: Template) -> None:
        for stage, spec in template.stages.items():
            assert set(spec.tools) <= set(TOOL_NAMES), f"{stage}: unknown tool"
            if spec.tools_if_rejected is not None:
                assert set(spec.tools_if_rejected) <= set(TOOL_NAMES)

    def test_no_stage_lists_a_global_tool_of_its_own(self, template: Template) -> None:
        """The globals are added on top by the registry; repeating one here would
        hide a stage that was meant to be restricted."""
        for spec in template.stages.values():
            assert not set(spec.tools) & set(GLOBAL_TOOLS)

    def test_every_placeholder_is_one_the_prompt_builder_fills(self, template: Template) -> None:
        for stage, spec in template.stages.items():
            for name in placeholders_in(spec.instrucciones):
                assert is_known_placeholder(name), f"{stage}: unknown placeholder {{{name}}}"

    def test_no_brace_is_left_over_once_placeholders_are_removed(self, template: Template) -> None:
        """A stray `{` would reach the model as literal text."""
        for stage, spec in template.stages.items():
            text = spec.instrucciones
            for name in placeholders_in(text):
                text = text.replace("{" + name + "}", "")
            assert "{" not in text and "}" not in text, f"{stage} has a stray brace"

    def test_each_text_calls_only_tools_that_exist(self, template: Template) -> None:
        """An instruction that calls `registrar_pago(...)` would send the model to
        a tool that is not there."""
        for stage, spec in template.stages.items():
            for call in CALL.findall(spec.instrucciones):
                assert call in TOOL_NAMES, f"{template.key}/{stage} calls unknown {call}"

    def test_each_text_only_calls_tools_its_stage_can_use(self, template: Template) -> None:
        """Telling the model to call a tool the stage does not enable is a dead
        instruction: the registry would never offer it."""
        for stage, spec in template.stages.items():
            enabled = {*spec.tools, *(spec.tools_if_rejected or ()), *GLOBAL_TOOLS}
            for call in CALL.findall(spec.instrucciones):
                assert call in enabled, f"{template.key}/{stage} calls {call} but it is off"

    def test_only_cobro_has_a_second_funnel_column(self, template: Template) -> None:
        for stage, spec in template.stages.items():
            if stage is Stage.COBRO:
                assert spec.funnel is FunnelCode.NEGOCIACION
                assert spec.funnel_with_methods_sent is FunnelCode.CUENTAS_ENVIADAS
            else:
                assert spec.funnel_with_methods_sent is None

    def test_only_verificacion_changes_its_tools_when_rejected(self, template: Template) -> None:
        for stage, spec in template.stages.items():
            if stage is Stage.VERIFICACION:
                assert spec.tools_for() == ("gestionar_pedido",)
                assert spec.tools_for(rejected=True) == (
                    "enviar_metodos_pago",
                    "gestionar_pedido",
                    "buscar_productos",
                )
            else:
                assert spec.tools_if_rejected is None
                assert spec.tools_for(rejected=True) == spec.tools

    def test_the_funnel_column_of_each_stage(self, template: Template) -> None:
        funnel = {stage: spec.funnel for stage, spec in template.stages.items()}

        assert funnel == {
            Stage.INICIO: FunnelCode.NUEVO,
            Stage.CALIFICAR: FunnelCode.NUEVO,
            Stage.ANUNCIO: FunnelCode.NUEVO,
            Stage.IDENTIFICAR: FunnelCode.ATENDIDO,
            Stage.PEDIDO: FunnelCode.NEGOCIACION,
            Stage.DATOS: FunnelCode.NEGOCIACION,
            Stage.COBRO: FunnelCode.NEGOCIACION,
            Stage.VERIFICACION: FunnelCode.EN_VERIFICACION,
            Stage.CERRADO: FunnelCode.GANADO,
            Stage.ASESORIA: FunnelCode.DERIVADO,
            Stage.DERIVADO: FunnelCode.DERIVADO,
        }

    def test_the_tools_of_every_stage_match_the_table_in_plantillas_md(
        self, template: Template
    ) -> None:
        for stage, expected in COMMON_TOOLS.items():
            assert template.stage(stage).tools == expected, f"{template.key}/{stage}"
        assert template.stage(Stage.INICIO).tools == INICIO_TOOLS[template.key]

    def test_follow_up_texts_exist_exactly_where_c13_allows_a_follow_up(
        self, template: Template
    ) -> None:
        with_follow_up = {s for s, spec in template.stages.items() if spec.seguimiento}

        assert with_follow_up == {
            Stage.IDENTIFICAR,
            Stage.PEDIDO,
            Stage.DATOS,
            Stage.COBRO,
            Stage.VERIFICACION,
        }
        for stage in with_follow_up:
            follow_up = template.stage(stage).seguimiento
            assert follow_up is not None
            assert follow_up.primer_intento.strip() and follow_up.ultimo_intento.strip()
            for text in (follow_up.primer_intento, follow_up.ultimo_intento):
                assert all(is_known_placeholder(n) for n in placeholders_in(text))

    def test_the_last_attempt_may_mention_the_follow_up_promotion(self, template: Template) -> None:
        for stage in (Stage.IDENTIFICAR, Stage.PEDIDO, Stage.COBRO):
            follow_up = template.stage(stage).seguimiento
            assert follow_up is not None
            assert "promoci" in follow_up.ultimo_intento
            assert "promoci" not in follow_up.primer_intento


class TestWhatDiffersBetweenTemplates:
    def test_identificar_has_its_own_text_in_each_template(self) -> None:
        texts = {t.key: t.stage(Stage.IDENTIFICAR).instrucciones for t in ALL_TEMPLATES}

        assert len(set(texts.values())) == len(ALL_TEMPLATES)
        assert "SIEMPRE SÍ" in texts["certificaciones"]
        assert "cotización" in texts["servicios"]
        assert "para qué lo necesita" in texts["productos"]

    def test_certificaciones_overrides_inicio_pedido_and_asesoria_only(self) -> None:
        base = TEMPLATES["productos"]
        cert = TEMPLATES["certificaciones"]

        differing = {
            stage
            for stage in Stage
            if base.stage(stage).instrucciones != cert.stage(stage).instrucciones
        }

        assert differing == {Stage.INICIO, Stage.IDENTIFICAR, Stage.PEDIDO, Stage.ASESORIA}

    def test_productos_and_servicios_share_every_text_but_identificar(self) -> None:
        productos, servicios = TEMPLATES["productos"], TEMPLATES["servicios"]

        differing = {
            stage
            for stage in Stage
            if productos.stage(stage).instrucciones != servicios.stage(stage).instrucciones
        }

        assert differing == {Stage.IDENTIFICAR}

    def test_certificaciones_only_greets_in_its_first_turn(self) -> None:
        text = TEMPLATES["certificaciones"].stage(Stage.INICIO).instrucciones

        assert "pregúntale su nombre" in text
        assert "la oferta va en el siguiente turno" in text

    def test_the_order_kind_and_line_limit(self) -> None:
        assert TEMPLATES["productos"].tipo_pedido == "carrito"
        assert TEMPLATES["productos"].max_lineas is None
        assert TEMPLATES["certificaciones"].tipo_pedido == "paquete"
        assert TEMPLATES["certificaciones"].max_lineas is None
        assert TEMPLATES["servicios"].tipo_pedido == "carrito"
        assert TEMPLATES["servicios"].max_lineas == 3


class TestDefaultOptions:
    """plantillas.md section 3."""

    def test_productos(self) -> None:
        effective = TEMPLATES["productos"].resolve()

        assert effective.calificacion_activa is False
        assert effective.caminos == []
        assert effective.modalidad_pago == "previo"
        assert effective.datos_requeridos == ("nombres", "telefono")

    def test_certificaciones(self) -> None:
        effective = TEMPLATES["certificaciones"].resolve()

        assert effective.calificacion_activa is True
        assert [(c.clave, c.tipo) for c in effective.caminos] == [
            ("certificacion", "venta"),
            ("programa", "asesoria"),
        ]
        assert effective.calificacion.pregunta
        assert effective.modalidad_pago == "previo"
        assert effective.datos_requeridos == ("dni", "nombres", "apellidos", "correo", "telefono")

    def test_servicios(self) -> None:
        effective = TEMPLATES["servicios"].resolve()

        assert effective.calificacion_activa is False
        assert effective.modalidad_pago == "previo"
        assert effective.datos_requeridos == ("nombres", "empresa", "correo")

    @pytest.mark.parametrize("template", ALL_TEMPLATES, ids=TEMPLATE_KEYS)
    def test_the_defaults_are_valid_for_their_own_template(self, template: Template) -> None:
        parsed = template.validate_options(template.defaults.model_dump(mode="json"))

        assert template.resolve(parsed) == template.resolve()

    def test_the_default_path_titles_fit_a_whatsapp_button(self) -> None:
        for camino in TEMPLATES["certificaciones"].resolve().caminos:
            assert len(camino.titulo) <= 20


class TestResolvingTenantOptions:
    def test_no_options_means_the_defaults(self) -> None:
        template = TEMPLATES["certificaciones"]

        assert template.resolve(None) == template.resolve(ProcessOptions())

    def test_what_the_tenant_sets_wins_and_the_rest_stays_default(self) -> None:
        options = ProcessOptions(datos_requeridos=["nombres", "dni"])

        effective = TEMPLATES["certificaciones"].resolve(options)

        assert effective.datos_requeridos == ("nombres", "dni")
        assert effective.calificacion_activa is True  # untouched default

    def test_an_empty_list_of_required_data_is_respected(self) -> None:
        """ "Ask for nothing" is a choice, not a missing value."""
        effective = TEMPLATES["productos"].resolve(ProcessOptions(datos_requeridos=[]))

        assert effective.datos_requeridos == ()

    def test_a_qualification_section_replaces_the_default_whole(self) -> None:
        options = ProcessOptions(
            calificacion=Calificacion(
                activa=True,
                pregunta="¿Compra o alquiler?",
                caminos=[
                    Camino(clave="compra", titulo="Comprar", tipo="venta"),
                    Camino(clave="alquiler", titulo="Alquilar", tipo="asesoria"),
                ],
            )
        )

        effective = TEMPLATES["certificaciones"].resolve(options)

        assert [c.clave for c in effective.caminos] == ["compra", "alquiler"]
        assert effective.calificacion.pregunta == "¿Compra o alquiler?"

    def test_a_tenant_can_switch_qualification_off(self) -> None:
        options = ProcessOptions(calificacion=Calificacion(activa=False))

        effective = TEMPLATES["certificaciones"].resolve(options)

        assert effective.calificacion_activa is False
        assert effective.caminos == []

    def test_productos_can_take_payment_on_delivery(self) -> None:
        options = TEMPLATES["productos"].validate_options({"modalidad_pago": "contra_entrega"})

        assert TEMPLATES["productos"].resolve(options).modalidad_pago == "contra_entrega"

    def test_the_card_payment_link_and_variables_pass_through(self) -> None:
        options = ProcessOptions(
            link_pago_tarjeta="https://tienda.example.com/pagar",
            variables={"temas_destacados": "inglés o PMBOK"},
        )

        effective = TEMPLATES["productos"].resolve(options)

        assert effective.link_pago_tarjeta == "https://tienda.example.com/pagar"
        assert effective.variables == {"temas_destacados": "inglés o PMBOK"}

    def test_without_a_link_there_is_none(self) -> None:
        assert TEMPLATES["productos"].resolve().link_pago_tarjeta is None


class TestEffectivePaths:
    def test_a_path_is_found_by_key(self) -> None:
        effective = TEMPLATES["certificaciones"].resolve()

        found = effective.camino("programa")

        assert found is not None and found.tipo == "asesoria"
        assert effective.camino("inventado") is None
        assert effective.camino(None) is None

    def test_the_first_sale_path(self) -> None:
        first = TEMPLATES["certificaciones"].resolve().primer_camino_de_venta()

        assert first is not None and first.clave == "certificacion"

    def test_no_sale_path_when_there_are_no_paths(self) -> None:
        assert TEMPLATES["productos"].resolve().primer_camino_de_venta() is None


class TestOptionSchemas:
    @pytest.mark.parametrize("template", ALL_TEMPLATES, ids=TEMPLATE_KEYS)
    def test_every_template_publishes_a_json_schema(self, template: Template) -> None:
        schema = template.options_schema

        assert schema["type"] == "object"
        assert {"calificacion", "modalidad_pago", "datos_requeridos"} <= set(
            schema["properties"]  # type: ignore[call-overload]
        )

    @pytest.mark.parametrize("key", ["certificaciones", "servicios"])
    def test_prepaid_templates_refuse_cash_on_delivery(self, key: str) -> None:
        with pytest.raises(ValidationError):
            TEMPLATES[key].validate_options({"modalidad_pago": "contra_entrega"})

    @pytest.mark.parametrize("key", ["certificaciones", "servicios"])
    def test_prepaid_templates_accept_prepaid(self, key: str) -> None:
        parsed = TEMPLATES[key].validate_options({"modalidad_pago": "previo"})

        assert parsed.modalidad_pago == "previo"

    def test_the_schema_of_a_prepaid_template_offers_one_payment_mode(self) -> None:
        schema = TEMPLATES["servicios"].options_schema
        property_schema = schema["properties"]["modalidad_pago"]  # type: ignore[call-overload]

        assert "contra_entrega" not in str(property_schema)
        assert "previo" in str(property_schema)

    def test_the_schema_of_productos_offers_both(self) -> None:
        schema = TEMPLATES["productos"].options_schema
        property_schema = schema["properties"]["modalidad_pago"]  # type: ignore[call-overload]

        assert "contra_entrega" in str(property_schema)

    @pytest.mark.parametrize("template", ALL_TEMPLATES, ids=TEMPLATE_KEYS)
    def test_an_unknown_option_is_refused(self, template: Template) -> None:
        with pytest.raises(ValidationError):
            template.validate_options({"delivery": True})

    def test_the_generic_limits_still_apply(self) -> None:
        with pytest.raises(ValidationError):
            TEMPLATES["productos"].validate_options({"datos_requeridos": ["dni", "dni"]})


class TestPlaceholders:
    def test_names_and_variables_are_found(self) -> None:
        text = "Hola {nombre_cliente}, tema {var.temas_destacados} y {caminos}."

        assert placeholders_in(text) == {"nombre_cliente", "var.temas_destacados", "caminos"}

    def test_text_without_placeholders(self) -> None:
        assert placeholders_in("Sin nada") == set()

    def test_known_and_free_variables_are_accepted(self) -> None:
        assert is_known_placeholder("nombre_asistente")
        assert is_known_placeholder("var.lo_que_sea")

    def test_an_unknown_name_is_rejected(self) -> None:
        assert not is_known_placeholder("precio_total")
        assert not is_known_placeholder("numero")  # only for system messages

    def test_the_catalogue_of_known_placeholders(self) -> None:
        assert {
            "nombre_asistente",
            "nombre_negocio",
            "nombre_cliente",
            "telefono_humano",
            "link_pago_tarjeta",
            "pregunta_calificacion",
            "caminos",
            "datos_requeridos",
            "datos_faltantes",
        } == KNOWN_PLACEHOLDERS


class TestSystemMessages:
    def test_the_messages_of_the_contract(self) -> None:
        # The five of the contract, and the two of feature 016 that tell a stage of the work.
        assert set(DEFAULT_SYSTEM_MESSAGES) == {
            "pago_confirmado",
            "pago_rechazado",
            "pedido_rechazado",
            "pedido_anulado",
            "pedido_listo",
            "pedido_en_camino",
            "derivacion",
        }

    def test_their_placeholders_are_the_exclusive_ones(self) -> None:
        for key, text in DEFAULT_SYSTEM_MESSAGES.items():
            assert text.strip()
            assert placeholders_in(text) <= SYSTEM_MESSAGE_PLACEHOLDERS, key

    def test_the_confirmation_names_the_customer_and_the_order(self) -> None:
        text = DEFAULT_SYSTEM_MESSAGES["pago_confirmado"]

        assert {"nombre_cliente", "numero", "siguiente_paso"} == placeholders_in(text)

    def test_the_handover_message_has_no_placeholders(self) -> None:
        assert placeholders_in(DEFAULT_SYSTEM_MESSAGES["derivacion"]) == set()


class TestTheUniversalProcess:
    """Feature 020: one process for every business, hidden until the real-model evals pass."""

    def test_it_is_not_offered_to_businesses_yet(self) -> None:
        assert TEMPLATES["universal"].visible is False
        assert all(TEMPLATES[key].visible for key in ("productos", "certificaciones", "servicios"))

    def test_it_has_no_line_limit_and_takes_cash_on_delivery(self) -> None:
        universal = TEMPLATES["universal"]

        assert universal.max_lineas is None
        assert universal.validate_options({"modalidad_pago": "contra_entrega"})

    def test_nothing_of_one_business_is_in_its_texts(self) -> None:
        """Capacita's steps (the name first, «SIEMPRE SÍ», the anchor price) are its own
        configuration, not the platform's."""
        texts = " ".join(spec.instrucciones for spec in TEMPLATES["universal"].stages.values())

        for particular in ("SIEMPRE SÍ", "ancla", "certific", "Paso 1", "pídele su nombre"):
            assert particular not in texts

    def test_every_deciding_stage_points_to_how_items_close(self) -> None:
        universal = TEMPLATES["universal"]

        for stage in (Stage.INICIO, Stage.ANUNCIO, Stage.IDENTIFICAR):
            assert "CÓMO SE CIERRA" in universal.stage(stage).instrucciones
