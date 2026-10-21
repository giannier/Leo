"""The tools that book appointments (feature 019, phase D; spec 014).

`consultar_horarios` offers at most three free times of a bookable service, `agendar_cita` books one
of them and `gestionar_cita` lists, moves, confirms or cancels the customer's own appointments. The
model never invents a time: it offers what `consultar_horarios` returned and books the `slot` the
customer chose. If two customers ask for the same minute the database decides; the loser is offered
the next times at once.

In the simulator nothing is written to the calendar (a test must not take a real customer's place),
but every check runs, so the owner sees the conversation as it would go.
"""

from __future__ import annotations

import uuid
from datetime import date
from typing import Any

from app.agent.tools.registry import ToolContext, ToolResult
from app.models.business import Product
from app.models.conversation import Conversation
from app.scheduling.availability import spread
from app.scheduling.service import Scheduling, SchedulingError, Slot, SlotTaken
from app.scheduling.text import describe, parse_slot, slot_token

MAX_OPTIONS = 3
MAX_NOTE = 300
PRODUCT_NOT_BOOKABLE = (
    "Ese servicio no se agenda por aquí. Si el cliente lo quiere, pásalo a una persona con "
    "derivar_a_humano (motivo asesoria)."
)


def _contact_id(context: ToolContext) -> uuid.UUID | None:
    if context.conversation_id is None:
        return None
    conversation = context.session.get(Conversation, context.conversation_id)
    if conversation is None or conversation.tenant_id != context.tenant_id:
        return None
    return conversation.contact_id


def _product(context: ToolContext, scheduling: Scheduling, raw: object) -> Product | None:
    try:
        product = scheduling.products.get(uuid.UUID(str(raw).strip()))
    except ValueError:
        return None
    if product is None or product.deleted_at is not None or not product.agendable:
        return None
    return product


def _options_text(
    product: Product, slots: list[Slot], scheduling: Scheduling, *, intro: str
) -> str:
    zone = scheduling.zone()
    chosen = spread([s.start for s in slots], zone, MAX_OPTIONS)
    lines = [f"{intro} «{product.name}» (dura {product.duracion_min} min):"]
    lines += [f"- slot {slot_token(s, zone)}: {describe(s, zone)}" for s in chosen]
    lines.append(
        "Ofrece como máximo estos horarios, sin inventar otros. Cuando el cliente elija uno, "
        "llama a agendar_cita con su slot."
    )
    return "\n".join(lines)


def _day(raw: object) -> date | None:
    try:
        return date.fromisoformat(str(raw).strip()[:10])
    except ValueError:
        return None


async def consultar_horarios(context: ToolContext, arguments: dict[str, Any]) -> ToolResult:
    scheduling = Scheduling(context.session, context.tenant_id)
    product = _product(context, scheduling, arguments.get("servicio_id"))
    if product is None:
        return ToolResult.no_disponible(decir=PRODUCT_NOT_BOOKABLE)
    slots = scheduling.offer(product, on_or_after=_day(arguments.get("desde")))
    if not slots:
        return ToolResult.no_disponible(
            decir=(
                "No hay horarios libres en los próximos días. Díselo con naturalidad y ofrécele "
                "que una persona lo coordine con derivar_a_humano (motivo asesoria)."
            )
        )
    context.facts_patch["vio_productos"] = True
    return ToolResult.hecho(
        texto=_options_text(product, slots, scheduling, intro="Horarios libres de")
    )


async def agendar_cita(context: ToolContext, arguments: dict[str, Any]) -> ToolResult:
    scheduling = Scheduling(context.session, context.tenant_id)
    product = _product(context, scheduling, arguments.get("servicio_id"))
    if product is None:
        return ToolResult.no_disponible(decir=PRODUCT_NOT_BOOKABLE)
    zone = scheduling.zone()
    start = parse_slot(arguments.get("slot"), zone)
    if start is None:
        return ToolResult.error("slot no es válido: usa el slot que devolvió consultar_horarios.")
    contact_id = _contact_id(context)
    note = " ".join(str(arguments.get("notas") or "").split())[:MAX_NOTE] or None
    try:
        if context.sandbox:
            # Same checks, nothing written: the simulator never takes a real customer's place.
            if start not in {
                s.start
                for s in scheduling.offer(product, on_or_after=start.astimezone(zone).date())
            }:
                raise SlotTaken()
        else:
            appointment = scheduling.book(
                product,
                start,
                by="agent",
                contact_id=contact_id,
                conversation_id=context.conversation_id,
                notas=note,
            )
            start = appointment.starts_at
    except SlotTaken:
        later = scheduling.offer(product, on_or_after=start.astimezone(zone).date())
        text = "Ese horario se acaba de ocupar."
        if later:
            text += "\n" + _options_text(product, later, scheduling, intro="Siguientes horarios de")
        return ToolResult.no_disponible(
            decir="Dile con naturalidad que se acaba de ocupar y ofrécele estos otros.", texto=text
        )
    except SchedulingError as exc:
        return ToolResult.no_disponible(decir=str(exc))
    context.facts_patch["cita_agendada"] = start.isoformat()
    return ToolResult.hecho(
        data={"servicio": product.name, "inicio": start.isoformat()},
        decir=(
            f"Confirma al cliente su cita de {product.name}: {describe(start, zone)}. "
            "Si el negocio tiene dirección, dísela; avísale que puede escribir aquí para cambiarla."
        ),
    )


def _mine(scheduling: Scheduling, context: ToolContext) -> list[Any]:
    contact_id = _contact_id(context)
    return scheduling.upcoming_of(contact_id) if contact_id is not None else []


async def gestionar_cita(context: ToolContext, arguments: dict[str, Any]) -> ToolResult:
    scheduling = Scheduling(context.session, context.tenant_id)
    zone = scheduling.zone()
    action = str(arguments.get("accion") or "ver")
    mine = _mine(scheduling, context)
    if action == "ver" or not mine:
        if not mine:
            return ToolResult.no_disponible(decir="El cliente no tiene citas pendientes.")
        lines = []
        for a in mine:
            product = scheduling.products.get(a.product_id)
            name = product.name if product else "Cita"
            lines.append(f"- cita_id {a.id}: {name}, {describe(a.starts_at, zone)} ({a.status})")
        return ToolResult.hecho(texto="Citas del cliente:\n" + "\n".join(lines))

    chosen = next((a for a in mine if str(a.id) == str(arguments.get("cita_id"))), None)
    if chosen is None and len(mine) == 1:
        chosen = mine[0]
    if chosen is None:
        return ToolResult.error("cita_id no es válido: usa uno de los que devuelve la acción ver.")
    try:
        if action == "confirmar":
            scheduling.set_status(chosen, "confirmar", by="agent")
            return ToolResult.hecho(decir="Agradécele: su cita queda confirmada.")
        if action == "cancelar":
            scheduling.cancel(
                chosen,
                by="agent",
                motivo=" ".join(str(arguments.get("motivo") or "").split())[:200],
            )
            return ToolResult.hecho(
                decir="Confirma que la cita quedó cancelada y ofrécele agendar otra cuando quiera."
            )
        if action == "reprogramar":
            start = parse_slot(arguments.get("slot"), zone)
            if start is None:
                return ToolResult.error("slot no es válido: usa el slot de consultar_horarios.")
            scheduling.reschedule(chosen, start, by="agent")
            context.facts_patch["cita_agendada"] = chosen.starts_at.isoformat()
            return ToolResult.hecho(
                decir=f"Confirma su nueva cita: {describe(chosen.starts_at, zone)}."
            )
    except SlotTaken:
        product = scheduling.products.get(chosen.product_id)
        later = scheduling.offer(product, ignore=chosen.id) if product else []
        text = "Ese horario no está libre."
        if product and later:
            text += "\n" + _options_text(product, later, scheduling, intro="Horarios libres de")
        return ToolResult.no_disponible(decir="Ofrécele estos otros horarios.", texto=text)
    except SchedulingError as exc:
        return ToolResult.no_disponible(decir=str(exc))
    return ToolResult.error("accion no es válida: usa ver, reprogramar, cancelar o confirmar.")
