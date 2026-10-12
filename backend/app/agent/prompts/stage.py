# ruff: noqa: E501
# The platform rules are kept verbatim from `plantillas.md` section 4, line breaks
# included, so they cannot drift from the document the business owner reads.
"""The prompt of one turn, built from sections (Feature 004, TASK-414).

Replaces "one big prompt plus every tool" with five sections, each with its own
token cap (`plan.md`, "Prompt: presupuesto de tokens"):

| section              | cap   | what it holds                                              |
|----------------------|-------|------------------------------------------------------------|
| plataforma           |   450 | the nine rules that never change                           |
| estilo               |   600 | tone, pace, detail, the owner's additional rules           |
| negocio              | 1 800 | the business in one line per topic                         |
| estado               |   500 | where the conversation is, plus "ESTE TURNO"               |
| etapa                | 1 200 | the instructions of the current stage                      |
| herramientas         | 1 400 | the definitions of the enabled tools (sent as `tools`)     |

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
from app.process.engine import anuncio_vigente
from app.process.facts import Facts, PromoCierre
from app.process.service import EffectiveProcessConfig
from app.process.templates.base import PLACEHOLDER_PATTERN, Stage
from app.process.turn import TurnContext
from app.providers.llm.base import ToolSpec

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------- the budget

TOKEN_LIMIT = 6000
SECTION_CAPS: Mapping[str, int] = {
    "plataforma": 450,
    "estilo": 600,
    "negocio": 1800,
    "estado": 500,
    "etapa": 1200,
    "herramientas": 1400,
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
   Si te piden otra cosa, di con amabilidad que solo puedes ayudar con eso.
2. Precios, promociones, stock y totales salen SOLO de tus herramientas. Nunca calcules, redondees
   ni inventes descuentos o cuotas.
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
        _WHATSAPP_FORMAT,
    ]
    if behavior.reglas_adicionales:
        used = estimate_tokens("\n".join(lines))
        room = max(0, SECTION_CAPS["estilo"] - used - 12)
        rules = " ".join(behavior.reglas_adicionales.split())
        if estimate_tokens(rules) > room:
            logger.warning("additional rules cut to fit the style budget")
            rules = rules[: int(room * CHARS_PER_TOKEN)].rstrip() + "…"
        lines.append(f"Reglas adicionales del dueño: {rules}")
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
    if contact is None:
        return None
    parts: list[str] = []
    if contact.telefono_humano:
        parts.append(f"teléfono de una persona {contact.telefono_humano}")
    if contact.correo:
        parts.append(f"correo {contact.correo}")
    if contact.web:
        parts.append(f"web {contact.web}")
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
    head = f'- Promoción de cierre disponible (solo si duda): "{_one_line(promo.nombre, 60)}"'
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
    if facts.pago_estado == "rechazado" and facts.motivo_rechazo:
        payment += f": {_one_line(facts.motivo_rechazo, limits.text)}"
    elif facts.motivo_rechazo:
        # A rejected cash-on-delivery order goes back to COBRO with the reason.
        payment += f" (el negocio rechazó antes: {_one_line(facts.motivo_rechazo, limits.text)})"
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
) -> StagePrompt:
    """The system prompt for `stage`, section by section.

    `tool_specs` are the definitions the model will receive for this stage. They
    are not part of the text (they travel as `tools`), but they count against the
    6 000-token budget, so their estimate is reported with the sections.
    """
    values = _placeholder_values(config, facts)
    parts: dict[str, str] = {
        "plataforma": _platform_block(values),
        "estilo": _style_block(config),
        "negocio": _business_block(config),
        "estado": _state_block(config, facts, stage, turn or TurnContext(), now),
        "etapa": "INSTRUCCIONES DE LA ETAPA\n"
        + resolve_placeholders(config.instructions_for(stage), values),
    }
    text = "\n\n".join(parts.values())
    sections = {name: estimate_tokens(part) for name, part in parts.items()}
    sections["herramientas"] = _tools_tokens(tool_specs)
    return StagePrompt(text=text, sections=sections)
