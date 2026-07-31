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
"""

from __future__ import annotations

import json
from typing import Any

from app.models.business import AgentConfig, BusinessProfile

DEFAULT_AGENT_NAME = "Leo"
DEFAULT_TONE = "cercano"
DEFAULT_OBJECTIVE = "ambos"

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


def build_system_prompt(
    *,
    profile: BusinessProfile | None,
    config: AgentConfig | None,
    tool_names: tuple[str, ...] = (),
) -> str:
    """Assemble the instructions for one tenant's agent.

    Pure: given the same rows it returns the same string, which is what lets
    the evals in TASK-016 compare prompts across changes.
    """
    name = (config.agent_name if config else None) or DEFAULT_AGENT_NAME
    tone = (config.tone if config else None) or DEFAULT_TONE
    objective = (config.objective if config else None) or DEFAULT_OBJECTIVE

    business = (profile.business_name if profile else None) or "el negocio"
    industry = profile.industry if profile else None
    description = profile.description if profile else None
    currency = (profile.currency if profile else None) or "PEN"
    timezone = (profile.timezone if profile else None) or "America/Lima"
    hours = _format_hours(profile.opening_hours if profile else None)

    lines: list[str] = [
        f"Eres {name}, el asistente de {business} y atiendes por WhatsApp.",
    ]
    if industry:
        lines.append(f"El negocio es del rubro {industry}.")
    if description:
        lines.append(f"Así describe el dueño su negocio: {description}")

    lines.append("")
    lines.append(_TONE_GUIDANCE.get(tone, _TONE_GUIDANCE[DEFAULT_TONE]))
    lines.append(_OBJECTIVE_GUIDANCE.get(objective, _OBJECTIVE_GUIDANCE[DEFAULT_OBJECTIVE]))
    lines.append("Responde en español, breve, como en un chat. Evita párrafos largos.")

    # The guardrail. Stated before anything else the model might weigh against
    # it, and phrased as a prohibition plus a permitted alternative - a rule
    # with no escape hatch is one the model will route around by inventing.
    lines.append("")
    lines.append("REGLAS QUE NO PUEDES ROMPER:")
    lines.append(
        "1. Nunca inventes productos, precios, promociones, plazos ni políticas. "
        "Si un dato no aparece en el resultado de tus herramientas, no lo sabes."
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
