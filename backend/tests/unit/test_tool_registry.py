"""The tool envelope, the stage registries and the ten declared tools (TASK-417).

Pure: the catalogue is asked for the registry of a stage with hand-made facts, so
which tools a stage exposes, how the schema adapts to the tenant and which calls
a guard refuses are checked without a database.
"""

from __future__ import annotations

import json
import uuid
from typing import Any, cast

import pytest

from app.agent.prompts.stage import SECTION_CAPS, TOKEN_LIMIT, build_stage_prompt, estimate_tokens
from app.agent.tools.definitions import (
    default_catalog,
    registry_for_stage,
)
from app.agent.tools.registry import (
    LEGACY_ALIASES,
    ToolCatalog,
    ToolContext,
    ToolDefinition,
    ToolRegistry,
    ToolResult,
)
from app.process.facts import Facts, Pedido
from app.process.templates import GLOBAL_TOOLS, TEMPLATES, TOOL_NAMES
from app.process.templates.base import Stage
from app.schemas.process_options import ProcessOptions
from tests.unit.test_stage_prompt import NOW, full_facts, make_config

CERT = TEMPLATES["certificaciones"]
SHOP = TEMPLATES["productos"]
SERVICES = TEMPLATES["servicios"]
CERT_OPTIONS = CERT.resolve()
SHOP_OPTIONS = SHOP.resolve()


def _names(registry: ToolRegistry) -> set[str]:
    return set(registry.names())


def _tool_args(registry: ToolRegistry, name: str) -> dict[str, Any]:
    tool = registry.get(name)
    assert tool is not None
    return tool.spec.parameters["properties"]


async def _call(registry: ToolRegistry, name: str, **arguments: Any) -> str | ToolResult:
    tool = registry.get(name)
    assert tool is not None, f"{name} is not offered"
    # The stubs and the guards never touch the database or the model.
    context = ToolContext(session=cast(Any, None), tenant_id=uuid.uuid4(), llm=cast(Any, None))
    return await tool.handler(context, arguments)


# -------------------------------------------------------------------- envelope


class TestToolResult:
    def test_done_shows_its_status_and_the_prose(self) -> None:
        result = ToolResult.hecho(texto="Pedido creado.", decir="Dile que ya lo tiene.")

        assert result.render() == (
            "estado: hecho\nPedido creado.\nQué decir al cliente: Dile que ya lo tiene."
        )
        assert result.ok

    def test_without_prose_the_data_is_shown_as_json(self) -> None:
        result = ToolResult.hecho(data={"total": "500.00", "nombre": "Paquete ñ"})

        assert result.render() == 'estado: hecho\n{"total": "500.00", "nombre": "Paquete ñ"}'

    def test_data_that_is_not_json_native_is_still_rendered(self) -> None:
        from decimal import Decimal

        assert "500.00" in ToolResult.hecho(data={"total": Decimal("500.00")}).render()

    def test_already_done_says_so(self) -> None:
        result = ToolResult.omitido(decir="Ya se envió; no lo menciones.")

        assert (
            result.render()
            == "estado: omitido\nQué decir al cliente: Ya se envió; no lo menciones."
        )

    def test_not_available_carries_its_instruction(self) -> None:
        result = ToolResult.no_disponible(decir="Ofrécelo en tu próximo mensaje.")

        assert result.status == "no_disponible"
        assert "Ofrécelo en tu próximo mensaje." in result.render()

    def test_an_error_keeps_the_prefix_the_loop_looks_for(self) -> None:
        result = ToolResult.error("falta producto_id")

        assert result.render() == "ERROR: falta producto_id"
        assert not result.ok

    def test_an_error_without_a_message_still_says_something(self) -> None:
        assert ToolResult("error").render() == "ERROR: la herramienta falló."

    def test_the_four_statuses_of_the_contract_exist(self) -> None:
        statuses = {
            ToolResult.hecho().status,
            ToolResult.omitido().status,
            ToolResult.no_disponible().status,
            ToolResult.error("x").status,
        }

        assert statuses == {"hecho", "omitido", "no_disponible", "error"}


# -------------------------------------------------------------------- registry


class TestAliases:
    def _registry(self) -> ToolRegistry:
        registry = ToolRegistry()

        async def handler(context: ToolContext, arguments: dict[str, Any]) -> str:
            return "ok"

        registry.register(name="nueva", description="", parameters={}, handler=handler)
        return registry

    def test_an_alias_resolves_but_is_not_offered(self) -> None:
        registry = self._registry()
        registry.alias("vieja", "nueva")

        assert registry.get("vieja") is registry.get("nueva")
        assert registry.names() == ("nueva",)
        assert [spec.name for spec in registry.specs()] == ["nueva"]

    def test_an_alias_to_nothing_is_refused(self) -> None:
        with pytest.raises(ValueError, match="unregistered"):
            self._registry().alias("vieja", "no_existe")

    def test_an_alias_cannot_shadow_a_real_tool(self) -> None:
        with pytest.raises(ValueError, match="not an alias"):
            self._registry().alias("nueva", "nueva")

    def test_registering_twice_is_refused(self) -> None:
        registry = self._registry()

        with pytest.raises(ValueError, match="already registered"):
            registry.register(name="nueva", description="", parameters={}, handler=cast(Any, None))


class TestCatalog:
    def test_the_catalogue_declares_exactly_the_ten_tools_of_the_contract(self) -> None:
        assert set(default_catalog().names()) == set(TOOL_NAMES)
        assert len(default_catalog().names()) == 10

    def test_a_tool_cannot_be_defined_twice(self) -> None:
        definition = default_catalog().get("ver_producto")

        with pytest.raises(ValueError, match="defined twice"):
            ToolCatalog([definition, definition])

    def test_the_catalogue_is_built_once(self) -> None:
        assert default_catalog() is default_catalog()

    def test_every_tool_has_a_description_and_an_object_schema(self) -> None:
        registry = registry_for_stage(CERT, Stage.IDENTIFICAR, Facts(), CERT_OPTIONS)

        for spec in registry.specs():
            assert spec.description
            assert spec.parameters["type"] == "object"


# ------------------------------------------------------------ tools by stage

_EXPECTED_OWN_TOOLS: dict[Stage, set[str]] = {
    Stage.INICIO: {"buscar_productos", "ver_producto", "mostrar_opciones", "gestionar_pedido"},
    Stage.CALIFICAR: {"mostrar_opciones", "elegir_camino"},
    Stage.ANUNCIO: {
        "buscar_productos",
        "ver_producto",
        "mostrar_opciones",
        "gestionar_pedido",
        "elegir_camino",
    },
    Stage.IDENTIFICAR: {
        "buscar_productos",
        "ver_producto",
        "mostrar_opciones",
        "gestionar_pedido",
        "elegir_camino",
    },
    Stage.PEDIDO: {"gestionar_pedido", "buscar_productos", "ver_producto", "mostrar_opciones"},
    Stage.DATOS: {"gestionar_pedido"},
    Stage.COBRO: {"enviar_metodos_pago", "gestionar_pedido"},
    Stage.VERIFICACION: set(),
    Stage.CERRADO: {"buscar_productos", "ver_producto", "gestionar_pedido"},
    Stage.ASESORIA: {"buscar_productos", "ver_producto", "mostrar_opciones", "elegir_camino"},
}


class TestEachStageExposesOnlyItsOwnTools:
    @pytest.mark.parametrize("stage", [s for s in Stage if s is not Stage.DERIVADO])
    def test_productos(self, stage: Stage) -> None:
        template = SHOP if stage is not Stage.INICIO else SHOP
        registry = registry_for_stage(template, stage, Facts(), SHOP_OPTIONS)

        # `productos` has no paths, so `elegir_camino` never appears.
        expected = (_EXPECTED_OWN_TOOLS[stage] - {"elegir_camino"}) | set(GLOBAL_TOOLS)
        assert _names(registry) == expected

    @pytest.mark.parametrize("stage", [s for s in Stage if s not in (Stage.DERIVADO, Stage.INICIO)])
    def test_certificaciones_with_paths(self, stage: Stage) -> None:
        registry = registry_for_stage(CERT, stage, Facts(), CERT_OPTIONS)

        assert _names(registry) == _EXPECTED_OWN_TOOLS[stage] | set(GLOBAL_TOOLS)

    def test_certificaciones_only_greets_in_its_first_turn(self) -> None:
        registry = registry_for_stage(CERT, Stage.INICIO, Facts(), CERT_OPTIONS)

        assert _names(registry) == set(GLOBAL_TOOLS)

    def test_the_globals_are_always_there(self) -> None:
        for template in TEMPLATES.values():
            for stage in Stage:
                if stage is Stage.DERIVADO:
                    continue
                registry = registry_for_stage(template, stage, Facts(), template.resolve())
                assert set(GLOBAL_TOOLS) <= _names(registry), (template.key, stage)

    @pytest.mark.parametrize("template", list(TEMPLATES.values()), ids=lambda t: t.key)
    def test_a_paused_conversation_has_no_tools(self, template: Any) -> None:
        registry = registry_for_stage(template, Stage.DERIVADO, Facts(), template.resolve())

        assert len(registry) == 0
        assert registry.specs() == ()

    def test_a_payment_under_review_offers_only_the_globals(self) -> None:
        facts = Facts(pago_estado="por_verificar")

        registry = registry_for_stage(SHOP, Stage.VERIFICACION, facts, SHOP_OPTIONS)

        assert _names(registry) == set(GLOBAL_TOOLS)

    def test_a_rejected_payment_may_resend_the_methods_and_change_the_order(self) -> None:
        facts = Facts(pago_estado="rechazado")

        registry = registry_for_stage(SHOP, Stage.VERIFICACION, facts, SHOP_OPTIONS)

        assert _names(registry) == {"enviar_metodos_pago", "gestionar_pedido", *GLOBAL_TOOLS}

    def test_every_tool_offered_exists_in_the_vocabulary(self) -> None:
        for template in TEMPLATES.values():
            for stage in Stage:
                registry = registry_for_stage(template, stage, full_facts(), template.resolve())
                assert _names(registry) <= set(TOOL_NAMES)


class TestThePathTool:
    def test_it_is_hidden_when_there_are_no_paths(self) -> None:
        for stage in (Stage.CALIFICAR, Stage.ANUNCIO, Stage.IDENTIFICAR, Stage.ASESORIA):
            registry = registry_for_stage(SHOP, stage, Facts(), SHOP_OPTIONS)
            assert "elegir_camino" not in _names(registry), stage

    def test_it_is_hidden_when_the_tenant_switches_the_paths_off(self) -> None:
        options = CERT.resolve(ProcessOptions.model_validate({"calificacion": {"activa": False}}))

        registry = registry_for_stage(CERT, Stage.IDENTIFICAR, Facts(), options)

        assert "elegir_camino" not in _names(registry)

    def test_its_enum_is_the_tenants_own_paths(self) -> None:
        registry = registry_for_stage(CERT, Stage.CALIFICAR, Facts(), CERT_OPTIONS)

        assert _tool_args(registry, "elegir_camino")["camino"]["enum"] == [
            "certificacion",
            "programa",
        ]

    def test_another_tenant_gets_its_own_enum(self) -> None:
        options = SHOP.resolve(
            ProcessOptions.model_validate(
                {
                    "calificacion": {
                        "activa": True,
                        "pregunta": "¿Qué buscas?",
                        "caminos": [
                            {"clave": "comprar", "titulo": "Comprar", "tipo": "venta"},
                            {"clave": "asesor", "titulo": "Hablar", "tipo": "asesoria"},
                        ],
                    }
                }
            )
        )

        registry = registry_for_stage(SHOP, Stage.CALIFICAR, Facts(), options)

        assert _tool_args(registry, "elegir_camino")["camino"]["enum"] == ["comprar", "asesor"]


class TestTheOptionsTool:
    def test_without_paths_the_type_enum_has_no_paths(self) -> None:
        registry = registry_for_stage(SHOP, Stage.IDENTIFICAR, Facts(), SHOP_OPTIONS)

        assert _tool_args(registry, "mostrar_opciones")["tipo"]["enum"] == [
            "categorias",
            "productos",
            "promociones",
        ]

    def test_with_paths_the_type_enum_has_them(self) -> None:
        registry = registry_for_stage(CERT, Stage.CALIFICAR, Facts(), CERT_OPTIONS)

        assert _tool_args(registry, "mostrar_opciones")["tipo"]["enum"][0] == "caminos"

    async def test_a_hallucinated_paths_call_is_refused_when_there_are_none(self) -> None:
        registry = registry_for_stage(SHOP, Stage.IDENTIFICAR, Facts(), SHOP_OPTIONS)

        result = await _call(registry, "mostrar_opciones", tipo="caminos", texto="¿Qué buscas?")

        assert isinstance(result, ToolResult)
        assert result.status == "no_disponible"
        assert "caminos" in (result.decir or "")

    async def test_a_configured_paths_call_reaches_the_handler(self) -> None:
        registry = registry_for_stage(CERT, Stage.CALIFICAR, Facts(), CERT_OPTIONS)

        result = await _call(registry, "mostrar_opciones", tipo="caminos", texto="¿Qué buscas?")

        # The guard let it through; the handler found nothing to send with (this
        # context has no adapter), which is its own refusal, not the guard's.
        assert isinstance(result, ToolResult)
        assert result.status == "no_disponible"
        assert "caminos" not in (result.decir or "")


class TestConfirmingAnOrder:
    cash = Facts(
        modalidad_pago="contra_entrega",
        pedido=Pedido(estado="borrador"),
    )

    def _actions(self, stage: Stage, facts: Facts) -> list[str]:
        registry = registry_for_stage(SHOP, stage, facts, SHOP_OPTIONS)
        return _tool_args(registry, "gestionar_pedido")["accion"]["enum"]

    def test_confirm_is_offered_at_the_charge_stage_with_cash_on_delivery(self) -> None:
        assert "confirmar" in self._actions(Stage.COBRO, self.cash)

    def test_confirm_is_not_offered_when_the_payment_is_up_front(self) -> None:
        assert "confirmar" not in self._actions(Stage.COBRO, Facts(modalidad_pago="previo"))

    @pytest.mark.parametrize("stage", [Stage.IDENTIFICAR, Stage.PEDIDO, Stage.DATOS, Stage.CERRADO])
    def test_confirm_is_not_offered_outside_the_charge_stage(self, stage: Stage) -> None:
        assert "confirmar" not in self._actions(stage, self.cash)

    def test_the_other_seven_actions_are_always_offered(self) -> None:
        assert self._actions(Stage.PEDIDO, Facts()) == [
            "agregar",
            "quitar",
            "cantidad",
            "elegir",
            "ver",
            "vaciar",
            "aplicar_promocion",
        ]

    @pytest.mark.parametrize("stage", [Stage.IDENTIFICAR, Stage.PEDIDO, Stage.DATOS])
    async def test_confirming_outside_the_charge_stage_is_refused(self, stage: Stage) -> None:
        registry = registry_for_stage(SHOP, stage, self.cash, SHOP_OPTIONS)

        result = await _call(registry, "gestionar_pedido", accion="confirmar")

        assert isinstance(result, ToolResult)
        assert result.status == "no_disponible"
        assert "contra entrega" in (result.decir or "")

    async def test_confirming_a_prepaid_order_is_refused_even_at_the_charge_stage(self) -> None:
        registry = registry_for_stage(
            SHOP, Stage.COBRO, Facts(modalidad_pago="previo"), SHOP_OPTIONS
        )

        result = await _call(registry, "gestionar_pedido", accion="confirmar")

        assert isinstance(result, ToolResult)
        assert result.status == "no_disponible"
        assert result.data == {}  # refused by the guard, the handler never ran

    async def test_confirming_cash_on_delivery_at_the_charge_stage_reaches_the_handler(
        self,
    ) -> None:
        registry = registry_for_stage(SHOP, Stage.COBRO, self.cash, SHOP_OPTIONS)

        result = await _call(registry, "gestionar_pedido", accion="confirmar")

        # Past the guard, the handler runs: with no conversation it has nothing to work on.
        assert isinstance(result, ToolResult)
        assert result.status == "error"
        assert "conversación" in (result.decir or "")

    async def test_other_actions_are_not_guarded(self) -> None:
        registry = registry_for_stage(SHOP, Stage.PEDIDO, Facts(), SHOP_OPTIONS)

        result = await _call(registry, "gestionar_pedido", accion="agregar")

        assert isinstance(result, ToolResult)
        assert result.status == "error"
        assert "conversación" in (result.decir or "")


class TestCustomerData:
    def _fields(self, template: Any, facts: Facts) -> list[str]:
        registry = registry_for_stage(template, Stage.DATOS, facts, template.resolve())
        return list(_tool_args(registry, "registrar_datos_cliente"))

    def test_it_accepts_the_name_and_what_the_template_asks_for(self) -> None:
        assert self._fields(CERT, Facts()) == [
            "nombres",
            "dni",
            "apellidos",
            "correo",
            "telefono",
        ]

    def test_a_template_with_other_fields_changes_the_schema(self) -> None:
        assert self._fields(SERVICES, Facts()) == ["nombres", "empresa", "correo"]

    def test_delivery_fields_appear_only_when_the_order_needs_delivery(self) -> None:
        fields = self._fields(SHOP, Facts(requiere_entrega=True))

        assert fields == [
            "nombres",
            "telefono",
            "tipo_entrega",
            "direccion",
            "referencia",
            "zona_envio",
        ]

    def test_the_delivery_type_is_one_of_two(self) -> None:
        registry = registry_for_stage(SHOP, Stage.DATOS, Facts(requiere_entrega=True), SHOP_OPTIONS)

        assert _tool_args(registry, "registrar_datos_cliente")["tipo_entrega"]["enum"] == [
            "delivery",
            "recojo",
        ]

    def test_a_field_is_never_listed_twice(self) -> None:
        options = SHOP.resolve(ProcessOptions(datos_requeridos=["nombres", "direccion"]))
        registry = registry_for_stage(SHOP, Stage.DATOS, Facts(requiere_entrega=True), options)

        fields = list(_tool_args(registry, "registrar_datos_cliente"))

        assert fields.count("direccion") == 1
        assert fields.count("nombres") == 1


class TestTheHandover:
    def test_the_reason_is_one_of_six_and_the_summary_is_capped(self) -> None:
        registry = registry_for_stage(SHOP, Stage.IDENTIFICAR, Facts(), SHOP_OPTIONS)
        properties = _tool_args(registry, "derivar_a_humano")

        assert properties["motivo"]["enum"] == [
            "pide_humano",
            "queja",
            "sin_respuesta",
            "asesoria",
            "cotizacion",
            "otro",
        ]
        assert properties["resumen"]["maxLength"] == 500


# ------------------------------------------------------------------------ stubs


class TestTheToolsOfLaterTasksAnswerNotAvailable:
    @pytest.mark.parametrize(
        ("tool", "stage"),
        [
            ("registrar_datos_cliente", Stage.DATOS),
            ("enviar_metodos_pago", Stage.COBRO),
            ("derivar_a_humano", Stage.IDENTIFICAR),
        ],
    )
    async def test_the_stub(self, tool: str, stage: Stage) -> None:
        registry = registry_for_stage(CERT, stage, Facts(), CERT_OPTIONS)

        result = await _call(registry, tool)

        assert isinstance(result, ToolResult)
        assert result.status == "no_disponible"
        assert result.data == {"herramienta": tool}
        # The model is told not to mention it; it must not promise anything.
        assert "No la menciones" in (result.decir or "")


# ------------------------------------------------------------------- the alias


class TestTheOldCatalogueToolStillWorks:
    def test_the_alias_is_declared(self) -> None:
        assert LEGACY_ALIASES == {"consultar_catalogo": "buscar_productos"}

    def test_the_old_name_resolves_wherever_the_search_is_enabled(self) -> None:
        registry = registry_for_stage(SHOP, Stage.IDENTIFICAR, Facts(), SHOP_OPTIONS)

        assert registry.get("consultar_catalogo") is registry.get("buscar_productos")

    def test_the_old_name_is_not_offered_to_the_model(self) -> None:
        registry = registry_for_stage(SHOP, Stage.IDENTIFICAR, Facts(), SHOP_OPTIONS)

        assert "consultar_catalogo" not in registry.names()
        assert "consultar_catalogo" not in [spec.name for spec in registry.specs()]

    def test_it_does_not_come_back_where_the_search_is_disabled(self) -> None:
        registry = registry_for_stage(SHOP, Stage.DATOS, Facts(), SHOP_OPTIONS)

        assert registry.get("consultar_catalogo") is None


# ----------------------------------------------------------------------- budget

_ALL = [(t, s) for t in TEMPLATES for s in Stage]


class TestThePromptBudgetWithTheRealTools:
    @pytest.mark.parametrize(("template_key", "stage"), _ALL)
    def test_the_enabled_definitions_fit_their_allowance_and_the_whole_fits_6000(
        self, template_key: str, stage: Stage
    ) -> None:
        template = TEMPLATES[template_key]
        config = make_config(template_key, full=True)
        facts = full_facts(requiere_entrega=True, modalidad_pago="contra_entrega")
        registry = registry_for_stage(template, stage, facts, config.options)

        prompt = build_stage_prompt(config, facts, stage, now=NOW, tool_specs=registry.specs())

        assert prompt.sections["herramientas"] <= SECTION_CAPS["herramientas"]
        assert prompt.tokens <= TOKEN_LIMIT, prompt.sections
        assert prompt.over_budget == []

    def test_all_ten_tools_together_still_fit_the_allowance(self) -> None:
        """The richest case: every tool at once, which no stage actually has."""
        from app.agent.tools.registry import ToolScope

        scope = ToolScope(
            template=CERT,
            stage=Stage.IDENTIFICAR,
            facts=full_facts(requiere_entrega=True),
            options=CERT_OPTIONS,
        )
        specs = [
            {
                "name": d.name,
                "description": d.description,
                "parameters": d.parameters(scope),
            }
            for d in (default_catalog().get(name) for name in default_catalog().names())
        ]

        assert (
            estimate_tokens(json.dumps(specs, ensure_ascii=False)) <= SECTION_CAPS["herramientas"]
        )

    def test_a_definition_is_a_plain_value(self) -> None:
        assert isinstance(default_catalog().get("ver_producto"), ToolDefinition)
