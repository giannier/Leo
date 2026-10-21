"""`derivar_a_humano`: a person of the business takes the conversation (TASK-443, C-14).

The tool:

* writes `derivado = {motivo, resumen, hasta}`, with `hasta` the business's pause
  (`agent_configs.derivacion_pausa_horas`, 6 by default). Until then the turn
  pipeline stores what the customer writes and calls no model (the gate);
* with `motivo = asesoria` also writes `asesoria_derivada_en`, which outlives the
  pause, so ASESORIA does not hand the customer over again every time they write;
* tells the business, in the panel and by email, with the summary (not in the
  simulator: `notices` is None there);
* with `motivo = cotizacion` also keeps `datos`, what the customer already said ("personas": "20"),
  so the CRM shows the quote as a form and not only as a sentence;
* **ends the turn.** The customer receives the business's handover message
  (`mensajes_sistema.derivacion`, Milestone B decision 19), never more words from
  the model.

The pause also applies in the simulator, which is how the owner sees it work; a new
simulator conversation starts without it.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from app.agent.tools.registry import ToolContext, ToolResult
from app.process.system_messages import render_system_message
from app.process.templates.base import DEFAULT_SYSTEM_MESSAGES
from app.repositories.business import AgentConfigRepository, BusinessProfileRepository
from app.scheduling.availability import is_open, next_opening
from app.scheduling.text import when_phrase

DEFAULT_PAUSE_HOURS = 6
MAX_SUMMARY = 500
MAX_OFFERED = 120
MAX_QUOTE_DATA = 8
logger = logging.getLogger(__name__)

REASONS = ("pide_humano", "queja", "sin_respuesta", "asesoria", "cotizacion", "postventa", "otro")
MOODS = ("tranquilo", "apurado", "molesto", "dudoso")


def _quote_data(raw: object) -> dict[str, str]:
    """`[{"dato": "personas", "valor": "20"}]` as a dict: short, unique, at most eight."""
    data: dict[str, str] = {}
    for item in raw if isinstance(raw, list) else []:
        if not isinstance(item, dict):
            continue
        name = " ".join(str(item.get("dato") or "").split())[:40]
        value = " ".join(str(item.get("valor") or "").split())[:120]
        if name and value and name not in data and len(data) < MAX_QUOTE_DATA:
            data[name] = value
    return data


def _after_hours(context: ToolContext, now: datetime) -> str:
    """The sentence that says when the answer comes, when the business is closed.

    Only when its hours are known: a business that never wrote them is not told it is closed.
    """
    profile = BusinessProfileRepository(context.session, context.tenant_id).current()
    hours = dict(profile.opening_hours) if profile and profile.opening_hours else {}
    if not hours:
        return ""
    try:
        zone = ZoneInfo((profile.timezone if profile else None) or "America/Lima")
    except ZoneInfoNotFoundError:
        zone = ZoneInfo("America/Lima")
    if is_open(hours, zone, now):
        return ""
    opening = next_opening(hours, zone, now)
    if opening is None:
        return ""
    return f" Ahora estamos fuera de horario: te escribimos {when_phrase(opening, zone, now)}."


def handover_text(context: ToolContext, *, now: datetime | None = None) -> str:
    """The business's handover message, or the default one, plus when it will be answered."""
    config = AgentConfigRepository(context.session, context.tenant_id).current()
    edited = (config.system_messages or {}).get("derivacion") if config is not None else None
    template = edited if isinstance(edited, str) and edited.strip() else None
    text = render_system_message(
        template or DEFAULT_SYSTEM_MESSAGES["derivacion"],
        {"nombre_cliente": context.facts.nombre_cliente},
    )
    return text + _after_hours(context, now or datetime.now(UTC))


def pause_hours(context: ToolContext) -> int:
    config = AgentConfigRepository(context.session, context.tenant_id).current()
    return config.derivacion_pausa_horas if config is not None else DEFAULT_PAUSE_HOURS


async def derivar_a_humano(context: ToolContext, arguments: dict[str, Any]) -> ToolResult:
    if context.conversation_id is None:
        return ToolResult.no_disponible(decir="No puedes derivar ahora. No lo menciones.")
    motivo = str(arguments.get("motivo") or "otro")
    if motivo not in REASONS:
        motivo = "otro"
    resumen = " ".join(str(arguments.get("resumen") or "").split())[:MAX_SUMMARY] or None
    # What the person who picks it up needs besides what the customer asked: what they were offered
    # and how they feel (feature 019, phase C: a warm handover).
    offered = " ".join(str(arguments.get("ofrecido") or "").split())[:MAX_OFFERED]
    mood = str(arguments.get("animo") or "")
    extra = [
        f"Se le ofreció: {offered}." if offered else "",
        f"Ánimo: {mood}." if mood in MOODS else "",
    ]
    if resumen and any(extra):
        resumen = " ".join([resumen, *[e for e in extra if e]])

    now = datetime.now(UTC)
    hasta = now + timedelta(hours=pause_hours(context))
    context.facts_patch["derivado"] = {
        "motivo": motivo,
        "resumen": resumen,
        "hasta": hasta.isoformat(),
        "desde": now.isoformat(),
    }
    if motivo == "cotizacion":
        context.facts_patch["derivado"]["datos"] = _quote_data(arguments.get("datos"))
    if motivo == "asesoria":
        context.facts_patch["asesoria_derivada_en"] = now.isoformat()
    if context.notices is not None:
        try:
            context.notices.handoff(context.conversation_id, motivo=motivo, resumen=resumen)
        except Exception:  # noqa: BLE001 - telling the business never fails the handover
            logger.exception("handoff notice failed")
    context.ending.reply = handover_text(context)
    return ToolResult.hecho(
        data={"motivo": motivo, "hasta": hasta.isoformat()},
        decir="La conversación pasó a una persona. No escribas nada más.",
    )
