"""`enviar_metodos_pago`: how to pay, in the customer's chat (Feature 004, TASK-428).

The tool sends the business's active payment methods as one text message, then
the image or QR that goes with them, and records `metodos_pago_enviados =
{order_id, en}` in the conversation's facts. That record is what moves the funnel
from NEGOCIACION to CUENTAS_ENVIADAS (`funnel_for`) and what makes a second call
answer `omitido`:

* **Once per order.** The derived fact `metodos_pago_enviados` is true only if the
  methods were sent for the *active* order and *after* the last rejection (C-21).
  So the same order is not sent them twice, a rejected customer gets them again
  once, and a new order after a closed one gets them as if it were the first.
* **The image.** Each method may have its own QR (`media_asset_id`). If no method
  has one, the library file with the key `metodos_pago` goes instead, if the
  business has uploaded it (`data-model.md` 0017).
* **The budget** (C-11). The text and every image each spend one message of the
  turn. If there is not room for the text, nothing is sent and the model is told to
  offer the methods in its next message; if there is room for the text but not for
  all the images, the text goes and the model is told which part did not.

Only the system confirms where the money goes: the text is what the owner wrote
for each method (`payment_methods_business.instructions`), never composed by the
model.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from app.agent.outbound import OutboundFailed
from app.agent.tools.registry import ToolContext, ToolResult
from app.agent.tools.shared import currency_symbol, money
from app.models.business import PaymentMethodBusiness
from app.models.media import MediaAsset
from app.orders.service import OrderService
from app.repositories.conversation import ConversationRepository
from app.repositories.media import MediaAssetRepository
from app.repositories.payment import PaymentMethodRepository

# The library key of the generic payment image (`data-model.md` 0017).
PAYMENT_ASSET_KEY = "metodos_pago"
# WhatsApp refuses a text message above 4 096 characters.
MAX_TEXT = 4000

METHOD_LABELS = {
    "yape": "Yape",
    "plin": "Plin",
    "transferencia": "Transferencia bancaria",
    "tarjeta": "Tarjeta",
    "efectivo": "Efectivo",
    "link": "Link de pago",
}

_NOT_SENDABLE = "No puedes enviar nada ahora. No menciones esa función."
_OVER_BUDGET = (
    "Ya enviaste suficientes mensajes en este turno. Ofrécelo en tu próximo mensaje "
    "y no digas que enviaste los métodos de pago."
)
_ALREADY = (
    "Ya le enviaste los métodos de pago a este cliente. No los envíes de nuevo ni los repitas; "
    "recuérdale que mande la captura del comprobante cuando pague."
)


def methods_text(methods: list[PaymentMethodBusiness]) -> str:
    """The message with every active method, as the owner wrote them."""
    lines = ["Estos son nuestros métodos de pago:"]
    for method in methods:
        label = METHOD_LABELS.get(method.method, method.method.capitalize())
        lines.append(f"\n• *{label}*\n{method.instructions.strip()}")
    return "\n".join(lines)[:MAX_TEXT]


def _already_sent(context: ToolContext, order_id: uuid.UUID) -> bool:
    """Whether the methods already went for this order (this turn or before)."""
    patched = context.facts_patch.get("metodos_pago_enviados")
    if isinstance(patched, dict) and patched.get("order_id") == str(order_id):
        return True
    registro = context.facts.metodos_pago_registro
    return (
        context.facts.metodos_pago_enviados
        and registro is not None
        and registro.order_id == order_id
    )


def _images(context: ToolContext, methods: list[PaymentMethodBusiness]) -> list[MediaAsset]:
    """The files that go with the methods: each method's own, else the generic one."""
    assets = MediaAssetRepository(context.session, context.tenant_id)
    found: list[MediaAsset] = []
    seen: set[uuid.UUID] = set()
    for method in methods:
        if method.media_asset_id is None or method.media_asset_id in seen:
            continue
        seen.add(method.media_asset_id)
        asset = assets.get(method.media_asset_id)
        if asset is not None and asset.is_active:
            found.append(asset)
    if found:
        return found
    generic = assets.by_clave(PAYMENT_ASSET_KEY)
    return [generic] if generic is not None and generic.is_active else []


async def enviar_metodos_pago(context: ToolContext, arguments: dict[str, Any]) -> ToolResult:
    """Send the payment methods of the business for the customer's order."""
    if context.conversation_id is None:
        return ToolResult.error("No hay una conversación abierta.")
    conversation = ConversationRepository(context.session, context.tenant_id).get(
        context.conversation_id
    )
    if conversation is None:
        return ToolResult.error("No se encontró la conversación.")

    order = OrderService(context.session, context.tenant_id).active_for(conversation.id)
    if order is None or order.estado != "borrador":
        return ToolResult.no_disponible(
            decir=(
                "No hay un pedido abierto al que enviarle los métodos de pago. No los menciones "
                "todavía: primero arma el pedido."
            )
        )
    if _already_sent(context, order.id):
        return ToolResult.omitido(decir=_ALREADY)

    methods = list(PaymentMethodRepository(context.session, context.tenant_id).list_active())
    if not methods:
        return ToolResult.no_disponible(
            decir=(
                "El negocio no tiene métodos de pago configurados. No inventes cuentas: usa "
                "derivar_a_humano para que una persona le indique cómo pagar."
            )
        )

    adapter = context.adapter
    if adapter is None:
        return ToolResult.no_disponible(decir=_NOT_SENDABLE)
    if not context.budget.can_consume():
        return ToolResult.no_disponible(decir=_OVER_BUDGET)

    try:
        await adapter.send_text(methods_text(methods))
    except OutboundFailed:
        return ToolResult.error(
            "No se pudieron enviar los métodos de pago. No digas que los enviaste."
        )
    context.budget.consume()

    sent_files: list[MediaAsset] = []
    unsent_files = 0
    for asset in _images(context, methods):
        if not context.budget.can_consume():
            unsent_files += 1
            continue
        try:
            await adapter.send_media(asset)
        except OutboundFailed:
            unsent_files += 1
            continue
        context.budget.consume()
        sent_files.append(asset)

    context.facts_patch["metodos_pago_enviados"] = {
        "order_id": str(order.id),
        "en": datetime.now(UTC).isoformat(),
    }
    if sent_files:
        already = context.facts_patch.get(
            "recursos_enviados", list(context.facts.recursos_enviados)
        )
        keys = [asset.clave for asset in sent_files if asset.clave not in already]
        context.facts_patch["recursos_enviados"] = [*already, *keys]

    symbol = currency_symbol(context.session, context.tenant_id)
    total = money(order.total, symbol)
    say = (
        "El cliente ya recibió los métodos de pago. No los copies ni los repitas. Dile el total "
        f"del pedido ({total}) y pídele que envíe la captura del comprobante cuando pague."
    )
    if unsent_files:
        say += " La imagen o el QR no alcanzó a enviarse: ofrécela en tu próximo mensaje."
    return ToolResult.hecho(
        data={
            "metodos": [method.method for method in methods],
            "imagenes_enviadas": len(sent_files),
            "imagenes_pendientes": unsent_files,
            "total": f"{order.total:.2f}",
        },
        texto=f"Métodos de pago enviados ({len(methods)}). Total del pedido: {total}.",
        decir=say,
    )
