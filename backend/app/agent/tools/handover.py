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
import re
from datetime import UTC, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from app.agent.tools.registry import ToolContext, ToolResult
from app.process.facts import Cotizacion
from app.process.system_messages import render_system_message
from app.process.templates.base import DEFAULT_SYSTEM_MESSAGES
from app.process.text import plain
from app.repositories.business import AgentConfigRepository, BusinessProfileRepository
from app.repositories.conversation import MessageRepository
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


# What a customer says when they will not or cannot give a datum. «sin dato» needs one of these in
# what they just wrote: an answer to something else («para negocio» to «¿para qué fecha?») is not
# a refusal, and the model took it for one to close the quote.
_REFUSAL = re.compile(
    r"\bno\s+(lo\s+)?(se|sabemos|tengo|tenemos|quiero|queremos|puedo|podemos|importa)\b|"
    r"todavia no|aun no|ahorita no|por ahora no|mas adelante|despues|luego|por definir|"
    r"a confirmar|prefiero|preferimos|da igual|cualquiera|depende"
)
LAST_MESSAGES = 8


def latest_customer_text(context: ToolContext) -> str:
    """What the customer wrote since the assistant last spoke, in order."""
    if context.conversation_id is None:
        return ""
    rows = MessageRepository(context.session, context.tenant_id).list_for_conversation(
        context.conversation_id
    )
    words: list[str] = []
    for message in reversed(list(rows)[-LAST_MESSAGES:]):
        if message.author != "customer":
            break
        words.append(message.content)
    return " ".join(reversed(words))


def customer_refused(context: ToolContext) -> bool:
    """Whether what the customer wrote since the assistant last spoke says they will not give it."""
    return bool(_REFUSAL.search(plain(latest_customer_text(context))))


# «pásame con alguien del equipo», «quiero hablar con una persona»: a person, asked for in so many
# words. Platform rule 5 hands over then, with whatever the quote holds.
_ASKS_FOR_A_PERSON = re.compile(
    r"\b(pasa(me)?|comunica(me)?|contacta(me)?|deriva(me)?|hablar|habla|atienda|atiendan|"
    r"llame|llamen|escriba|escriban)\b.{0,40}\b(persona|humano|humana|asesor|asesora|alguien|"
    r"encargad[oa]|vendedor|vendedora|ejecutiv[oa]|equipo|dueno|duena)\b"
)


def customer_asks_for_a_person(context: ToolContext) -> bool:
    """Whether what the customer wrote since the assistant last spoke asks for a person."""
    return bool(_ASKS_FOR_A_PERSON.search(plain(latest_customer_text(context))))


# A question the business never answered while a quote is being put together: the person gets it
# with the quote, so the conversation goes on. It handed over «¿distribuyen a Huacho?» at once, with
# nothing for the person to quote (three runs of three, gpt-4.1-mini, 2026-10-27).
_ASIDE_REASONS = frozenset({"asesoria", "sin_respuesta", "otro"})
ASIDE_REFUSAL = (
    "No derives por esta duda: dile que eso se lo confirma una persona del equipo junto con la "
    "cotización, y sigue con ella (pregunta lo que falta). Si pide hablar con una persona, deriva."
)


def _an_aside_while_quoting(context: ToolContext, motivo: str) -> bool:
    options = context.options
    if motivo not in _ASIDE_REASONS or options is None:
        return False
    if not (options.cotizar and options.datos_cotizacion):
        return False
    chosen = options.camino(context.facts.camino) if context.facts.camino else None
    if chosen is not None and chosen.tipo == "asesoria":
        return False  # the path the business set for a person
    if customer_asks_for_a_person(context) or customer_refused(context):
        return False
    noted = current_quote(context)
    return noted is None or bool(noted.falta)


def current_quote(context: ToolContext) -> Cotizacion | None:
    """The quote in progress: what a tool set earlier this turn, else what the facts hold."""
    patched = context.facts_patch.get("cotizacion")
    if isinstance(patched, dict):
        return Cotizacion.model_validate(patched)
    return context.facts.cotizacion


def recap_line(datos: dict[str, str], producto: str | None = None) -> str:
    """What the customer gave, written back to them: «Anoté: Bidón 20 L; cantidad: 5; fecha: lunes.»

    The handover used to say only «ya le pasé lo que necesitas»: the customer could not tell what
    had been passed on, and a wrong figure went to the person unnoticed.
    """
    parts = [producto] if producto else []
    for name, value in datos.items():
        if producto and plain(name) == "producto":
            continue  # already the head: «Bidón 20 L; producto: Bidón 20 L» reads as a mistake
        parts.append(f"{name}: {'por definir' if plain(value).startswith('sin dato') else value}")
    return f"Anoté: {'; '.join(parts)}." if parts else ""


def handover_text(
    context: ToolContext,
    *,
    now: datetime | None = None,
    datos: dict[str, str] | None = None,
    producto: str | None = None,
) -> str:
    """The business's handover message, what was noted and when it will be answered."""
    config = AgentConfigRepository(context.session, context.tenant_id).current()
    edited = (config.system_messages or {}).get("derivacion") if config is not None else None
    template = edited if isinstance(edited, str) and edited.strip() else None
    text = render_system_message(
        template or DEFAULT_SYSTEM_MESSAGES["derivacion"],
        {"nombre_cliente": context.facts.nombre_cliente},
    )
    recap = recap_line(datos, producto) if datos else ""
    if recap:
        text = f"{text}\n\n{recap}"
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

    if _an_aside_while_quoting(context, motivo):
        return ToolResult.no_disponible(decir=ASIDE_REFUSAL)
    quote: dict[str, str] | None = None
    producto: str | None = None
    if motivo == "cotizacion":
        # What the model passes, over what was noted turn by turn: nothing noted is lost.
        noted = current_quote(context)
        asks = context.options.datos_cotizacion if context.options is not None else ()
        # Asked for a person: it goes now, with what is noted (the tool refused it and the assistant
        # kept asking for the date of a customer who wanted to negotiate the price with someone).
        waits = not customer_refused(context) and not customer_asks_for_a_person(context)
        if noted is None and asks and waits:
            # Nothing noted yet and the business asks for data: handing over now sends a person a
            # conversation with nothing in it (the assistant did it on «quiero empezar a vender»).
            return ToolResult.no_disponible(
                decir=(
                    f"Aún no tienes ningún dato de la cotización ({', '.join(asks[:5])}). Conversa "
                    "con el cliente y anótalos con anotar_cotizacion; no derives todavía."
                )
            )
        if noted is not None and noted.falta and waits:
            return ToolResult.no_disponible(
                decir=(
                    f"Aún falta {', '.join(noted.falta)} y el cliente no dijo que no quiera darlo: "
                    f"pregúntalo (primero «{noted.falta[0]}»). No derives todavía."
                )
            )
        quote = {**(noted.datos if noted else {}), **_quote_data(arguments.get("datos"))}
        producto = noted.producto if noted else None
    return hand_over(context, motivo=motivo, resumen=resumen, datos=quote, producto=producto)


def hand_over(
    context: ToolContext,
    *,
    motivo: str,
    resumen: str | None,
    datos: dict[str, str] | None = None,
    producto: str | None = None,
) -> ToolResult:
    """Pass the conversation to a person and end the turn with the business's message.

    Shared by `derivar_a_humano`, which the model calls when it judges it is time, and by
    `anotar_cotizacion`, which does it itself when the quote has everything it asks for.
    """
    assert context.conversation_id is not None
    now = datetime.now(UTC)
    hasta = now + timedelta(hours=pause_hours(context))
    context.facts_patch["derivado"] = {
        "motivo": motivo,
        "resumen": resumen,
        "hasta": hasta.isoformat(),
        "desde": now.isoformat(),
    }
    if motivo == "cotizacion":
        context.facts_patch["derivado"]["datos"] = datos or {}
        # The quote is on its way to a person: the next one starts from nothing.
        context.facts_patch["cotizacion"] = None
    if motivo == "asesoria":
        context.facts_patch["asesoria_derivada_en"] = now.isoformat()
    if context.notices is not None:
        try:
            context.notices.handoff(context.conversation_id, motivo=motivo, resumen=resumen)
        except Exception:  # noqa: BLE001 - telling the business never fails the handover
            logger.exception("handoff notice failed")
    context.ending.reply = handover_text(context, datos=datos, producto=producto)
    return ToolResult.hecho(
        data={"motivo": motivo, "hasta": hasta.isoformat()},
        decir="La conversación pasó a una persona. No escribas nada más.",
    )
