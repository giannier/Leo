"""`solicitar_devolucion` and `enviar_documento`: after the sale (feature 019, phase G).

* `solicitar_devolucion` **registers** what the customer asks about an order they made (a return, an
  exchange, the warranty, a complaint) with what they said and the photos they sent. Deciding is the
  business's — refunding is money — so the tool never promises a refund, an exchange or a date. The
  business's own policy (does it take returns, in how many days) is respected for a change of mind;
  a defect, a wrong or an incomplete order is registered whatever the window.
* `enviar_documento` sends the customer a document of **their own** order (the boleta, the factura,
  the guía, the warranty) that the business attached. A document that is not attached yet is not
  invented: the business is told, and the customer is told someone will send it.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import select

from app.aftersale.documents import DocumentService, label_of, mark_sent
from app.aftersale.returns import TYPE_LABELS, ReturnError, ReturnService
from app.agent.outbound import OutboundFailed
from app.agent.tools.registry import ToolContext, ToolResult
from app.models.aftersale import OrderDocument, OrderReturn
from app.models.conversation import Conversation, Message
from app.models.orders import Order
from app.orders.service import SALE_MADE
from app.repositories.aftersale import DocumentRepository, ReturnRepository
from app.repositories.orders import OrderItemRepository, OrderRepository
from app.repositories.sales_config import BusinessInfoRepository
from app.schemas.business_info import Devoluciones

MAX_ORDERS = 5
# Photos the customer sent in the last hours are the proof they mean.
EVIDENCE_WINDOW = timedelta(hours=3)
MAX_EVIDENCE = 5
# A change of mind is bound by the business's window; these reasons are the business's fault.
OWN_FAULT = frozenset({"defectuoso", "equivocado", "incompleto"})

STATE_WORDS = {
    "solicitada": "en revisión por el equipo",
    "aprobada": "aprobada",
    "rechazada": "no aprobada",
    "resuelta": "resuelta",
    "cancelada": "cancelada",
}
_NOT_SENDABLE = "No puedes enviar nada ahora. No menciones esa función."
_OVER_BUDGET = "Ya enviaste suficientes mensajes en este turno. No digas que enviaste el documento."


def _conversation(context: ToolContext) -> Conversation | None:
    if context.conversation_id is None:
        return None
    conversation = context.session.get(Conversation, context.conversation_id)
    if conversation is None or conversation.tenant_id != context.tenant_id:
        return None
    return conversation


def _orders_of(context: ToolContext, contact_id: uuid.UUID) -> list[Order]:
    """The customer's orders that are a sale made, newest first. Nobody else's."""
    orders = OrderRepository(context.session, context.tenant_id).of_contact(
        contact_id, sandbox=context.sandbox, limit=MAX_ORDERS
    )
    return [o for o in orders if o.estado in SALE_MADE]


def _which(orders: list[Order], numero: Any) -> tuple[Order | None, str | None]:
    """The order meant, or the question to ask when it is not clear."""
    if numero not in (None, ""):
        try:
            wanted = int(str(numero).lstrip("#"))
        except ValueError:
            wanted = -1
        found = next((o for o in orders if o.numero == wanted), None)
        if found is None:
            return None, (
                "Ese número no es de un pedido del cliente. Pregúntale cuál es: "
                + ", ".join(f"#{o.numero}" for o in orders)
                + "."
            )
        return found, None
    if len(orders) == 1:
        return orders[0], None
    return None, (
        "El cliente tiene varios pedidos. Pregúntale de cuál (por número o por lo que compró): "
        + ", ".join(f"#{o.numero} ({o.created_at:%d/%m})" for o in orders)
        + "."
    )


def _policy(context: ToolContext) -> Devoluciones | None:
    raw = BusinessInfoRepository(context.session, context.tenant_id).sections().get("devoluciones")
    if not raw:
        return None
    try:
        return Devoluciones.model_validate(raw)
    except ValueError:  # a half-written section is no policy
        return None


def _outside_policy(
    order: Order, tipo: str, motivo: str, policy: Devoluciones | None
) -> str | None:
    """Why a change of mind cannot be registered, in words for the customer; None if it can."""
    if policy is None or motivo in OWN_FAULT or tipo not in ("devolucion", "cambio"):
        return None
    if tipo == "devolucion" and not policy.acepta_devoluciones:
        return "El negocio no hace devoluciones por cambio de opinión."
    if tipo == "cambio" and not policy.acepta_cambios:
        return "El negocio no hace cambios por cambio de opinión."
    if policy.plazo_dias:
        since = order.confirmed_at or order.created_at
        days = (datetime.now(UTC) - since).days
        if days > policy.plazo_dias:
            return f"El plazo del negocio es de {policy.plazo_dias} días y el pedido tiene {days}."
    return None


def _recent_photos(context: ToolContext, conversation: Conversation) -> list[dict[str, Any]]:
    since = datetime.now(UTC) - EVIDENCE_WINDOW
    rows = context.session.execute(
        select(Message.id)
        .where(
            Message.tenant_id == context.tenant_id,
            Message.conversation_id == conversation.id,
            Message.direction == "inbound",
            Message.msg_type == "image",
            Message.created_at >= since,
        )
        .order_by(Message.created_at.desc())
        .limit(MAX_EVIDENCE)
    ).scalars()
    return [{"message_id": str(message_id)} for message_id in rows]


async def solicitar_devolucion(context: ToolContext, arguments: dict[str, Any]) -> ToolResult:
    conversation = _conversation(context)
    contact_id = conversation.contact_id if conversation else None
    if conversation is None or contact_id is None:
        return ToolResult.no_disponible(decir="No puedes registrar eso ahora. No lo menciones.")
    orders = _orders_of(context, contact_id)
    if not orders:
        return ToolResult.no_disponible(
            decir=(
                "El cliente no tiene un pedido confirmado, así que no hay nada que devolver. "
                "Díselo con amabilidad; si insiste, usa derivar_a_humano."
            )
        )
    order, question = _which(orders, arguments.get("pedido"))
    if order is None:
        return ToolResult.omitido(decir=question)

    tipo = str(arguments.get("tipo") or "")
    motivo = str(arguments.get("motivo") or "otro")
    blocked = _outside_policy(order, tipo, motivo, _policy(context))
    if blocked:
        return ToolResult.no_disponible(
            decir=(
                f"{blocked} En este mensaje explícaselo con amabilidad, con la política del "
                "negocio, y pregúntale si quiere que una persona evalúe su caso. NO derives "
                "todavía: solo si te dice que sí, usa derivar_a_humano con motivo postventa. "
                "No prometas excepciones."
            )
        )

    lines = list(
        OrderItemRepository(context.session, context.tenant_id)
        .for_orders([order.id])
        .get(order.id, [])
    )
    wanted = [
        {"nombre": str(name)} for name in (arguments.get("productos") or []) if str(name).strip()
    ]
    try:
        opened = ReturnService(context.session, context.tenant_id).open(
            order,
            tipo=tipo,
            motivo=motivo,
            detalle=str(arguments.get("detalle") or ""),
            items=wanted,
            lines=lines,
            evidencia=_recent_photos(context, conversation),
            actor="agent",
            conversation_id=conversation.id,
        )
    except ReturnError as exc:
        return ToolResult.no_disponible(decir=f"{exc} No lo registraste; corrígelo con el cliente.")

    ret = opened.ret
    label = TYPE_LABELS[ret.tipo]
    if not opened.created:
        return ToolResult.omitido(
            decir=(
                f"Ya había una solicitud de {label} #{ret.numero} de ese pedido, "
                f"{STATE_WORDS[ret.estado]}. Díselo; no la registres otra vez."
            )
        )
    if context.notices is not None:
        context.notices.return_requested(order, numero=ret.numero, tipo=ret.tipo, motivo=ret.motivo)
    return ToolResult.hecho(
        data={"solicitud": ret.numero, "pedido": order.numero, "tipo": ret.tipo},
        decir=(
            f"Registraste la solicitud de {label} #{ret.numero} del pedido #{order.numero}. "
            "Dile que una persona del negocio la revisa y le responde por este chat. NO prometas "
            "reembolso, cambio, recojo ni fechas: lo decide el negocio."
        ),
    )


# ----------------------------------------------------------------- documents


def _document_for(
    documents: dict[uuid.UUID, list[OrderDocument]], orders: list[Order], tipo: str | None
) -> tuple[Order, OrderDocument] | None:
    for order in orders:
        for document in documents.get(order.id, []):
            if tipo is None or document.tipo == tipo:
                return order, document
    return None


async def enviar_documento(context: ToolContext, arguments: dict[str, Any]) -> ToolResult:
    conversation = _conversation(context)
    contact_id = conversation.contact_id if conversation else None
    if conversation is None or contact_id is None:
        return ToolResult.no_disponible(decir="No puedes enviar documentos ahora. No lo menciones.")
    orders = _orders_of(context, contact_id)
    if not orders:
        return ToolResult.no_disponible(
            decir="El cliente no tiene un pedido confirmado, así que no hay documentos que enviar."
        )
    tipo = str(arguments.get("tipo") or "") or None
    wanted_order, question = (
        _which(orders, arguments.get("pedido")) if arguments.get("pedido") else (None, None)
    )
    if question:
        return ToolResult.omitido(decir=question)
    scope = [wanted_order] if wanted_order is not None else orders
    documents = DocumentRepository(context.session, context.tenant_id).of_orders(
        [o.id for o in scope]
    )
    found = _document_for(documents, scope, tipo)
    if found is None:
        target = scope[0]
        if context.notices is not None:
            context.notices.document_requested(target, tipo=tipo)
        return ToolResult.no_disponible(
            decir=(
                f"Ese documento del pedido #{target.numero} todavía no está disponible. Ya "
                "avisaste al equipo: dile que se lo hacen llegar por este chat. No inventes "
                "plazos ni lo describas."
            )
        )
    order, document = found
    adapter = context.adapter
    if adapter is None:
        return ToolResult.no_disponible(decir=_NOT_SENDABLE)
    if not context.budget.can_consume():
        return ToolResult.no_disponible(decir=_OVER_BUDGET)
    asset = DocumentService.as_asset(document, order)
    try:
        await adapter.send_media(asset)
    except OutboundFailed:
        return ToolResult.error("No se pudo enviar el documento. No digas que lo enviaste.")
    context.budget.consume()
    mark_sent(context.session, document, asset)
    return ToolResult.hecho(
        data={"documento": label_of(document), "pedido": order.numero},
        decir=(
            f"El documento ({label_of(document)}, pedido #{order.numero}) ya le llegó al cliente. "
            "No lo repitas; puedes referirte a él."
        ),
    )


# ------------------------------------------------------- what consultar_pedidos adds


def aftersale_lines(context: ToolContext, orders: list[Order]) -> dict[uuid.UUID, str]:
    """For each order: its returns and the documents ready to send, in a line to append."""
    ids = [o.id for o in orders]
    returns = ReturnRepository(context.session, context.tenant_id).of_orders(ids)
    documents = DocumentRepository(context.session, context.tenant_id).of_orders(ids)
    out: dict[uuid.UUID, str] = {}
    for order in orders:
        parts: list[str] = []
        for ret in returns.get(order.id, [])[:2]:
            parts.append(_return_line(ret))
        docs = documents.get(order.id, [])
        if docs:
            parts.append(
                "Documentos que puedes enviar con enviar_documento: "
                + ", ".join(label_of(d) for d in docs[:4])
                + "."
            )
        if parts:
            out[order.id] = " ".join(parts)
    return out


def _return_line(ret: OrderReturn) -> str:
    text = f"Solicitud de {TYPE_LABELS[ret.tipo]} #{ret.numero}: {STATE_WORDS[ret.estado]}."
    if ret.resolucion and ret.estado != "solicitada":
        text += f" El negocio escribió: {ret.resolucion}"
    return text
