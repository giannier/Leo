"""`calificar_cliente`: what the assistant learned about a customer (feature 019, phase E).

Called when the assistant knows enough to say how interested the customer is, never to
interrogate. It stores the answers and the interest (`alto`, `medio`, `bajo`) as the
conversation's `lead_calificado` fact; the CRM turns that into the lead's score (`caliente`,
`tibio`, `frio`). The first time a customer becomes `alto` the business is told, because a hot
lead is worth a person's minutes now.

The customer is never told: it is the assistant's note, not part of the conversation.
"""

from __future__ import annotations

import contextlib
from datetime import UTC, datetime
from typing import Any

from app.agent.tools.registry import ToolContext, ToolResult

INTERESTS = ("alto", "medio", "bajo")
MAX_ANSWERS = 6
MAX_REASON = 200


def _answers(raw: object) -> dict[str, str]:
    """`[{"pregunta": ..., "respuesta": ...}]` as a dict: short, unique, at most six."""
    answers: dict[str, str] = {}
    for item in raw if isinstance(raw, list) else []:
        if not isinstance(item, dict):
            continue
        question = " ".join(str(item.get("pregunta") or "").split())[:80]
        answer = " ".join(str(item.get("respuesta") or "").split())[:160]
        if question and answer and question not in answers and len(answers) < MAX_ANSWERS:
            answers[question] = answer
    return answers


async def calificar_cliente(context: ToolContext, arguments: dict[str, Any]) -> ToolResult:
    interest = str(arguments.get("interes") or "").lower()
    if interest not in INTERESTS:
        return ToolResult.error("interes debe ser alto, medio o bajo.")
    reason = " ".join(str(arguments.get("motivo") or "").split())[:MAX_REASON] or None
    answers = _answers(arguments.get("respuestas"))
    before = context.facts.lead_calificado
    context.facts_patch["lead_calificado"] = {
        "interes": interest,
        "motivo": reason,
        "respuestas": answers,
        "en": datetime.now(UTC).isoformat(),
    }
    if (
        interest == "alto"
        and (before is None or before.interes != "alto")
        and context.notices is not None
        and context.conversation_id is not None
    ):
        # Telling the business never fails the conversation.
        with contextlib.suppress(Exception):
            context.notices.hot_lead(context.conversation_id, resumen=reason)
    return ToolResult.hecho(decir="Es una nota interna: no se la menciones al cliente.")
