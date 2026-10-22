"""`consultar_pedidos`: what happened to the orders a customer made (feature 019, phase G).

After the sale the most frequent question is "¿cómo va mi pedido?" and, for a customer who buys
the same thing every time, "lo mismo que la vez pasada". The tool reads the customer's last three
orders (never the draft being assembled, never another customer's) and says each one's state in
plain words, its lines, its total and what the business wrote as the next step. It never promises
a date the business did not write. To repeat an order the assistant adds the same products with
`gestionar_pedido`.
"""

from __future__ import annotations

import uuid
from typing import Any

from app.agent.tools.aftersale import aftersale_lines
from app.agent.tools.registry import ToolContext, ToolResult
from app.models.conversation import Conversation
from app.models.orders import Order
from app.repositories.orders import OrderItemRepository, OrderRepository

HOW_IT_STANDS = {
    "por_confirmar": "su pago o su pedido está en revisión por el equipo",
    "confirmado": "confirmado por el equipo",
    "en_curso": "en preparación",
    "completado": "completado",
    "descartado": "cancelado",
}
MAX_ORDERS = 3


def _contact_id(context: ToolContext) -> uuid.UUID | None:
    if context.conversation_id is None:
        return None
    conversation = context.session.get(Conversation, context.conversation_id)
    if conversation is None or conversation.tenant_id != context.tenant_id:
        return None
    return conversation.contact_id


def _how_it_stands(order: Order) -> str:
    """Where the order is, as exactly as the business recorded it (feature 016)."""
    if order.estado == "en_curso" and order.etapa == "listo":
        return (
            "listo para recoger en el local"
            if order.tipo_entrega == "recojo"
            else "listo, a punto de salir"
        )
    if order.estado == "en_curso" and order.etapa == "en_camino":
        return "en camino"
    return HOW_IT_STANDS[order.estado]


def _line(order: Order, items: list[Any]) -> str:
    lines = [
        f"- {item.cantidad} × {item.nombre}"
        + (f" (producto_id: {item.product_id})" if item.product_id else "")
        for item in items
    ]
    head = (
        f"Pedido #{order.numero} ({order.created_at:%d/%m/%Y}): {_how_it_stands(order)}. "
        f"Total {order.moneda} {order.total:.2f}."
    )
    if order.tipo_entrega:
        zone = f" a {order.zona_envio}" if order.zona_envio else ""
        head += f" Entrega: {order.tipo_entrega}{zone}."
    if order.siguiente_paso:
        head += f" Siguiente paso que escribió el negocio: {order.siguiente_paso}"
    if order.estado == "descartado" and order.devolucion_pendiente:
        head += " Hay una devolución pendiente: que lo vea una persona."
    return "\n".join([head, *lines])


async def consultar_pedidos(context: ToolContext, arguments: dict[str, Any]) -> ToolResult:
    contact_id = _contact_id(context)
    if contact_id is None:
        return ToolResult.no_disponible(decir="No puedes ver pedidos ahora. No lo menciones.")
    orders = OrderRepository(context.session, context.tenant_id).of_contact(
        contact_id, sandbox=context.sandbox, limit=MAX_ORDERS
    )
    if not orders:
        return ToolResult.no_disponible(
            decir="El cliente no tiene pedidos hechos. Díselo y ofrécele armar uno."
        )
    grouped = OrderItemRepository(context.session, context.tenant_id).for_orders(
        [order.id for order in orders]
    )
    after = aftersale_lines(context, list(orders))
    text = "Pedidos del cliente, del más reciente al más antiguo:\n" + "\n".join(
        _line(order, grouped.get(order.id, []))
        + (f"\n  {after[order.id]}" if order.id in after else "")
        for order in orders
    )
    text += (
        "\nDi solo lo que está aquí: no prometas fechas ni tiempos de entrega que no estén. "
        "Si quiere repetir un pedido, agrega esos mismos productos con gestionar_pedido "
        "(producto_id y cantidad)."
    )
    return ToolResult.hecho(texto=text)
