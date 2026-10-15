"""`derivar_a_humano`: a person of the business takes the conversation (TASK-443, C-14).

The tool:

* writes `derivado = {motivo, resumen, hasta}`, with `hasta` the business's pause
  (`agent_configs.derivacion_pausa_horas`, 6 by default). Until then the turn
  pipeline stores what the customer writes and calls no model (the gate);
* with `motivo = asesoria` also writes `asesoria_derivada_en`, which outlives the
  pause, so ASESORIA does not hand the customer over again every time they write;
* tells the business, in the panel and by email, with the summary (not in the
  simulator: `notices` is None there);
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

from app.agent.tools.registry import ToolContext, ToolResult
from app.process.system_messages import render_system_message
from app.process.templates.base import DEFAULT_SYSTEM_MESSAGES
from app.repositories.business import AgentConfigRepository

DEFAULT_PAUSE_HOURS = 6
MAX_SUMMARY = 500
logger = logging.getLogger(__name__)

REASONS = ("pide_humano", "queja", "sin_respuesta", "asesoria", "cotizacion", "otro")


def handover_text(context: ToolContext) -> str:
    """The business's handover message, or the default one."""
    config = AgentConfigRepository(context.session, context.tenant_id).current()
    edited = (config.system_messages or {}).get("derivacion") if config is not None else None
    template = edited if isinstance(edited, str) and edited.strip() else None
    return render_system_message(
        template or DEFAULT_SYSTEM_MESSAGES["derivacion"],
        {"nombre_cliente": context.facts.nombre_cliente},
    )


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

    now = datetime.now(UTC)
    hasta = now + timedelta(hours=pause_hours(context))
    context.facts_patch["derivado"] = {
        "motivo": motivo,
        "resumen": resumen,
        "hasta": hasta.isoformat(),
        "desde": now.isoformat(),
    }
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
