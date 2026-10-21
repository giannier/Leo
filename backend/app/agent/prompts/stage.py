# ruff: noqa: E501
# The platform rules are kept verbatim from `plantillas.md` section 4, line breaks
# included, so they cannot drift from the document the business owner reads.
"""The prompt of one turn, built from sections (Feature 004, TASK-414).

Replaces "one big prompt plus every tool" with sections, each with its own token
cap (`plan.md`, "Prompt: presupuesto de tokens"; rebalanced by the 017, TASK-464,
so the caps still add up to the 6 000-token limit):

| section              | cap   | what it holds                                              |
|----------------------|-------|------------------------------------------------------------|
| plataforma           |   450 | the nine rules that never change                           |
| estilo               |   300 | tone, pace, detail, tú or usted, emojis, greeting          |
| estado               |   500 | where the conversation is, plus "ESTE TURNO"               |
| etapa (PROCESO)      |   700 | the active rules of the current stage                      |
| rubro                |   350 | the active rules of the rubro                              |
| negocio              | 1 300 | the business in one line per topic                         |
| situaciones          | 1 000 | the business's situations for this message + priority     |
| herramientas         | 1 400 | the definitions of the enabled tools (sent as `tools`)     |

The layers go from the least to the most specific (017, C-11): process, rubro,
business, situations, and the prompt says that, if they clash, the later one
wins. The platform rules above them never yield.

Everything is built from values already loaded (`EffectiveProcessConfig`,
`Facts`, `TurnContext`), so the function is pure and the prompt of any stage can
be snapshotted. The FAQ is not in the prompt (it goes through
`consultar_conocimiento`) and neither is the catalogue (`buscar_productos`).

Placeholders in the stage text (`{nombre_cliente}`, `{var.temas}`...) are filled
here; when a value is missing the **whole line** is dropped, so the model never
sees `{...}` or a sentence with a hole in it.

The turn pipeline (TASK-421) builds every prompt here. The legacy prompt of
`agent/prompts/builder.py` is only what the evals and the v0 agent tests still run.
"""

from __future__ import annotations

import json
import logging
import math
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from app.agent.prompts.builder import (
    _TONE_GUIDANCE,
    DEFAULT_TONE,
    _format_hours,
    _format_today,
)
from app.agent.situations import SelectedSituation
from app.models.business import MAX_SALUDO
from app.process.engine import anuncio_vigente
from app.process.facts import Facts, PromoCierre
from app.process.service import EffectiveProcessConfig
from app.process.templates.base import PLACEHOLDER_PATTERN, Stage
from app.process.turn import TurnContext
from app.providers.llm.base import ToolSpec
from app.services.website import normalize_website

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------- the budget

TOKEN_LIMIT = 6000
SECTION_CAPS: Mapping[str, int] = {
    "plataforma": 450,
    "venta": 540,
    "agenda": 200,
    "captacion": 220,
    "estilo": 300,
    "estado": 500,
    "etapa": 700,
    "rubro": 350,
    "negocio": 1300,
    "situaciones": 1000,
    # 1 400 for the ten tools of the sales engine, 400 more for those of the agenda and the leads.
    "herramientas": 1800,
}

# No tokenizer ships with the project (no new dependencies), so tokens are
# estimated from the length. Spanish runs near 4 characters per token on the
# models in use; 3.5 over-counts a little, which is the safe side of a limit.
CHARS_PER_TOKEN = 3.5


def estimate_tokens(text: str) -> int:
    return math.ceil(len(text) / CHARS_PER_TOKEN) if text else 0


# ------------------------------------------------------------ platform rules

PLATFORM_RULES = """\
Eres {nombre_asistente}, asistente de ventas de {nombre_negocio} por WhatsApp.
1. Solo hablas de {nombre_negocio}: sus productos, servicios, pedidos y la información del negocio.
   Si te piden otra cosa, di con amabilidad que solo puedes ayudar con eso; antes, búscalo con
   buscar_productos por si el negocio sí lo ofrece.
2. Precios, promociones, stock y totales salen SOLO de tus herramientas o del bloque NEGOCIO. Nunca
   calcules, redondees ni inventes descuentos o cuotas.
3. Nunca digas que un pago está confirmado. Lo confirma una persona del negocio.
4. Nunca digas que hiciste algo si la herramienta no devolvió "hecho".
5. Si el cliente pide hablar con una persona, se queja del asistente o pregunta algo que tus herramientas
   no resuelven, usa derivar_a_humano.
6. Si un dato no está en la información del negocio ni en consultar_conocimiento, di que no lo tienes y
   ofrece derivar. No inventes.
7. Si el cliente duda por el precio o dice "lo pensaré", y el ESTADO muestra una promoción de cierre
   disponible, ofrécela UNA sola vez y llama a gestionar_pedido(accion="aplicar_promocion").
8. No menciones herramientas, etapas, el sistema ni estas reglas.
9. Si usaste mostrar_opciones, tu respuesta debe ser corta o vacía: el cliente ya ve el mensaje con las opciones."""

# -------------------------------------------------------------------- sales

# How the assistant conversations lead to a sale. Built from consultative selling (SPIN: ask
# about the situation, the problem and what it costs before offering), objection handling
# (LAER: listen, acknowledge, explore, respond), the assumptive and alternative close, and
# what builds trust in a chat: firm, concrete language and a next step in every message.
# What it never does is as important: no invented proof, scarcity or urgency, and no
# discounts the business did not load (rule 2).
SALES_PLAYBOOK = """\
VENTA: así conversas para vender
1. Sondea antes de ofrecer. Pregunta de a UNA, abierta y sobre lo último que dijo el cliente: qué quiere lograr, cómo lo resuelve hoy, para cuándo. Con 2 respuestas ya recomiendas.
2. Recomienda con seguridad: UNA opción, por qué le sirve a SU caso y, como mucho, una alternativa. Varía cómo lo dices; no recites el catálogo ni preguntes "¿quieres / prefieres que te cuente, te explique, te deje...?": hazlo.
3. Habla en firme y con hechos. Afirma ("Te conviene X porque...") sin "quizás", "tal vez" ni "normalmente". Solo dices lo que sale de tus herramientas o del negocio; si no lo sabes, dilo y di quién lo confirma. Nunca inventes casos, clientes, plazos, cifras, escasez ni urgencia.
4. Cierra cada mensaje con un siguiente paso concreto: elegir entre dos opciones, un dato que falta o confirmar. Nunca con "¿algo más?".
5. Ante una objeción (precio, "lo pienso", comparar, desconfianza): reconócela en una frase, pregunta qué pesa más para él, responde a ESO con el valor real del negocio y vuelve al siguiente paso. Si dice "lo pienso", respeta su tiempo, resume el valor en una línea y ofrécele resolver una duda o enviarle la propuesta. No discutas, no rebajes, no menciones descuentos ni digas que no hay promoción (solo ofrece la que el ESTADO traiga) y no derives por una objeción de precio.
6. Señal de compra (pregunta precio, plazo o cómo pagar; dice "me interesa"): resume en una línea lo que recibirá y propón cerrar ya ("Te lo dejo listo: ¿pagas por Yape o transferencia?"). Si ya eligió, no reabras la decisión ni ofrezcas lo que no esté en tus herramientas: avanza.
7. Cercanía: usa su nombre si lo dio, retoma sus palabras, mensajes cortos. Si hay interés real, pide su nombre y cómo contactarlo, diciendo para qué."""

SERVICE_ONLY_PLAYBOOK = """\
VENTA: tu prioridad es resolver dudas con claridad y calidez. No presiones ni ofrezcas lo que el cliente no pidió; si muestra ganas de comprar, ayúdalo a concretar."""

# The stages where the sale is still being led; once the order is paid or paused for a
# person there is nothing left to sell, and the section would only cost tokens.
_SELLING_STAGES = frozenset(
    {
        Stage.INICIO,
        Stage.CALIFICAR,
        Stage.ANUNCIO,
        Stage.IDENTIFICAR,
        Stage.PEDIDO,
        Stage.DATOS,
        Stage.COBRO,
        Stage.ASESORIA,
    }
)


def _sales_block(config: EffectiveProcessConfig, stage: Stage) -> str:
    if stage not in _SELLING_STAGES:
        return ""
    if config.behavior.objetivo == "atender":
        return SERVICE_ONLY_PLAYBOOK
    return SALES_PLAYBOOK


SCHEDULE_RULES = """\
AGENDA: un servicio que se agenda se agenda: no lo pidas con gestionar_pedido ni lo derives por eso.
Usa consultar_horarios y ofrece como máximo 3 horarios; nunca inventes uno. Cuando el cliente elija
uno que ya ofreciste y tengas su nombre, llama a agendar_cita con ese slot (AAAA-MM-DDTHH:MM, hora
local) sin volver a consultar ni pedir otra confirmación; una cita agendada ya es el cierre: confírmala
y NO derives. Si piden cambiar o cancelar, usa gestionar_cita. Si responden "sí" a un recordatorio,
confirma la cita."""


LEAD_RULES = """\
CAPTACIÓN: ve conociendo al cliente sin interrogarlo. Entre tus respuestas haz UNA pregunta a la vez,
solo las que falten y no repitas lo que ya dijo: {preguntas}.
Con 2 respuestas, o apenas muestre interés claro, llama a calificar_cliente (nota interna: no la
menciones). Interés ALTO: necesidad concreta y plazo corto, o pide precio, cita o propuesta. MEDIO:
interés con plazo o presupuesto sin definir. BAJO: solo curiosidad. No pidas tarjeta ni documentos."""


def _lead_block(config: EffectiveProcessConfig) -> str:
    if not config.options.captar or not config.preguntas_calificacion:
        return ""
    return LEAD_RULES.format(preguntas="; ".join(config.preguntas_calificacion))


def _schedule_block(config: EffectiveProcessConfig) -> str:
    return SCHEDULE_RULES if config.options.agendar else ""


# ------------------------------------------------------------------- style

_PACE_TEXT: Mapping[str, str] = {
    "directo": "Ve al grano. Si el cliente ya dio lo que necesitas, avanza sin preguntar de nuevo.",
    "equilibrado": "Una pregunta por mensaje. Confirma lo importante antes de avanzar.",
    "paso_a_paso": (
        "Explica cada paso antes de pedir algo. Pregunta una cosa a la vez y verifica que entendió."
    ),
}
_DETAIL_TEXT: Mapping[str, str] = {
    "breve": "Responde en 1 a 3 líneas (máximo 350 caracteres).",
    "moderado": "Responde en máximo 700 caracteres.",
    "extenso": "Puedes extenderte hasta 1 400 caracteres cuando el cliente pide detalles.",
}
_TREATMENT_TEXT: Mapping[str, str] = {
    "tu": "Trata al cliente de tú.",
    "usted": (
        "Trata al cliente de usted en TODA la respuesta (usted, le, su, quiere, tiene), "
        "nunca de tú, aunque los ejemplos de abajo estén en tú."
    ),
}
_EMOJI_TEXT: Mapping[str, str] = {
    "no": "No uses emojis.",
    "pocos": "Usa como máximo un emoji por mensaje, y no en todos.",
    "si": "Puedes usar emojis con naturalidad.",
}
_DISCOUNT_TEXT: Mapping[str, str] = {
    "no_ofrece": "Descuentos: el negocio no da descuentos fuera de sus promociones; no los ofrezcas.",
    "solo_promociones": "Descuentos: solo las promociones del sistema.",
    "negociable": (
        "Descuentos: si el cliente pide un descuento que no está en las promociones, no lo concedas "
        "tú: usa derivar_a_humano para que una persona del negocio lo evalúe."
    ),
}
_PRIORITY = (
    "PRIORIDAD: si dos instrucciones chocan, manda la situación del negocio, luego el NEGOCIO, luego "
    "el RUBRO, luego el PROCESO. Las reglas 1 a 9 del inicio nunca se rompen."
)
_SITUATION_FIELD_CHARS = 400
_WHATSAPP_FORMAT = (
    "Formato de WhatsApp, no Markdown: *negrita* con un asterisco, _cursiva_ con guion bajo. "
    "Sin ## ni tablas."
)

# ----------------------------------------------------------------- business

_PAYMENT_LABELS: Mapping[str, str] = {
    "yape": "Yape",
    "plin": "Plin",
    "transferencia": "transferencia bancaria",
    "tarjeta": "tarjeta",
    "efectivo": "efectivo",
    "link": "link de pago",
}
_CURRENCY_SYMBOLS: Mapping[str, str] = {"PEN": "S/", "USD": "US$"}
_LINE_CAP_CHARS = 500
_DESCRIPTION_CAP_CHARS = 300
_MAX_PROMOTION_LINES = 8
_NETWORKS_SEPARATOR = ", "

# ------------------------------------------------------------------- state

# Closing promotions are only offered where the customer may be weighing the
# price (`plantillas.md` section 4).
_PROMO_STAGES = frozenset({Stage.IDENTIFICAR, Stage.PEDIDO, Stage.DATOS, Stage.COBRO})
_MAX_STATE_TEXT = 200


@dataclass(frozen=True)
class _StateLimits:
    """How much of each growing list the state block shows."""

    order_items: int = 8
    selections: int = 10
    listed_keys: int = 20
    text: int = _MAX_STATE_TEXT


# From the full view to the tightest one: the state block uses the first that
# fits its cap, so a conversation with a long order never pushes the prompt over.
_STATE_LIMITS = (
    _StateLimits(),
    _StateLimits(order_items=4, selections=6, listed_keys=10, text=120),
    _StateLimits(order_items=2, selections=3, listed_keys=5, text=80),
)

_PAYMENT_STATE_TEXT: Mapping[str, str] = {
    "por_verificar": "por verificar (en revisión del negocio)",
    "rechazado": "rechazado",
    "confirmado": "confirmado",
}


# --------------------------------------------------------------- the result


@dataclass(frozen=True)
class StagePrompt:
    """The prompt of a turn and what each section costs."""

    text: str
    # Estimated tokens by section; "herramientas" is sent apart as `tools`.
    sections: Mapping[str, int]

    @property
    def tokens(self) -> int:
        return sum(self.sections.values())

    @property
    def over_budget(self) -> list[str]:
        """The sections above their cap, and `total` if the whole is above the limit."""
        over = [name for name, used in self.sections.items() if used > SECTION_CAPS[name]]
        if self.tokens > TOKEN_LIMIT:
            over.append("total")
        return over


# ------------------------------------------------------------ small helpers


def _one_line(text: str, limit: int = _MAX_STATE_TEXT) -> str:
    return " ".join(text.split())[:limit]


def _symbol(currency: str) -> str:
    return _CURRENCY_SYMBOLS.get(currency.upper(), currency.upper())


def _money(amount: Decimal, currency: str) -> str:
    return f"{_symbol(currency)} {amount:.2f}"


def _zone(timezone: str) -> ZoneInfo:
    try:
        return ZoneInfo(timezone)
    except (ZoneInfoNotFoundError, ValueError):
        return ZoneInfo("America/Lima")


def _join_capped(items: Sequence[str], separator: str, cap_chars: int = _LINE_CAP_CHARS) -> str:
    """`items` joined, stopping before `cap_chars` and saying how many were left out."""
    kept: list[str] = []
    used = 0
    for index, item in enumerate(items):
        extra = len(item) + (len(separator) if kept else 0)
        if kept and used + extra > cap_chars:
            return separator.join(kept) + f" (y {len(items) - index} más)"
        kept.append(item)
        used += extra
    return separator.join(kept)


def resolve_placeholders(text: str, values: Mapping[str, str | None]) -> str:
    """Fill `{name}` placeholders; drop every line that mentions a missing one.

    A single pass over each line, so a value that itself contains `{x}` is left
    as written, not expanded again.
    """
    kept: list[str] = []
    for line in text.split("\n"):
        names = PLACEHOLDER_PATTERN.findall(line)
        if any(not values.get(name) for name in names):
            continue
        kept.append(PLACEHOLDER_PATTERN.sub(lambda match: str(values[match.group(1)]), line))
    return re.sub(r"\n{3,}", "\n\n", "\n".join(kept)).strip()


# ------------------------------------------------------------------ sections


def _platform_block(values: Mapping[str, str | None]) -> str:
    return resolve_placeholders(PLATFORM_RULES, values)


def _style_block(config: EffectiveProcessConfig) -> str:
    behavior = config.behavior
    lines = [
        "ESTILO",
        _TONE_GUIDANCE.get(behavior.tone, _TONE_GUIDANCE[DEFAULT_TONE]),
        _PACE_TEXT.get(behavior.ritmo, _PACE_TEXT["equilibrado"]),
        _DETAIL_TEXT.get(behavior.detalle, _DETAIL_TEXT["moderado"]),
        _TREATMENT_TEXT.get(behavior.tratamiento, _TREATMENT_TEXT["tu"]),
        _EMOJI_TEXT.get(behavior.emojis, _EMOJI_TEXT["pocos"]),
        _WHATSAPP_FORMAT,
    ]
    if behavior.saludo:
        lines.append(
            f"Saludo del negocio, para tu primer mensaje: «{_one_line(behavior.saludo, MAX_SALUDO)}»"
        )
    return "\n".join(lines)


def _rubro_block(config: EffectiveProcessConfig) -> str | None:
    if not config.rubro_rules:
        return None
    lines = ["RUBRO"]
    for rule in config.rubro_rules:
        candidate = [*lines, f"- {_one_line(rule, 300)}"]
        if estimate_tokens("\n".join(candidate)) > SECTION_CAPS["rubro"]:
            logger.warning("rubro rules cut to fit the prompt budget")
            break
        lines = candidate
    return "\n".join(lines)


def _clip(text: str, chars: int) -> str:
    """One line, cut with "…" so the model knows there was more."""
    flat = " ".join(text.split())
    return flat if len(flat) <= chars else flat[: chars - 1].rstrip() + "…"


def _situation_text(situation: SelectedSituation, chars: int) -> str:
    parts = [
        f"- «{_one_line(situation.titulo, 80)}». Cuándo: {_clip(situation.cuando_aplica, chars)}",
        f"  Qué haces: {_clip(situation.que_hace, chars)}",
    ]
    if situation.claves:
        parts.append(f"  Envía con enviar_recurso: {', '.join(situation.claves)}")
    if situation.derivar:
        parts.append("  Después, usa derivar_a_humano con un resumen.")
    if situation.ejemplo_respuesta:
        parts.append(f"  Ejemplo de respuesta: «{_clip(situation.ejemplo_respuesta, chars)}»")
    return "\n".join(parts)


def _situations_block(situations: Sequence[SelectedSituation]) -> str:
    """The situations of this message, each cut to its share of the budget, then the priority."""
    if not situations:
        return _PRIORITY
    # Measured with the platform model (TASK-469): with a softer heading an
    # unconditional "siempre" was dropped whenever the customer also asked something.
    head = (
        "SITUACIONES DEL NEGOCIO: aplican a este mensaje. Cúmplelas TODAS en tu respuesta, "
        "además de responder lo que preguntó."
    )
    # The "siempre" ones are repeated in one closing line: without it the model
    # dropped them in 2 of 12 turns, with it in none (TASK-469 measurement).
    always = [s for s in situations if s.siempre]
    reminder = (
        "Antes de enviar, revisa que cumpliste: "
        + "; ".join(_clip(s.que_hace, 160) for s in always)
        if always
        else ""
    )
    fixed = estimate_tokens(head) + estimate_tokens(_PRIORITY) + estimate_tokens(reminder) + 6
    share = max(40, (SECTION_CAPS["situaciones"] - fixed) // len(situations))
    lines = [head]
    for situation in situations:
        chars = _SITUATION_FIELD_CHARS
        text = _situation_text(situation, chars)
        while estimate_tokens(text) > share and chars > 60:
            chars -= 40
            text = _situation_text(situation, chars)
        if chars < _SITUATION_FIELD_CHARS:
            logger.warning("a situation was cut to fit the prompt budget")
        lines.append(text)
    if reminder:
        lines.append(reminder)
    lines.append(_PRIORITY)
    return "\n".join(lines)


def _locales_line(config: EffectiveProcessConfig) -> str | None:
    locales = config.info.locales
    if locales is None:
        return None
    if not locales.atiende_presencial:
        return "Locales: sin atención presencial (solo en línea)."
    items: list[str] = []
    for local in locales.locales:
        parts = [f"{local.nombre}: {local.direccion}"]
        if local.referencia:
            parts.append(f"({local.referencia})")
        hours = _format_hours(local.horarios)
        if hours:
            parts.append(f"· {hours}")
        items.append(" ".join(parts))
    return f"Locales: {_join_capped(items, ' | ')}" if items else None


def _entregas_line(config: EffectiveProcessConfig) -> str | None:
    entregas = config.info.entregas
    if entregas is None:
        return None
    currency = config.currency
    parts: list[str] = []
    if entregas.hace_delivery:
        if entregas.zonas:
            zones = [
                f"{zona.nombre} {_money(zona.costo, currency)} ({zona.tiempo})"
                for zona in entregas.zonas
            ]
            parts.append(f"delivery, zonas: {_join_capped(zones, '; ')}")
        else:
            parts.append("delivery (costo a coordinar)")
    if entregas.recojo_en_tienda:
        parts.append("recojo en tienda")
    if entregas.envio_gratis_desde is not None:
        parts.append(f"envío gratis desde {_money(entregas.envio_gratis_desde, currency)}")
    if not parts:
        return "Entregas: el negocio no hace entregas."
    notes = f" {_one_line(entregas.notas)}" if entregas.notas else ""
    return f"Entregas: {'; '.join(parts)}.{notes}"


def _returns_line(config: EffectiveProcessConfig) -> str | None:
    returns = config.info.devoluciones
    if returns is None:
        return None
    parts = [
        f"cambios: {'sí' if returns.acepta_cambios else 'no'}",
        f"devoluciones: {'sí' if returns.acepta_devoluciones else 'no'}",
    ]
    if returns.plazo_dias:
        parts.append(f"plazo {returns.plazo_dias} días")
    if returns.condiciones:
        parts.append(_one_line(returns.condiciones))
    return f"Cambios y devoluciones: {'; '.join(parts).rstrip('.')}."


def _contact_line(config: EffectiveProcessConfig) -> str | None:
    contact = config.info.contacto
    parts: list[str] = []
    if contact is not None and contact.telefono_humano:
        parts.append(f"teléfono de una persona {contact.telefono_humano}")
    if contact is not None and contact.correo:
        parts.append(f"correo {contact.correo}")
    # The contact section's web, or the one written once in the business profile.
    web = (contact.web if contact is not None else None) or normalize_website(config.website)
    if web:
        parts.append(f"web {web}")
    if contact is not None:
        for network, url in contact.redes.items():
            parts.append(f"{network} {url}")
    return f"Contacto: {_join_capped(parts, '; ')}." if parts else None


def _customer_types_line(config: EffectiveProcessConfig) -> str | None:
    types = config.info.tipos_cliente
    if types is None or not types.tipos:
        return None
    items = [f"{t.nombre} ({t.como_reconocerlo}): {t.trato}" for t in types.tipos]
    return f"Tipos de cliente: {_join_capped(items, ' | ')}"


def _payment_line(config: EffectiveProcessConfig) -> str | None:
    if not config.payment_methods:
        return None
    labels = [_PAYMENT_LABELS.get(method, method) for method in config.payment_methods]
    when = (
        "se paga al recibir el pedido"
        if config.options.modalidad_pago == "contra_entrega"
        else "se paga antes de entregar"
    )
    return f"Métodos de pago: {', '.join(labels)}; {when}."


def _promotion_lines(config: EffectiveProcessConfig) -> list[str]:
    """Automatic promotions in force. Closing ones are offered from the state block."""
    lines: list[str] = []
    automatic = [p for p in config.promotions if p.uso == "automatica"]
    for promotion in automatic[:_MAX_PROMOTION_LINES]:
        text = f"Promoción vigente: {promotion.nombre}"
        if promotion.descripcion:
            text += f" — {_one_line(promotion.descripcion, 120)}"
        lines.append(text)
    if len(automatic) > _MAX_PROMOTION_LINES:
        lines.append(f"(y {len(automatic) - _MAX_PROMOTION_LINES} promociones más)")
    return lines


def _identity_line(config: EffectiveProcessConfig) -> str:
    name = config.business_name or "el negocio"
    line = f"Negocio: {name}"
    if config.industry:
        line += f" (rubro {config.industry})"
    line += "."
    if config.business_description:
        line += f" {_one_line(config.business_description, _DESCRIPTION_CAP_CHARS)}"
    return line


def _business_block(config: EffectiveProcessConfig) -> str:
    """The business in one line per topic, most useful first, within its cap."""
    candidates: list[str | None] = [
        _identity_line(config),
        _payment_line(config),
        _entregas_line(config),
        _locales_line(config),
        _returns_line(config),
        _contact_line(config),
        *_promotion_lines(config),
        _customer_types_line(config),
    ]
    hours = _format_hours(dict(config.opening_hours)) if config.opening_hours else None
    if hours:
        candidates.insert(4, f"Horario de atención ({config.timezone}): {hours}.")

    candidates.insert(2, _DISCOUNT_TEXT.get(config.behavior.politica_descuentos))
    lines = ["NEGOCIO", *(line for line in candidates if line)]
    # Drop from the end until it fits: the least useful topics come last.
    while len(lines) > 2 and estimate_tokens("\n".join(lines)) > SECTION_CAPS["negocio"]:
        logger.warning("business information cut to fit the prompt budget")
        lines.pop()
    return "\n".join(lines)


def _stored_name(facts: Facts) -> str:
    if not facts.nombre_cliente:
        return "nombre desconocido"
    note = "nombre confirmado" if facts.nombre_confirmado else "nombre de perfil, sin confirmar"
    return f"{_one_line(facts.nombre_cliente, 60)} ({note})"


def _order_line(facts: Facts, limits: _StateLimits) -> str:
    order = facts.pedido
    if order is None:
        return "- Pedido: ninguno"
    items: list[str] = []
    for item in order.items[: limits.order_items]:
        text = f"{item.cantidad}× {_one_line(item.nombre, 60)}"
        if item.selecciones:
            text += (
                f" [{', '.join(_one_line(s, 40) for s in item.selecciones[: limits.selections])}]"
            )
        items.append(text)
    if len(order.items) > limits.order_items:
        items.append(f"y {len(order.items) - limits.order_items} más")
    number = f"#{order.numero} " if order.numero is not None else ""
    text = f"- Pedido {number}({order.estado}): {'; '.join(items) or 'sin ítems'}"
    text += f" · Total {_money(order.total, order.moneda)}"
    if order.faltan_selecciones:
        text += f" · faltan {order.faltan_selecciones} selecciones"
    return text


def _promo_state_line(promo: PromoCierre, now: datetime, zone: ZoneInfo) -> str:
    head = (
        f'- Promoción de cierre disponible (solo si duda): "{_one_line(promo.nombre, 60)}" '
        f"(promocion_id={promo.id})"
    )
    if promo.ofrecida_vence is not None and promo.ofrecida_vence > now:
        until = promo.ofrecida_vence.astimezone(zone)
        return f"{head} · ofrecida, vence {until:%d/%m %H:%M}"
    return f"{head} · vale {promo.vigencia_horas} h desde que se ofrece · no ofrecida"


def _state_block(
    config: EffectiveProcessConfig,
    facts: Facts,
    stage: Stage,
    turn: TurnContext,
    now: datetime,
) -> str:
    """The state block at the fullest view that fits its cap."""
    block = ""
    for limits in _STATE_LIMITS:
        block = _state_lines(config, facts, stage, turn, now, limits)
        if estimate_tokens(block) <= SECTION_CAPS["estado"]:
            break
    return block


def _state_lines(
    config: EffectiveProcessConfig,
    facts: Facts,
    stage: Stage,
    turn: TurnContext,
    now: datetime,
    limits: _StateLimits,
) -> str:
    zone = _zone(config.timezone)
    lines = [
        "ESTADO DE LA CONVERSACIÓN",
        f"- Hoy: {_format_today(now, config.timezone)}",
        f"- Cliente: {_stored_name(facts)}",
    ]

    entrada = facts.entrada_anuncio
    if entrada is not None and anuncio_vigente(facts, now):
        shown = entrada.headline or entrada.body or entrada.source_url
        if shown:
            lines.append(f'- Llegó por anuncio: "{_one_line(shown, 150)}"')

    if facts.camino:
        path = config.options.camino(facts.camino)
        label = f" ({_one_line(path.titulo, 40)})" if path else ""
        lines.append(f"- Camino: {facts.camino}{label}")

    if facts.paquete_pedido and facts.pedido is None:
        lines.append(
            f'- Paquete que ya pidió: "{_one_line(facts.paquete_pedido, 80)}" (cuando diga sus '
            "temas, agrégalo con ellos con gestionar_pedido; no le muestres los paquetes otra vez)"
        )

    lines.append(_order_line(facts, limits))

    if facts.datos_faltantes:
        lines.append(f"- Datos del cliente: faltan {', '.join(facts.datos_faltantes)}")
    else:
        lines.append("- Datos del cliente: completos")
    lines.append(
        f"- Métodos de pago: {'enviados' if facts.metodos_pago_enviados else 'no enviados'}"
    )

    if facts.recursos_enviados:
        sent = ", ".join(facts.recursos_enviados[: limits.listed_keys])
        lines.append(f"- Recursos ya enviados (NO reenviar): {sent}")
    if facts.opciones_mostradas:
        shown_options = ", ".join(facts.opciones_mostradas[: limits.listed_keys])
        lines.append(f"- Opciones ya mostradas: {shown_options}")

    if stage in _PROMO_STAGES:
        lines.extend(_promo_state_line(promo, now, zone) for promo in facts.promos_cierre)

    if facts.asesoria_derivada_en is not None:
        lines.append("- Asesoría: asesoría ya derivada a una persona del negocio")

    payment = _PAYMENT_STATE_TEXT.get(facts.pago_estado or "", "—")
    if stage is Stage.COBRO and facts.modalidad_pago == "contra_entrega" and not facts.pago_estado:
        # Both branches of COBRO are in the prompt; the model offered accounts to a
        # cash-on-delivery customer (Milestone B run).
        payment = (
            "contra entrega: resume el pedido y pídele que lo confirme; no hay métodos de pago "
            "que enviar"
        )
    if facts.pago_estado == "rechazado" and facts.motivo_rechazo:
        payment += f": {_one_line(facts.motivo_rechazo, limits.text)}"
    elif facts.motivo_rechazo:
        # A rejected cash-on-delivery order goes back to COBRO with the reason.
        payment += f" (el negocio rechazó antes: {_one_line(facts.motivo_rechazo, limits.text)})"
    if facts.comprobante_en_espera:
        payment = (
            "comprobante ya recibido; pasa a revisión del negocio cuando el pedido y los "
            "datos estén completos (no lo pidas de nuevo ni reenvíes los métodos de pago)"
        )
    lines.append(f"- Pago: {payment}")

    turn_lines = turn.lines()
    if turn_lines:
        lines.append("ESTE TURNO")
        lines.extend(f"- {line}" for line in turn_lines)
    lines.append(f"ETAPA ACTUAL: {stage.value}")
    return "\n".join(lines)


def _placeholder_values(config: EffectiveProcessConfig, facts: Facts) -> dict[str, str | None]:
    options = config.options
    contact = config.info.contacto
    paths = "; ".join(
        f"{c.clave} = {c.titulo}" + (f" ({c.descripcion})" if c.descripcion else "")
        for c in options.caminos
    )
    values: dict[str, str | None] = {
        "nombre_asistente": config.behavior.agent_name,
        "nombre_negocio": config.business_name or "el negocio",
        # Only a name the customer gave counts; the profile label is a guess.
        "nombre_cliente": facts.nombre_cliente if facts.nombre_confirmado else None,
        "telefono_humano": contact.telefono_humano if contact else None,
        "link_pago_tarjeta": options.link_pago_tarjeta,
        "pregunta_calificacion": options.calificacion.pregunta,
        "caminos": paths or None,
        "datos_requeridos": ", ".join(options.datos_requeridos) or None,
        "datos_faltantes": ", ".join(facts.datos_faltantes) or None,
    }
    values.update({f"var.{key}": value for key, value in options.variables.items()})
    return values


def _tools_tokens(tool_specs: Sequence[ToolSpec]) -> int:
    payload = [
        {"name": t.name, "description": t.description, "parameters": t.parameters}
        for t in tool_specs
    ]
    return estimate_tokens(json.dumps(payload, ensure_ascii=False)) if payload else 0


# ------------------------------------------------------------------ the build


def build_stage_prompt(
    config: EffectiveProcessConfig,
    facts: Facts,
    stage: Stage,
    *,
    now: datetime,
    turn: TurnContext | None = None,
    tool_specs: Sequence[ToolSpec] = (),
    situations: Sequence[SelectedSituation] = (),
) -> StagePrompt:
    """The system prompt for `stage`, section by section.

    `tool_specs` are the definitions the model will receive for this stage. They
    are not part of the text (they travel as `tools`), but they count against the
    6 000-token budget, so their estimate is reported with the sections.
    """
    values = _placeholder_values(config, facts)
    parts: dict[str, str] = {"plataforma": _platform_block(values)}
    sales = _sales_block(config, stage)
    if sales:
        parts["venta"] = sales
    schedule = _schedule_block(config)
    if schedule:
        parts["agenda"] = schedule
    leads = _lead_block(config)
    if leads:
        parts["captacion"] = leads
    parts |= {
        "estilo": _style_block(config),
        "estado": _state_block(config, facts, stage, turn or TurnContext(), now),
        "etapa": "PROCESO: INSTRUCCIONES DE LA ETAPA\n"
        + resolve_placeholders(config.instructions_for(stage), values),
    }
    rubro = _rubro_block(config)
    if rubro:
        parts["rubro"] = rubro
    parts["negocio"] = _business_block(config)
    parts["situaciones"] = _situations_block(situations)
    text = "\n\n".join(parts.values())
    sections = {name: estimate_tokens(part) for name, part in parts.items()}
    sections["herramientas"] = _tools_tokens(tool_specs)
    return StagePrompt(text=text, sections=sections)
