"""Build the agent's system prompt from the tenant's own configuration.

"Configuración sobre código" (Constitution): a new industry must never need a
code change. Everything that varies between businesses comes out of the
database - identity, tone, objective, restrictions, escalation rules, opening
hours - and this file only decides how to phrase it.

The prompt is written in Spanish because the agent answers Peruvian customers
in Spanish, and instructions in one language with output expected in another
measurably degrades adherence. It is the one deliberate exception to the
project's English-artifacts rule, and it is customer-facing copy.

**The "no inventar" guardrail is the load-bearing part.** An agent that
invents a product, a price or a policy does more damage than one that says it
does not know: the customer acts on the answer, and the business finds out
when somebody arrives expecting a dish that does not exist. So the prompt
states the rule, names the tools as the only source of truth, and gives an
explicit escape hatch - escalate - because a model with no permitted way to
fail will invent one.

**And the prompt alone is not enough.** Rule 1 already said "nunca inventes
productos" the day the catalogue said "Leche" and the agent offered "Leche
pausterizada". So `app/agent/catalog_guard.py` checks the finished reply
against the tenant's real catalogue, and `build_name_correction` below is what
a model that slipped is handed before it gets its one retry.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from app.agent.catalog_guard import words
from app.models.business import AgentConfig, BusinessProfile

DEFAULT_AGENT_NAME = "Leo"
DEFAULT_TONE = "cercano"
DEFAULT_OBJECTIVE = "ambos"
DEFAULT_TIMEZONE = "America/Lima"

_TONE_GUIDANCE = {
    "profesional": "Trata de usted. Frases completas, sin jerga ni emojis.",
    "cercano": "Trata de tú. Cálido y natural, con emojis solo si el cliente los usa.",
    "juvenil": "Trata de tú. Relajado y breve, los emojis son bienvenidos.",
}

_OBJECTIVE_GUIDANCE = {
    "vender": (
        "Tu prioridad es ayudar a concretar la compra: confirma qué quiere, "
        "dile el precio exacto del catálogo y explícale cómo pagar."
    ),
    "atender": (
        "Tu prioridad es resolver dudas. No presiones para vender ni ofrezcas "
        "productos que el cliente no pidió."
    ),
    "ambos": (
        "Resuelve la duda primero. Si el cliente muestra interés en comprar, "
        "ayúdalo a concretar con el precio exacto del catálogo."
    ),
}

_DAY_NAMES = {
    "mon": "lunes",
    "tue": "martes",
    "wed": "miércoles",
    "thu": "jueves",
    "fri": "viernes",
    "sat": "sábado",
    "sun": "domingo",
}

_ESCALATION_PHRASES = {
    "cliente_molesto": "el cliente está molesto o frustrado",
    "pide_humano": "el cliente pide hablar con una persona",
    "fuera_de_catalogo": "te preguntan por algo que no está en el catálogo ni en tu información",
}

# `datetime.weekday()` order, Monday first.
_WEEKDAYS = ("lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo")

_MONTHS = (
    "enero",
    "febrero",
    "marzo",
    "abril",
    "mayo",
    "junio",
    "julio",
    "agosto",
    "septiembre",
    "octubre",
    "noviembre",
    "diciembre",
)


def _format_hours(opening_hours: dict[str, Any] | None) -> str | None:
    """Turn the stored `{"mon": [["09:00", "18:00"]]}` into a readable line.

    Malformed entries are skipped rather than raising: this data comes from a
    wizard form, and one bad row must not take the whole agent down - it would
    fail at answer time, in front of a customer.
    """
    if not opening_hours:
        return None
    parts: list[str] = []
    for key, ranges in opening_hours.items():
        day = _DAY_NAMES.get(str(key).lower()[:3])
        if day is None or not isinstance(ranges, list):
            continue
        spans = [
            f"{span[0]} a {span[1]}"
            for span in ranges
            if isinstance(span, list | tuple) and len(span) >= 2
        ]
        if spans:
            parts.append(f"{day}: {', '.join(spans)}")
    return "; ".join(parts) if parts else None


def _format_today(now: datetime, timezone: str) -> str:
    """Today's date, in the tenant's own timezone, spelled out in Spanish.

    Spelled out rather than ISO because the model has to reason about it in
    prose - "el 5" means nothing without knowing that today is the 4th, and a
    customer who asks for "mañana" is asking about a date the agent otherwise
    has no way to compute.

    An unknown timezone falls back rather than raising: the column is filled
    from a wizard dropdown, but a bad value must not take the agent down in
    front of a customer.
    """
    try:
        local = now.astimezone(ZoneInfo(timezone))
    except (ZoneInfoNotFoundError, ValueError):
        local = now.astimezone(ZoneInfo(DEFAULT_TIMEZONE))
    return (
        f"{_WEEKDAYS[local.weekday()]} {local.day} de "
        f"{_MONTHS[local.month - 1]} de {local.year}, {local:%H:%M}"
    )


def build_system_prompt(
    *,
    profile: BusinessProfile | None,
    config: AgentConfig | None,
    tool_names: tuple[str, ...] = (),
    now: datetime | None = None,
) -> str:
    """Assemble the instructions for one tenant's agent.

    Pure: given the same rows *and the same `now`* it returns the same string,
    which is what lets the evals in TASK-016 compare prompts across changes.
    The clock is injected rather than read here precisely to keep that true.
    """
    name = (config.agent_name if config else None) or DEFAULT_AGENT_NAME
    tone = (config.tone if config else None) or DEFAULT_TONE
    objective = (config.objective if config else None) or DEFAULT_OBJECTIVE

    business = (profile.business_name if profile else None) or "el negocio"
    industry = profile.industry if profile else None
    description = profile.description if profile else None
    currency = (profile.currency if profile else None) or "PEN"
    timezone = (profile.timezone if profile else None) or DEFAULT_TIMEZONE
    hours = _format_hours(profile.opening_hours if profile else None)

    lines: list[str] = [
        f"Eres {name}, el asistente de {business} y atiendes por WhatsApp.",
    ]
    if industry:
        lines.append(f"El negocio es del rubro {industry}.")
    if description:
        lines.append(f"Así describe el dueño su negocio: {description}")

    if now is not None:
        lines.append("")
        lines.append(f"Hoy es {_format_today(now, timezone)}.")

    lines.append("")
    lines.append(_TONE_GUIDANCE.get(tone, _TONE_GUIDANCE[DEFAULT_TONE]))
    lines.append(_OBJECTIVE_GUIDANCE.get(objective, _OBJECTIVE_GUIDANCE[DEFAULT_OBJECTIVE]))
    lines.append("Responde en español, breve, como en un chat. Evita párrafos largos.")

    # WhatsApp is not Markdown, and the model defaults to Markdown. `**negrita**`
    # reaches the customer with the asterisks showing, which reads as a broken
    # bot. Said here so the model aims right; `whatsapp_text()` in the agent
    # core then guarantees it, because prompt instructions on formatting are
    # advice a model drifts from over a long conversation.
    lines.append(
        "Formato de WhatsApp, no Markdown: *negrita* con un asterisco, _cursiva_ "
        "con guion bajo, ~tachado~. Nunca uses ** ni ##, y no escribas tablas."
    )

    # The guardrail. Stated before anything else the model might weigh against
    # it, and phrased as a prohibition plus a permitted alternative - a rule
    # with no escape hatch is one the model will route around by inventing.
    lines.append("")
    lines.append("REGLAS QUE NO PUEDES ROMPER:")
    lines.append(
        "1. Nunca inventes productos, precios, promociones, plazos ni políticas. "
        "Si un dato no aparece en el resultado de tus herramientas, no lo sabes. "
        "Escribe el nombre de cada producto tal cual te lo devolvió la herramienta, "
        "sin agregarle palabras."
    )
    if tool_names:
        lines.append(
            f"2. Tu única fuente de verdad son tus herramientas ({', '.join(tool_names)}). "
            "Consúltalas antes de afirmar cualquier dato concreto."
        )
    else:  # pragma: no cover - the loop always registers tools
        lines.append("2. No dispones de herramientas, así que no afirmes datos concretos.")
    lines.append(
        "3. Si no encuentras la información, dilo con honestidad y ofrece derivar "
        "con una persona del negocio. Es mejor no saber que equivocarse."
    )
    lines.append(f"4. Los precios están en {currency} y son exactamente los del catálogo.")
    # Rule 1 forbids inventing *data*; this forbids inventing *actions*, which
    # is a different failure and the more expensive one. A customer told "queda
    # registrado" hangs up believing the order exists, the business never sees
    # it, and nobody finds out until the customer arrives to collect. The agent
    # can only read: registering, reserving, scheduling and charging are not
    # among its tools, so claiming them is always false.
    lines.append(
        "5. No puedes registrar, reservar, agendar, cancelar ni cobrar nada: solo "
        "consultas información. Nunca digas que algo quedó registrado, reservado, "
        "agendado o pagado. Si el cliente quiere cerrar un pedido, resume lo que "
        "pidió y dile que una persona del negocio lo confirmará."
    )

    if config is not None and config.restrictions:
        lines.append("")
        lines.append(f"El dueño te pide además: {config.restrictions}")

    escalation = _escalation_rules(config)
    if escalation:
        lines.append("")
        triggers = ", o cuando ".join(escalation)
        lines.append(f"Deriva a una persona del negocio cuando {triggers}.")

    if hours:
        lines.append("")
        lines.append(f"Horario de atención ({timezone}): {hours}.")

    return "\n".join(lines)


# How many catalogue names the correction quotes back. A catalogue can hold
# thousands, and pasting all of them would cost more than the turn it is
# fixing. The names related to what the model just invented go first, so the
# ones it most needs survive the cut.
MAX_CORRECTION_NAMES = 30


def build_name_correction(invented: Sequence[str], catalogue: Sequence[str]) -> str:
    """The instruction handed back to a model that renamed a product.

    Spanish, like the rest of the prompt, and for the same reason: correcting
    in one language what must be rewritten in another measurably degrades
    adherence.

    It is deliberately concrete. "No inventes productos" is already rule 1 and
    the model broke it, so repeating it changes nothing; naming the exact
    string it wrote and the exact strings it is allowed to write instead gives
    it something to copy rather than something to interpret.
    """
    offending = ", ".join(f"«{name}»" for name in invented)
    names = _correction_names(invented, catalogue)
    listing = "\n".join(f"- {name}" for name in names)

    lines = [
        f"ALTO. Escribiste {offending}, y eso no es un producto del catálogo.",
        "Un nombre parecido no sirve: el cliente vendría a pedir algo que el negocio no vende.",
        "",
        "Estos son los nombres del catálogo, tal cual están escritos:",
        listing,
    ]
    if len(names) < len(catalogue):
        lines.append(
            f"(Son solo los primeros {len(names)} de {len(catalogue)}. "
            "Usa únicamente nombres que te haya devuelto una herramienta.)"
        )
    lines.extend(
        [
            "",
            "Vuelve a responderle al cliente copiando esos nombres exactamente, "
            "sin agregarles ni quitarles palabras.",
            "Si lo que pidió no está en la lista, dilo con honestidad y ofrécele "
            "derivar con una persona del negocio.",
            "Responde solo con el mensaje para el cliente.",
        ]
    )
    return "\n".join(lines)


def _correction_names(invented: Sequence[str], catalogue: Sequence[str]) -> list[str]:
    """Catalogue names to quote back, the relevant ones first.

    "Relevant" is a shared word with what the model wrote: it renamed
    something, so the real name is almost always the one it mangled, and a
    truncated list that dropped it would be worse than useless.
    """
    wanted = {word for phrase in invented for word in words(phrase)}
    related = [name for name in catalogue if wanted & set(words(name))]
    rest = [name for name in catalogue if name not in related]
    return [*related, *rest][:MAX_CORRECTION_NAMES]


def _escalation_rules(config: AgentConfig | None) -> list[str]:
    """Readable phrasing for the configured escalation triggers.

    Unknown keys are passed through verbatim rather than dropped: the column
    is JSONB and a future feature may add triggers this file has not heard of,
    and silently ignoring one would remove a guardrail the owner switched on.
    """
    if config is None:
        return []
    raw = config.escalation_rules
    if isinstance(raw, str):  # tolerated: JSONB can arrive as a JSON string
        try:
            raw = json.loads(raw)
        except ValueError:  # pragma: no cover - defensive
            return []
    if not isinstance(raw, list):
        return []
    return [_ESCALATION_PHRASES.get(str(rule), str(rule)) for rule in raw if rule]
