"""The ten tools of the sales engine, declared (Feature 004, TASK-417).

`contracts/api.md` section 7 names ten tools. This module declares all of them -
text, schema, per-stage guards - and `default_catalog()` is the catalogue the
turn pipeline asks `for_stage` for the registry of each turn.

Which ones do something today:

* `buscar_productos`, `ver_producto`, `consultar_conocimiento`: real
  (`tools/catalog.py`).
* `enviar_recurso`, `mostrar_opciones`, `elegir_camino` (`tools/sales.py`).
* `gestionar_pedido` (`tools/orders.py`), `registrar_datos_cliente`
  (`tools/customer.py`), `enviar_metodos_pago` (`tools/payments.py`).
* `derivar_a_humano` (`tools/handover.py`, TASK-443): ends the turn.

The tools that write (the order, the customer's data, a message) are declared with
`effect=True`, so the agent loop never answers one from its per-turn cache after
another write.

The descriptions are short: the definitions of every enabled tool share a
1 400-token allowance of the 6 000-token prompt (`plan.md`).
"""

from __future__ import annotations

from functools import cache
from typing import Any

from app.agent.tools.catalog import (
    KNOWLEDGE_TOOL,
    SEARCH_TOOL,
    VIEW_TOOL,
    buscar_productos,
    consultar_conocimiento,
    ver_producto,
)
from app.agent.tools.customer import registrar_datos_cliente
from app.agent.tools.handover import derivar_a_humano
from app.agent.tools.orders import gestionar_pedido
from app.agent.tools.payments import enviar_metodos_pago
from app.agent.tools.registry import (
    ToolCatalog,
    ToolDefinition,
    ToolRegistry,
    ToolResult,
    ToolScope,
)
from app.agent.tools.sales import elegir_camino, enviar_recurso, mostrar_opciones
from app.process.facts import Facts
from app.process.templates.base import EffectiveOptions, Stage, Template

OPTION_TYPES = ("caminos", "categorias", "productos", "promociones")
ORDER_ACTIONS = (
    "agregar",
    "quitar",
    "cantidad",
    "elegir",
    "ver",
    "vaciar",
    "aplicar_promocion",
    "confirmar",
    "solicitar_cambio",
)
# While the business reviews the payment the order cannot change: the model can
# only show it, or leave the customer's request on it (Milestone B, decision 8).
UNDER_REVIEW_ACTIONS = ("ver", "solicitar_cambio")
HANDOVER_REASONS = ("pide_humano", "queja", "sin_respuesta", "asesoria", "cotizacion", "otro")
DELIVERY_TYPES = ("delivery", "recojo")

# What the customer can be asked for besides the template's own list. Offered
# only when the order needs delivery (`plantillas.md` section 1).
_DELIVERY_FIELDS = ("tipo_entrega", "direccion", "referencia", "zona_envio")
_MAX_OPTION_BODY = 1024
_MAX_SUMMARY = 500


def _object(properties: dict[str, Any], required: list[str] | None = None) -> dict[str, Any]:
    schema: dict[str, Any] = {"type": "object", "properties": properties}
    if required:
        schema["required"] = required
    return schema


def _text(description: str, **extra: Any) -> dict[str, Any]:
    return {"type": "string", "description": description, **extra}


# ------------------------------------------------------------------- schemas


def _search_schema(scope: ToolScope) -> dict[str, Any]:
    return _object(
        {
            "consulta": _text(
                "Nombre o tipo de producto que busca el cliente. Vacío para ver todos."
            ),
            "categoria": _text("Categoría a la que limitar la búsqueda."),
            "solo_paquetes": {"type": "boolean", "description": "Solo paquetes de N selecciones."},
        },
        ["consulta"],
    )


def _view_schema(scope: ToolScope) -> dict[str, Any]:
    return _object(
        {"producto_id": _text("El producto_id que devolvió buscar_productos.")}, ["producto_id"]
    )


def _knowledge_schema(scope: ToolScope) -> dict[str, Any]:
    return _object(
        {"consulta": _text("La duda del cliente, en sus propias palabras.")}, ["consulta"]
    )


def _resource_schema(scope: ToolScope) -> dict[str, Any]:
    return _object({"clave": _text("Clave del recurso (saludo, flyer_paquetes...).")}, ["clave"])


def _options_types(scope: ToolScope) -> tuple[str, ...]:
    # With no paths configured there is nothing to show under "caminos".
    if scope.options.caminos:
        return OPTION_TYPES
    return tuple(kind for kind in OPTION_TYPES if kind != "caminos")


def _options_schema(scope: ToolScope) -> dict[str, Any]:
    return _object(
        {
            "tipo": {"type": "string", "enum": list(_options_types(scope))},
            "texto": _text("Mensaje que acompaña las opciones.", maxLength=_MAX_OPTION_BODY),
            "filtro": _text('Solo con tipo=productos: "paquetes" o el nombre de una categoría.'),
        },
        ["tipo", "texto"],
    )


def _options_guard(scope: ToolScope, arguments: dict[str, Any]) -> ToolResult | None:
    if arguments.get("tipo") == "caminos" and not scope.options.caminos:
        return ToolResult.no_disponible(
            decir="Este negocio no tiene caminos configurados. No ofrezcas esa pregunta."
        )
    return None


def _may_confirm(scope: ToolScope) -> bool:
    """`confirmar` closes a cash-on-delivery order, and only at the charge stage."""
    return scope.stage is Stage.COBRO and scope.facts.modalidad_pago == "contra_entrega"


def _under_review(scope: ToolScope) -> bool:
    return scope.stage is Stage.VERIFICACION and scope.facts.pago_estado != "rechazado"


def _order_actions(scope: ToolScope) -> list[str]:
    if _under_review(scope):
        return list(UNDER_REVIEW_ACTIONS)
    return [
        a
        for a in ORDER_ACTIONS
        if a != "solicitar_cambio" and (a != "confirmar" or _may_confirm(scope))
    ]


def _order_schema(scope: ToolScope) -> dict[str, Any]:
    return _object(
        {
            "accion": {"type": "string", "enum": _order_actions(scope)},
            "producto_id": _text("El producto_id del producto."),
            "cantidad": {"type": "integer", "minimum": 1},
            "seleccion": _text(
                "Temas o elementos que el cliente elige para su paquete, todos los que dijo en "
                "una sola llamada, separados por comas (con agregar o con elegir)."
            ),
            "promocion_id": _text("La promoción de cierre que se ofrece."),
            "nota": _text("Qué quiere cambiar el cliente, en sus palabras (solicitar_cambio)."),
        },
        ["accion"],
    )


def _order_guard(scope: ToolScope, arguments: dict[str, Any]) -> ToolResult | None:
    if _under_review(scope) and arguments.get("accion") not in UNDER_REVIEW_ACTIONS:
        return ToolResult.no_disponible(
            decir=(
                "El pedido está en revisión del negocio y no se puede cambiar. Si el cliente "
                'quiere otra cosa, usa accion="solicitar_cambio" con su pedido en "nota".'
            )
        )
    if arguments.get("accion") == "confirmar" and not _may_confirm(scope):
        return ToolResult.no_disponible(
            decir=(
                "Confirmar solo aplica al cobrar un pedido contra entrega. Si el cliente ya pagó, "
                "una persona del negocio confirma el pago; no digas que quedó confirmado."
            )
        )
    return None


def customer_fields(scope: ToolScope) -> list[str]:
    """The fields `registrar_datos_cliente` accepts for this tenant and order.

    `nombres` always (the name may be given at any time), the ones the template
    asks for, and the delivery fields only when the order needs delivery.
    """
    fields = ["nombres", *scope.options.datos_requeridos]
    if scope.facts.requiere_entrega:
        fields.extend(_DELIVERY_FIELDS)
    return list(dict.fromkeys(fields))


def _customer_schema(scope: ToolScope) -> dict[str, Any]:
    properties: dict[str, Any] = {}
    for field_name in customer_fields(scope):
        if field_name == "tipo_entrega":
            properties[field_name] = {"type": "string", "enum": list(DELIVERY_TYPES)}
        else:
            properties[field_name] = {"type": "string"}
    return _object(properties)


def _methods_schema(scope: ToolScope) -> dict[str, Any]:
    return _object({})


def _path_keys(scope: ToolScope) -> list[str]:
    return [camino.clave for camino in scope.options.caminos]


def _path_schema(scope: ToolScope) -> dict[str, Any]:
    return _object({"camino": {"type": "string", "enum": _path_keys(scope)}}, ["camino"])


def _pays_up_front(scope: ToolScope) -> bool:
    """Cash on delivery sends no accounts: the order is confirmed, then paid on delivery."""
    return scope.facts.modalidad_pago != "contra_entrega"


def _has_paths(scope: ToolScope) -> bool:
    return bool(scope.options.caminos)


def _handover_schema(scope: ToolScope) -> dict[str, Any]:
    return _object(
        {
            "motivo": {"type": "string", "enum": list(HANDOVER_REASONS)},
            "resumen": _text("Qué necesita el cliente, en pocas líneas.", maxLength=_MAX_SUMMARY),
        },
        ["motivo", "resumen"],
    )


# ----------------------------------------------------------------- catalogue


def _definitions() -> list[ToolDefinition]:
    return [
        ToolDefinition(
            name=SEARCH_TOOL,
            description=(
                "Busca en el catálogo del negocio (productos, servicios o certificados) con su "
                "precio. Úsala antes de mencionar cualquier cosa que se vende o su precio."
            ),
            parameters=_search_schema,
            handler=buscar_productos,
        ),
        ToolDefinition(
            name=VIEW_TOOL,
            description="Muestra un producto completo, por su producto_id.",
            parameters=_view_schema,
            handler=ver_producto,
        ),
        ToolDefinition(
            name=KNOWLEDGE_TOOL,
            description=(
                "Consulta la información del negocio: horarios, políticas, preguntas "
                "frecuentes. Úsala antes de afirmar cualquier dato."
            ),
            parameters=_knowledge_schema,
            handler=consultar_conocimiento,
        ),
        ToolDefinition(
            name="enviar_recurso",
            description="Envía al cliente un archivo del negocio (imagen, PDF) por su clave.",
            parameters=_resource_schema,
            handler=enviar_recurso,
        ),
        ToolDefinition(
            name="mostrar_opciones",
            description="Envía botones o una lista para que el cliente toque una opción.",
            parameters=_options_schema,
            handler=mostrar_opciones,
            guard=_options_guard,
        ),
        ToolDefinition(
            name="gestionar_pedido",
            description=(
                "Crea o modifica el pedido del cliente. Cada cambio pasa por aquí; los "
                "totales los calcula el sistema."
            ),
            parameters=_order_schema,
            handler=gestionar_pedido,
            guard=_order_guard,
            effect=True,
        ),
        ToolDefinition(
            name="registrar_datos_cliente",
            description="Guarda los datos que el cliente dé, aunque sean parciales.",
            parameters=_customer_schema,
            handler=registrar_datos_cliente,
            effect=True,
        ),
        ToolDefinition(
            name="enviar_metodos_pago",
            description="Envía al cliente los métodos de pago del negocio.",
            parameters=_methods_schema,
            handler=enviar_metodos_pago,
            effect=True,
            available=_pays_up_front,
        ),
        ToolDefinition(
            name="elegir_camino",
            description="Registra el camino que el cliente escogió escribiendo.",
            parameters=_path_schema,
            handler=elegir_camino,
            available=_has_paths,
        ),
        ToolDefinition(
            name="derivar_a_humano",
            description=(
                "Pasa la conversación a una persona del negocio. Termina tu turno: lo que "
                "escribas después se descarta."
            ),
            parameters=_handover_schema,
            handler=derivar_a_humano,
            effect=True,
        ),
    ]


@cache
def default_catalog() -> ToolCatalog:
    """The catalogue of the ten tools. Built once: it holds no per-tenant state."""
    return ToolCatalog(_definitions())


def registry_for_stage(
    template: Template, stage: Stage, facts: Facts, options: EffectiveOptions
) -> ToolRegistry:
    """The registry of one turn: the stage's tools plus the global ones."""
    return default_catalog().for_stage(template, stage, facts, options)
