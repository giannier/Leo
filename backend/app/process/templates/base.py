# ruff: noqa: E501
# The default instruction texts below are kept verbatim from `plantillas.md`,
# line breaks included; reflowing them to the code line length would make them
# drift from the document the business owner reads.
"""Process template building blocks (Feature 004, `plantillas.md`).

A template is code, not data (C-05): the order of the stages, their conditions,
the tools each one enables and the funnel column it maps to are versioned here
and reviewed in a PR. A tenant customises only the *options* (which the template
validates). The instruction texts below are the source of the catalogue's
`proceso v1` (017, TASK-460): at run time the stage text comes from the
published version in the database, as rules a business can switch off, and these
texts are only the fallback when nothing is published.

This module holds the pieces every template shares: the stage vocabulary, the
tool vocabulary, the default texts that apply to all templates (`[base]` in
`plantillas.md` section 5), the follow-up texts, the system messages and the
`Template` / `EffectiveOptions` types. The three concrete templates only declare
what differs.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum
from types import MappingProxyType
from typing import Literal

from app.models.conversation_state import FunnelCode
from app.schemas.process_options import Calificacion, Camino, ProcessOptions

# --------------------------------------------------------------------- stages


class Stage(StrEnum):
    """Where a conversation is in the sale (`plantillas.md` section 1)."""

    INICIO = "INICIO"
    CALIFICAR = "CALIFICAR"
    ANUNCIO = "ANUNCIO"
    IDENTIFICAR = "IDENTIFICAR"
    PEDIDO = "PEDIDO"
    DATOS = "DATOS"
    COBRO = "COBRO"
    VERIFICACION = "VERIFICACION"
    CERRADO = "CERRADO"
    ASESORIA = "ASESORIA"
    DERIVADO = "DERIVADO"


# ---------------------------------------------------------------------- tools

# The ten tools the model can be given (`contracts/api.md` section 7). The
# registry (TASK-417) declares each one's schema; the templates only name them.
# `consultar_catalogo`, the single catalogue tool the agent has today, is split
# into `buscar_productos` and `ver_producto`: see "Decisiones abiertas" in the
# TASK-409 notes of `conciliacion.md`.
TOOL_NAMES: tuple[str, ...] = (
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
)

# The tools of a business whose agent also books appointments (capability `agendar`, feature 019):
# on top of the stage's own tools, in every stage that runs the model.
SCHEDULING_TOOL_NAMES: tuple[str, ...] = ("consultar_horarios", "agendar_cita", "gestionar_cita")

# The tool of a business whose agent also qualifies leads (capability `captar`, feature 019, phase E).
LEAD_TOOL_NAMES: tuple[str, ...] = ("calificar_cliente",)

# The tools of a business that sells, for after the sale (feature 019, phase G): what happened to an
# order already made, a return or a claim about it, and the documents of the order.
POSTSALE_TOOL_NAMES: tuple[str, ...] = (
    "consultar_pedidos",
    "solicitar_devolucion",
    "enviar_documento",
)

# Available in every stage that runs the model, on top of the stage's own tools.
GLOBAL_TOOLS: tuple[str, ...] = (
    "consultar_conocimiento",
    "enviar_recurso",
    "derivar_a_humano",
    "registrar_datos_cliente",
)

_SEARCH = ("buscar_productos", "ver_producto", "mostrar_opciones")
_SEARCH_AND_ORDER = (*_SEARCH, "gestionar_pedido")

INICIO_TOOLS_WITH_ORDER = _SEARCH_AND_ORDER
INICIO_TOOLS_WITHOUT_ORDER = _SEARCH
INICIO_TOOLS_GREET_ONLY: tuple[str, ...] = ()

_TOOLS_BY_STAGE: Mapping[Stage, tuple[str, ...]] = MappingProxyType(
    {
        Stage.CALIFICAR: ("mostrar_opciones", "elegir_camino"),
        Stage.ANUNCIO: (*_SEARCH_AND_ORDER, "elegir_camino"),
        Stage.IDENTIFICAR: (*_SEARCH_AND_ORDER, "elegir_camino"),
        Stage.PEDIDO: ("gestionar_pedido", "buscar_productos", "ver_producto", "mostrar_opciones"),
        Stage.DATOS: ("gestionar_pedido",),
        Stage.COBRO: ("enviar_metodos_pago", "gestionar_pedido"),
        # Waiting on the business: the order can only be shown, or a change the
        # customer asks for noted for the operator (Milestone B, decision 8).
        # Rejected: the customer may fix it.
        Stage.VERIFICACION: ("gestionar_pedido",),
        Stage.CERRADO: ("buscar_productos", "ver_producto", "gestionar_pedido"),
        Stage.ASESORIA: (*_SEARCH, "elegir_camino"),
        Stage.DERIVADO: (),
    }
)
# With the search: to add something the model needs its producto_id (Milestone B run).
_VERIFICATION_TOOLS_IF_REJECTED = ("enviar_metodos_pago", "gestionar_pedido", "buscar_productos")

# ------------------------------------------------------------------- funnel

_FUNNEL_BY_STAGE: Mapping[Stage, FunnelCode] = MappingProxyType(
    {
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
)

# --------------------------------------------------------------- placeholders

# `{name}` for the values the prompt builder fills in, and `{var.key}` for the
# tenant's free variables (`process_options.variables`).
PLACEHOLDER_PATTERN = re.compile(r"\{([a-z][a-z0-9_]*(?:\.[a-z][a-z0-9_]*)?)\}")

KNOWN_PLACEHOLDERS: frozenset[str] = frozenset(
    {
        "nombre_asistente",
        "nombre_negocio",
        "nombre_cliente",
        "telefono_humano",
        "link_pago_tarjeta",
        "pregunta_calificacion",
        "caminos",
        "datos_requeridos",
        "datos_faltantes",
    }
)

# Only the system messages (section 7) may use these.
SYSTEM_MESSAGE_PLACEHOLDERS: frozenset[str] = frozenset(
    {"nombre_cliente", "numero", "motivo", "siguiente_paso"}
)

_VARIABLE_PREFIX = "var."


def placeholders_in(text: str) -> set[str]:
    """Every `{placeholder}` in a text, with `var.<key>` kept whole."""
    return set(PLACEHOLDER_PATTERN.findall(text))


def is_known_placeholder(name: str) -> bool:
    """Whether a prompt text may use `{name}`."""
    return name in KNOWN_PLACEHOLDERS or name.startswith(_VARIABLE_PREFIX)


# ---------------------------------------------------------- default texts [base]

INICIO_BASE = """\
Es tu primer mensaje en esta conversación.
- Si existe el recurso "saludo", envíalo con enviar_recurso.
- Saluda en una línea, preséntate como {nombre_asistente} de {nombre_negocio}.
- Si el cliente ya preguntó algo, respóndelo en este mismo mensaje usando tus herramientas.
- Si no preguntó nada, pregúntale en qué lo puedes ayudar."""

CALIFICAR_BASE = """\
Necesitas saber qué busca el cliente para llevarlo por el camino correcto.
- Si aún no sabes su nombre, pídelo. Si en ese mismo mensaje ya dijo qué busca o hizo una pregunta, primero registra el camino con elegir_camino y responde su pregunta: nunca condiciones una respuesta a que te dé su nombre.
- Con el nombre, llama a mostrar_opciones(tipo="caminos") con este texto, personalizado con su nombre:
  "{pregunta_calificacion}"
- Si responde ESCRIBIENDO en vez de tocar un botón, llama a elegir_camino con la opción que corresponda:
  {caminos}
- Si la respuesta es ambigua, vuelve a preguntar con tus palabras. No adivines."""

ANUNCIO_BASE = """\
El cliente llegó por un anuncio y YA vio la oferta (está en el ESTADO). No hagas preguntas de calificación
ni le muestres el catálogo desde cero.
- Si no sabes su nombre, pídelo, pero en el mismo mensaje ve a lo anunciado: nunca condiciones la información a que te dé su nombre.
- Ve directo a lo anunciado: búscalo con buscar_productos, muéstralo y envía el recurso del producto si existe.
- Si lo anunciado corresponde a un camino, llama a elegir_camino en este mismo turno.
- Si dice que lo quiere, agrégalo con gestionar_pedido."""

PEDIDO_BASE = """\
El pedido está en el ESTADO y aún le falta algo para estar completo.
- Dile qué tiene y qué le falta, en una línea.
- Cada cambio pasa por gestionar_pedido. Nunca digas que agregaste o quitaste algo sin llamarla.
- Si quiere algo más, búscalo y agrégalo. Si quiere cambiar algo, quítalo y agrega lo nuevo."""

DATOS_BASE = """\
El pedido está completo. Pide los datos que faltan ({datos_faltantes}) en UN solo mensaje, explicando para
qué son. Guarda con registrar_datos_cliente todo lo que mande, aunque sea un dato suelto, y pide solo lo
que siga faltando. No envíes métodos de pago todavía."""

COBRO_BASE = """\
Pedido y datos completos.
Si el pago es previo:
- Si los métodos de pago NO se enviaron, llama a enviar_metodos_pago y dile que cuando pague envíe la foto
  del comprobante por aquí.
  Si existe {link_pago_tarjeta}, menciónalo para pago con tarjeta.
- Si ya se enviaron y pregunta, dile que revise el mensaje anterior y pídele la foto del comprobante.
Si el pago es contra entrega:
- Resume el pedido (productos, total, entrega) y pregúntale si lo confirma. Cuando confirme, llama a
  gestionar_pedido(accion="confirmar").
Si envió una imagen y ESTE TURNO dice que NO es un comprobante, pídele la foto del comprobante.
Si el ESTADO trae el motivo de un rechazo anterior del negocio, tenlo en cuenta y ayúdalo a corregir."""

VERIFICACION_BASE = """\
- Si el pago está por verificar: dile que su comprobante (o su pedido) está en revisión y que le avisamos
  apenas se confirme. No pidas el comprobante de nuevo, no reenvíes los métodos de pago y no digas que está
  confirmado.
- Si quiere cambiar el pedido mientras está en revisión: explícale que ahora no se puede modificar y deja
  anotado lo que pide con gestionar_pedido(accion="solicitar_cambio", nota=...). El negocio lo verá al revisar.
- Si el pago fue rechazado: el ESTADO trae el motivo. Explícaselo con amabilidad y ayúdalo a corregirlo:
  nueva foto, nuevo pago (puedes reenviar los métodos de pago una vez), o un cambio en el pedido o en sus
  datos con gestionar_pedido o registrar_datos_cliente. Con el pago rechazado el pedido sí se puede
  cambiar: hazlo en el mismo turno (búscalo con buscar_productos y usa agregar, quitar o cambiar_cantidad;
  nunca solicitar_cambio), sin preguntarle si quiere que lo hagas. Si no se resuelve, usa derivar_a_humano.
- Dudas generales (tiempos, entrega): respóndelas con consultar_conocimiento. No vuelvas a vender."""

CERRADO_BASE = """\
El pago está confirmado. Atiéndelo sobre su pedido (tiempos, entrega, cómo recibe lo que compró) con
consultar_conocimiento. No pidas comprobante ni reenvíes métodos de pago. Si quiere comprar algo más,
búscalo y agrégalo con gestionar_pedido: se abrirá un pedido nuevo."""

ASESORIA_BASE = """\
El cliente eligió un camino que atiende un asesor.
Si el ESTADO NO dice "asesoría ya derivada": haz máximo 2 preguntas para entender qué necesita, pídele su
nombre y cómo contactarlo (para qué: que el asesor le escriba) y llama a
derivar_a_humano(motivo="asesoria") con un resumen claro. Dile que un asesor le escribirá pronto.
Si el ESTADO dice "asesoría ya derivada": responde sus dudas con consultar_conocimiento, no vuelvas a
derivar salvo que pida hablar con alguien, y si muestra interés en otro camino, ofrécelo con
mostrar_opciones(tipo="caminos") o regístralo con elegir_camino."""

# Not in `plantillas.md`: while a conversation is paused for a person the engine
# never builds a model turn (section 2, "no hay turno de modelo"). The text only
# exists so that every stage carries one and a mistaken call degrades safely.
DERIVADO_BASE = """\
Esta conversación está pausada para que la atienda una persona del negocio.
No respondas al cliente."""

_BASE_INSTRUCTIONS: Mapping[Stage, str] = MappingProxyType(
    {
        Stage.INICIO: INICIO_BASE,
        Stage.CALIFICAR: CALIFICAR_BASE,
        Stage.ANUNCIO: ANUNCIO_BASE,
        Stage.PEDIDO: PEDIDO_BASE,
        Stage.DATOS: DATOS_BASE,
        Stage.COBRO: COBRO_BASE,
        Stage.VERIFICACION: VERIFICACION_BASE,
        Stage.CERRADO: CERRADO_BASE,
        Stage.ASESORIA: ASESORIA_BASE,
        Stage.DERIVADO: DERIVADO_BASE,
    }
)

# ------------------------------------------------------------ follow-up texts


@dataclass(frozen=True)
class FollowUpText:
    """What a follow-up says in a stage (`plantillas.md` section 6)."""

    primer_intento: str
    ultimo_intento: str


_PROMO = "si hay promoción para seguimiento, menciónala con su vigencia."

_BASE_FOLLOW_UPS: Mapping[Stage, FollowUpText] = MappingProxyType(
    {
        Stage.IDENTIFICAR: FollowUpText(
            "Retoma lo último que dijo con una pregunta concreta que avance; no repitas el saludo.",
            f"Cierra con respeto: resume en una línea lo que le ofreciste y deja un siguiente paso fácil; {_PROMO}",
        ),
        Stage.PEDIDO: FollowUpText(
            "Recuérdale lo que lleva y lo que falta para completar.",
            "Recuérdale lo que lleva y lo que falta para completar, más la promoción para "
            "seguimiento si existe.",
        ),
        Stage.DATOS: FollowUpText(
            "Recuérdale que solo faltan sus datos para continuar.",
            "Recuérdale que solo faltan sus datos para continuar, en tono breve.",
        ),
        Stage.COBRO: FollowUpText(
            "Recuérdale el total y que puede enviar el comprobante aquí.",
            "Recuérdale el total y que puede enviar el comprobante aquí, más la promoción para "
            "seguimiento si existe.",
        ),
        # Only reached while the payment is rejected.
        Stage.VERIFICACION: FollowUpText(
            "Recuérdale con amabilidad el detalle de su comprobante y cómo corregirlo.",
            "Recuérdale con amabilidad el detalle de su comprobante y cómo corregirlo, en tono "
            "breve.",
        ),
    }
)

# ----------------------------------------------------------- system messages

# Messages the system sends without going through the model (section 7). The
# keys are the ones `agent_configs.system_messages` stores and the API exposes
# (`contracts/api.md`, `mensajes_sistema`). When the bot resumes after a pause
# there is no message: it waits for the customer to write.
DEFAULT_SYSTEM_MESSAGES: Mapping[str, str] = MappingProxyType(
    {
        "pago_confirmado": (
            "¡{nombre_cliente}, tu pago fue confirmado! ✅ Pedido #{numero}. {siguiente_paso}"
        ),
        "pago_rechazado": (
            "{nombre_cliente}, revisamos tu comprobante y hay un detalle: {motivo}. "
            "¿Nos ayudas a corregirlo?"
        ),
        "pedido_rechazado": (
            "{nombre_cliente}, revisamos tu pedido #{numero} y hay un detalle: {motivo}. "
            "¿Lo ajustamos?"
        ),
        "pedido_anulado": (
            "{nombre_cliente}, tu pedido #{numero} fue anulado: {motivo}. Si ya realizaste "
            "el pago, nos comunicaremos contigo para la devolución."
        ),
        # Feature 016: the stage that tells the customer the work moved. Each business can edit them.
        "pedido_listo": (
            "{nombre_cliente}, tu pedido #{numero} ya está listo para que lo recojas. {siguiente_paso}"
        ),
        "pedido_en_camino": (
            "{nombre_cliente}, tu pedido #{numero} ya va en camino. {siguiente_paso}"
        ),
        # Giannier, Milestone B, decision 19. Each business can edit it.
        "derivacion": (
            "Listo 🙌 Ya le pasé a nuestro equipo lo que necesitas, para que no tengas que "
            "repetirlo. Una persona te escribe por aquí en breve."
        ),
    }
)

# ------------------------------------------------------------------ the types


class PrepaidOptions(ProcessOptions):
    """Options of a template whose orders are always paid up front.

    Narrows `modalidad_pago` so the JSON Schema offers a single choice and a
    tenant cannot save `contra_entrega` for a template that does not support it.
    """

    modalidad_pago: Literal["previo"] | None = None


@dataclass(frozen=True)
class StageSpec:
    """One stage of one template."""

    stage: Stage
    funnel: FunnelCode
    # The stage's own tools. The global ones (`GLOBAL_TOOLS`) are added on top.
    tools: tuple[str, ...]
    instrucciones: str
    # VERIFICACION only: the tools when the payment was rejected.
    tools_if_rejected: tuple[str, ...] | None = None
    # COBRO only: the funnel column once the payment methods were sent.
    funnel_with_methods_sent: FunnelCode | None = None
    seguimiento: FollowUpText | None = None

    def tools_for(self, *, rejected: bool = False) -> tuple[str, ...]:
        """The stage's own tools, honouring the rejected-payment variant."""
        if rejected and self.tools_if_rejected is not None:
            return self.tools_if_rejected
        return self.tools


@dataclass(frozen=True)
class EffectiveOptions:
    """A template's options once the tenant's choices are laid over the defaults."""

    calificacion: Calificacion
    modalidad_pago: Literal["previo", "contra_entrega"]
    datos_requeridos: tuple[str, ...]
    link_pago_tarjeta: str | None
    variables: Mapping[str, str]
    # The business turned on the capability to book appointments (feature 019).
    agendar: bool = False
    # ...and the one to capture and qualify leads (feature 019, phase E).
    captar: bool = False
    # A business that sells answers for its orders too (feature 019, phase G).
    postventa: bool = False

    @property
    def calificacion_activa(self) -> bool:
        return self.calificacion.activa

    @property
    def caminos(self) -> Sequence[Camino]:
        return self.calificacion.caminos

    def camino(self, clave: str | None) -> Camino | None:
        """The configured path with this key, or None."""
        return next((c for c in self.calificacion.caminos if c.clave == clave), None)

    def primer_camino_de_venta(self) -> Camino | None:
        """The first path that ends in a sale rather than in an advisor."""
        return next((c for c in self.calificacion.caminos if c.tipo == "venta"), None)


@dataclass(frozen=True)
class Template:
    """A sales process: stages, tools, funnel, options and default texts."""

    key: str
    version: int
    nombre: str
    descripcion: str
    stages: Mapping[Stage, StageSpec]
    # What a template with no tenant options does (section 3).
    defaults: ProcessOptions
    # The model that validates a tenant's options for this template. Its JSON
    # Schema is what the panel renders the options form from.
    options_model: type[ProcessOptions]
    # "carrito" (several products) or "paquete" (one package with selections).
    tipo_pedido: Literal["carrito", "paquete"]
    # Most lines an order may have (services: 1 to 3), or None for no limit.
    max_lineas: int | None = None

    def stage(self, stage: Stage) -> StageSpec:
        return self.stages[stage]

    @property
    def options_schema(self) -> dict[str, object]:
        """JSON Schema of the options this template accepts."""
        return self.options_model.model_json_schema()

    def validate_options(self, raw: Mapping[str, object]) -> ProcessOptions:
        """Parse a tenant's options for this template. Raises `ValidationError`."""
        return self.options_model.model_validate(raw)

    def resolve(self, options: ProcessOptions | None = None) -> EffectiveOptions:
        """Lay the tenant's options over this template's defaults.

        A section the tenant set replaces the default section whole: a half
        qualification (a question with the default paths, say) would be a
        process nobody configured.
        """
        chosen = options or ProcessOptions()
        defaults = self.defaults
        calificacion = chosen.calificacion or defaults.calificacion or Calificacion()
        modalidad = chosen.modalidad_pago or defaults.modalidad_pago or "previo"
        required = chosen.datos_requeridos
        if required is None:
            required = defaults.datos_requeridos or []
        link = chosen.link_pago_tarjeta or defaults.link_pago_tarjeta
        return EffectiveOptions(
            calificacion=calificacion,
            modalidad_pago=modalidad,
            datos_requeridos=tuple(required),
            link_pago_tarjeta=str(link) if link else None,
            variables=dict(chosen.variables),
        )


def build_stages(
    *,
    inicio_tools: tuple[str, ...],
    identificar: str,
    overrides: Mapping[Stage, str] | None = None,
) -> dict[Stage, StageSpec]:
    """Every stage of a template: the shared defaults plus what the template changes.

    `identificar` has no `[base]` text, so every template supplies its own.
    `overrides` replaces the `[base]` text of other stages (INICIO, PEDIDO,
    ASESORIA in the certification template).
    """
    texts = {**_BASE_INSTRUCTIONS, Stage.IDENTIFICAR: identificar, **(overrides or {})}
    specs: dict[Stage, StageSpec] = {}
    for stage in Stage:
        tools = inicio_tools if stage is Stage.INICIO else _TOOLS_BY_STAGE[stage]
        specs[stage] = StageSpec(
            stage=stage,
            funnel=_FUNNEL_BY_STAGE[stage],
            tools=tools,
            instrucciones=texts[stage],
            tools_if_rejected=(
                _VERIFICATION_TOOLS_IF_REJECTED if stage is Stage.VERIFICACION else None
            ),
            funnel_with_methods_sent=(
                FunnelCode.CUENTAS_ENVIADAS if stage is Stage.COBRO else None
            ),
            seguimiento=_BASE_FOLLOW_UPS.get(stage),
        )
    return specs
