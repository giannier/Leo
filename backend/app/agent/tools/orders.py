"""`gestionar_pedido`: the one tool that builds the order (Feature 004, TASK-426).

Every change to the order goes through here, and nothing the model says is a
price: the arguments are a product id, a quantity, a theme, a promotion id. The
name and price are copied from the catalogue by `OrderService`, the totals come
from `pricing`, and the answer is always the whole order as the system now holds
it, so the model never has to remember or recompute anything.

Actions (`contracts/api.md` section 7):

* `agregar`, `quitar`, `cantidad`, `vaciar`, `ver`: the lines.
* `elegir`: the selections of a package (free themes if the package allows them,
  catalogue products if not). Free themes join the catalogue guard's whitelist for
  the rest of the turn.
* `aplicar_promocion`: offers a closing promotion. The offer is recorded in the
  conversation's facts even when there is no order yet, and priced into the order
  that is created within its validity.
* `confirmar`: closes a cash-on-delivery order (the stage guard in
  `definitions.py` limits it to COBRO).

A product that is quoted (`modo_venta = cotizacion`) is never added: the answer
tells the model to hand the conversation to a person with a summary.
"""

from __future__ import annotations

import re
import uuid
from collections.abc import Mapping
from datetime import timedelta
from decimal import Decimal
from typing import Any

from app.agent.tools.registry import ToolContext, ToolResult
from app.agent.tools.shared import currency_code, currency_symbol
from app.models.business import Product
from app.models.channel import Contact
from app.models.conversation import Conversation
from app.models.orders import Order
from app.orders.pricing import Line as PricingLine
from app.orders.pricing import Rule, applies_to_order, offer_is_valid
from app.orders.service import (
    MAX_QUANTITY,
    BadQuantity,
    InvalidTransition,
    LineNotFound,
    NotReady,
    NotSellable,
    OrderError,
    OrderLimit,
    OrderLocked,
    OrderService,
    SelectionRefused,
)
from app.process.facts import missing_customer_data
from app.process.text import plain
from app.repositories.channel import ContactRepository
from app.repositories.conversation import ConversationRepository
from app.repositories.product import ProductRepository
from app.repositories.sales_config import PromotionRepository

_NAME_SEPARATORS = re.compile(r"[,;\n]")
_MAX_SELECTIONS_PER_CALL = 10

_NO_ORDER = "Todavía no hay un pedido. Para empezar, agrega un producto."
_LOCKED = (
    "El pedido ya está en revisión del negocio y no se puede cambiar. Explícaselo al cliente; "
    'si quiere otra cosa, déjalo anotado con accion="solicitar_cambio" y su pedido en "nota".'
)


# --------------------------------------------------------------------- views


def _money(amount: Decimal) -> str:
    return f"{amount:.2f}"


def order_view(service: OrderService, order: Order) -> dict[str, Any]:
    """The order as the contract shows it to the model (`contracts/api.md` section 7)."""
    items: list[dict[str, Any]] = []
    missing_total = 0
    for item in service.lines(order):
        chosen = [str(selection.get("nombre", "")) for selection in item.bundle_selections]
        missing = (
            max(0, (item.bundle_size or 0) * item.cantidad - len(chosen)) if item.bundle_size else 0
        )
        missing_total += missing
        items.append(
            {
                "producto_id": str(item.product_id) if item.product_id else None,
                "nombre": item.nombre,
                "cantidad": item.cantidad,
                "precio_unitario": _money(item.precio_unitario),
                "subtotal": _money(item.subtotal),
                "selecciones": chosen,
                "faltan_selecciones": missing,
            }
        )
    snapshot = order.promotion_snapshot
    return {
        "numero": order.numero,
        "estado": order.estado,
        "items": items,
        "subtotal": _money(order.subtotal),
        "descuento": _money(order.descuento),
        "envio": _money(order.envio),
        "total": _money(order.total),
        "moneda": order.moneda,
        "promocion": (
            None
            if snapshot is None
            else {"nombre": snapshot.get("nombre"), "ahorro": snapshot.get("ahorro")}
        ),
        "completo": bool(items) and missing_total == 0,
        "faltan_selecciones": missing_total,
        "requiere_entrega": service.requires_delivery(order),
        "tipo_entrega": order.tipo_entrega,
        "zona_envio": order.zona_envio,
    }


def order_text(view: Mapping[str, Any], symbol: str) -> str:
    """The order as prose: what the model reads and quotes."""
    lines = [f"Pedido #{view['numero']} ({view['estado']})."]
    if not view["items"]:
        lines.append("No tiene productos.")
    for item in view["items"]:
        entry = (
            f"- {item['nombre']} x{item['cantidad']} a {symbol} {item['precio_unitario']} "
            f"c/u = {symbol} {item['subtotal']}"
        )
        if item["selecciones"]:
            entry += f". Elegidos: {', '.join(item['selecciones'])}"
        if item["faltan_selecciones"]:
            entry += f". Faltan {item['faltan_selecciones']} por elegir"
        lines.append(entry)
    lines.append(f"Subtotal: {symbol} {view['subtotal']}")
    promotion = view["promocion"]
    if promotion is not None:
        lines.append(
            f"Descuento: {symbol} {view['descuento']} ({promotion['nombre']}, "
            f"ahorra {symbol} {promotion['ahorro']})"
        )
    else:
        lines.append(f"Descuento: {symbol} {view['descuento']}")
    lines.append(f"Envío: {symbol} {view['envio']}")
    lines.append(f"Total: {symbol} {view['total']}")
    if view["items"]:
        lines.append(
            "El pedido está completo."
            if view["completo"]
            else f"El pedido NO está completo: faltan {view['faltan_selecciones']} elecciones."
        )
    return "\n".join(lines)


# ------------------------------------------------------------------- helpers


def offers_of(context: ToolContext) -> dict[str, str]:
    """The closing promotions offered in this conversation, with their expiry (ISO).

    What was stored before the turn, with what this turn's tools added on top.
    """
    stored = {key: value.isoformat() for key, value in context.facts.promos_ofrecidas.items()}
    return {**stored, **dict(context.facts_patch.get("promos_ofrecidas", {}))}


def _uuid(raw: Any) -> uuid.UUID | None:
    try:
        return uuid.UUID(str(raw))
    except (ValueError, TypeError):
        return None


def _quantity(raw: Any, default: int | None) -> int | None:
    if raw is None or raw == "":
        return default
    if isinstance(raw, bool):
        return None
    try:
        number = float(raw)
    except (TypeError, ValueError):
        return None
    return int(number) if number == int(number) else None


def _names(raw: Any) -> list[str]:
    parts = _NAME_SEPARATORS.split(str(raw or ""))
    return [part.strip() for part in parts if part.strip()][:_MAX_SELECTIONS_PER_CALL]


class _Turn:
    """What every action of one call shares."""

    def __init__(self, context: ToolContext, conversation: Conversation) -> None:
        self.context = context
        self.conversation = conversation
        self.service = OrderService(context.session, context.tenant_id, offers=offers_of(context))
        self.symbol = currency_symbol(context.session, context.tenant_id)

    def result(
        self,
        status: str,
        order: Order | None,
        decir: str,
        *,
        extra: Mapping[str, Any] | None = None,
        lead: str | None = None,
    ) -> ToolResult:
        """The answer of an action: the whole order, or null, plus what to say."""
        view = None if order is None else order_view(self.service, order)
        text_parts = [lead] if lead else []
        text_parts.append(_NO_ORDER if view is None else order_text(view, self.symbol))
        data: dict[str, Any] = {"pedido": view, **(extra or {})}
        text = "\n".join(text_parts)
        if status == "hecho":
            return ToolResult.hecho(data=data, decir=decir, texto=text)
        if status == "omitido":
            return ToolResult("omitido", data, decir, text)
        return ToolResult.no_disponible(decir=decir, texto=text, data=data)

    def active(self) -> Order | None:
        return self.service.active_for(self.conversation.id)

    def draft(self) -> Order | None:
        order = self.active()
        return order if order is not None and order.estado == "borrador" else None


# ------------------------------------------------------------------- the tool


async def gestionar_pedido(context: ToolContext, arguments: dict[str, Any]) -> ToolResult:
    """Create or change the customer's order; always answers with the whole order."""
    if context.conversation_id is None:
        return ToolResult.error("No hay una conversación abierta para armar un pedido.")
    conversation = ConversationRepository(context.session, context.tenant_id).get(
        context.conversation_id
    )
    if conversation is None:
        return ToolResult.error("No se encontró la conversación.")

    turn = _Turn(context, conversation)
    action = str(arguments.get("accion") or "").strip()
    handler = _ACTIONS.get(action)
    if handler is None:
        return ToolResult.error(
            "accion no es válida: usa agregar, quitar, cantidad, elegir, ver, vaciar, "
            "aplicar_promocion, confirmar o solicitar_cambio."
        )
    try:
        return handler(turn, arguments)
    except OrderLocked:
        return turn.result("no_disponible", turn.active(), _LOCKED)
    except OrderLimit as exc:
        return turn.result(
            "no_disponible",
            turn.draft(),
            f"Este pedido admite como máximo {exc.limit} líneas. Dile al cliente que ya llegó "
            "al límite; no agregues más.",
        )
    except BadQuantity:
        return ToolResult.error("cantidad no es válida: debe estar entre 1 y 999.")
    except LineNotFound:
        return turn.result(
            "no_disponible",
            turn.active(),
            "Ese producto no está en el pedido. Revisa el pedido y usa un producto_id de la lista.",
        )
    except OrderError as exc:
        return turn.result("no_disponible", turn.active(), f"No se pudo: {exc}.")


# ------------------------------------------------------------------- actions


def _product_id(arguments: Mapping[str, Any]) -> uuid.UUID | None:
    return _uuid(arguments.get("producto_id"))


# Most products a name is matched against: a catalogue larger than this is searched, not scanned.
MAX_NAMED_PRODUCTS = 300


def _named_product(context: ToolContext, raw: str) -> Product | None:
    """The one active product `raw` names, when the model passed a name instead of the id.

    gpt-4.1-mini wrote the product's name (or a mangled id) in `producto_id`; the tool refused it
    and the reply still said «te agrego…» or «un momento». A name that names one product is it.
    """
    from app.agent.tools.quote import match_product  # quote imports the handover, not the order

    said = plain(raw)
    if not re.search(r"[a-z]{3}", said):
        return None
    products = ProductRepository(context.session, context.tenant_id).list_active(
        limit=MAX_NAMED_PRODUCTS
    )
    exact = [p for p in products if plain(p.name) == said]
    if len(exact) == 1:
        return exact[0]
    return match_product(raw, products)


def _bad_product_id() -> ToolResult:
    return ToolResult.error(
        "producto_id no es válido: usa el producto_id que devolvió buscar_productos."
    )


def _agregar(turn: _Turn, arguments: Mapping[str, Any]) -> ToolResult:
    context = turn.context
    product_id = _product_id(arguments)
    product = (
        ProductRepository(context.session, context.tenant_id).get_active(product_id)
        if product_id is not None
        else None
    )
    if product_id is None:
        # Not an id at all: a name. An id that names nothing is not looked up by its digits.
        product = _named_product(context, str(arguments.get("producto_id") or ""))
        if product is None:
            return _bad_product_id()
    quantity = _quantity(arguments.get("cantidad"), 1)
    if quantity is None or not 1 <= quantity <= MAX_QUANTITY:
        return ToolResult.error("cantidad no es válida: debe ser un número entre 1 y 999.")

    if product is None:
        return turn.result(
            "no_disponible",
            turn.active(),
            "Ese producto no existe o ya no está disponible. Búscalo con buscar_productos.",
        )
    try:
        turn.service.assert_sellable(product)
    except NotSellable:
        return turn.result(
            "no_disponible",
            turn.active(),
            f"{product.name} se cotiza, no tiene precio fijo: no lo agregues al pedido. "
            "Usa derivar_a_humano con motivo cotizacion y un resumen de lo que pide el cliente.",
        )

    options = context.options
    order = turn.service.open_draft(
        turn.conversation,
        moneda=currency_code(context.session, context.tenant_id),
        modalidad_pago=options.modalidad_pago if options is not None else "previo",
        is_sandbox=context.sandbox,
    )
    turn.service.add_product(order, product, quantity, max_lines=context.max_lines)
    says = (
        "El producto quedó en el pedido. Si en su mensaje pidió otros productos, agrégalos "
        "también ahora, sin preguntarle si los quiere. Después cuéntale lo que quedó, con los "
        "montos del último resultado y sin recalcular nada."
    )
    names = _names(arguments.get("seleccion"))
    if not (names and product.bundle_size):
        return turn.result("hecho", order, says)
    # The customer often names the themes together with the package: record them
    # in the same call instead of losing them.
    added, refusal = _select(turn, order, names, product.id)
    if refusal is not None:
        says += " " + _refusal_says(refusal)
    return turn.result("hecho", order, says, extra={"elegidos": added})


def _quitar(turn: _Turn, arguments: Mapping[str, Any]) -> ToolResult:
    order = turn.draft()
    if order is None:
        return turn.result(
            "no_disponible", turn.active(), "No hay un pedido en borrador para quitar."
        )
    product_id = _product_id(arguments)
    if product_id is None:
        return _bad_product_id()
    seleccion = str(arguments.get("seleccion") or "").strip()
    if seleccion:
        try:
            turn.service.remove_selection(order, seleccion, package_id=product_id)
        except SelectionRefused:
            return turn.result(
                "no_disponible", order, "Esa elección no está en el pedido. Revisa las elecciones."
            )
        return turn.result("hecho", order, "Quedó quitada esa elección del paquete.")
    turn.service.remove_product(order, product_id)
    return turn.result("hecho", order, "El producto salió del pedido.")


def _cantidad(turn: _Turn, arguments: Mapping[str, Any]) -> ToolResult:
    order = turn.draft()
    if order is None:
        return turn.result("no_disponible", turn.active(), "No hay un pedido en borrador.")
    product_id = _product_id(arguments)
    if product_id is None:
        return _bad_product_id()
    quantity = _quantity(arguments.get("cantidad"), None)
    if quantity is None:
        return ToolResult.error("cantidad no es válida: debe ser un número entero.")
    turn.service.set_quantity(order, product_id, quantity)
    return turn.result("hecho", order, "La cantidad quedó cambiada. Confírmala con el nuevo total.")


def _elegir(turn: _Turn, arguments: Mapping[str, Any]) -> ToolResult:
    order = turn.draft()
    if order is None:
        return turn.result(
            "no_disponible",
            turn.active(),
            "Todavía no hay un paquete en el pedido. Si el cliente ya dijo qué paquete quiere, "
            'agrégalo ahora con gestionar_pedido(accion="agregar", producto_id=<el del '
            "paquete>, seleccion=<sus temas>). No digas que los temas quedaron registrados.",
        )
    context = turn.context
    product_id = _product_id(arguments)
    names = _names(arguments.get("seleccion"))
    package_id = product_id
    if not names:
        # Without a theme, `producto_id` is the catalogue product being chosen.
        if product_id is None:
            return ToolResult.error("Falta seleccion: el tema o producto que elige el cliente.")
        chosen = ProductRepository(context.session, context.tenant_id).get_active(product_id)
        if chosen is None:
            return _bad_product_id()
        names, package_id = [chosen.name], None

    added, refusal = _select(turn, order, names, package_id)
    if not added and refusal is None:
        return turn.result("omitido", order, "Ya estaba elegido. No lo repitas; sigue adelante.")
    if not added and refusal is not None:
        return turn.result("no_disponible", order, _refusal_says(refusal))
    note = None if refusal is None else _refusal_says(refusal)
    return turn.result(
        "hecho",
        order,
        note
        or "Quedó registrado. Dile al cliente qué eligió y cuántos le faltan, según el pedido.",
        extra={"elegidos": added},
    )


def _select(
    turn: _Turn, order: Order, names: list[str], package_id: uuid.UUID | None
) -> tuple[list[str], SelectionRefused | None]:
    """Add each name to the package; a duplicate is skipped, a refusal stops."""
    added: list[str] = []
    for name in names:
        try:
            stored = turn.service.add_selection(order, name, package_id=package_id)
        except SelectionRefused as exc:
            if exc.code == "duplicate":
                continue
            return added, exc
        added.append(str(stored["nombre"]))
        # A free theme must not be mistaken for an invented product when the
        # model repeats it to the customer.
        turn.context.extra_names.append(str(stored["nombre"]))
    return added, None


def _refusal_says(refusal: SelectionRefused) -> str:
    if refusal.code == "full":
        return "El paquete ya tiene todas sus elecciones. No agregues más."
    if refusal.code == "not_in_catalog":
        options = ", ".join(refusal.suggestions)
        return (
            "Ese paquete solo admite productos del catálogo. Ofrece elegir entre: "
            f"{options or 'los productos del catálogo'}."
        )
    if refusal.code == "no_package":
        return "El pedido no tiene un paquete en el que elegir. Agrégalo primero."
    return f"No se pudo registrar la elección: {refusal}."


def _solicitar_cambio(turn: _Turn, arguments: Mapping[str, Any]) -> ToolResult:
    """Leave the customer's change on an order the business is reviewing (decision 8)."""
    order = turn.active()
    if order is None or order.estado != "por_confirmar":
        # Seen in the Milestone B run: after a rejection the model asked for a note
        # and then told the customer the order could not change. It can.
        return ToolResult.error(
            "El pedido no está en revisión y sí se puede cambiar: llama ahora a gestionar_pedido "
            "con la acción que corresponde (agregar, quitar, cambiar_cantidad). No le digas al "
            "cliente que no se puede."
        )
    note = " ".join(str(arguments.get("nota") or "").split())
    if not note:
        return ToolResult.error("nota está vacía: escribe qué quiere cambiar el cliente.")
    turn.service.add_note(order, note, origen="cliente")
    return turn.result(
        "hecho",
        order,
        "Quedó anotado para el negocio. Explícale al cliente que su pedido está en revisión y no "
        "se puede cambiar ahora; el negocio verá lo que pidió al revisarlo. No prometas el cambio.",
    )


def _ver(turn: _Turn, arguments: Mapping[str, Any]) -> ToolResult:
    order = turn.active()
    if order is None:
        return turn.result("hecho", None, "Todavía no hay pedido: ofrécele armarlo.")
    if order.estado == "borrador":
        # Prices may have moved since the last change (a promotion that expired).
        turn.service.recalculate(order)
    return turn.result("hecho", order, "Resume el pedido con los montos de este resultado.")


def _vaciar(turn: _Turn, arguments: Mapping[str, Any]) -> ToolResult:
    order = turn.draft()
    if order is None:
        return turn.result("no_disponible", turn.active(), "No hay un pedido en borrador.")
    turn.service.clear(order)
    return turn.result("hecho", order, "El pedido quedó vacío. Pregúntale qué quiere llevar.")


def _aplicar_promocion(turn: _Turn, arguments: Mapping[str, Any]) -> ToolResult:
    raw = str(arguments.get("promocion_id") or "").strip()
    context = turn.context
    service = turn.service
    now = service.now
    in_force = [
        p
        for p in PromotionRepository(context.session, context.tenant_id).in_force(now=now)
        if p.uso == "cierre"
    ]
    promotion_id = _uuid(raw)
    if promotion_id is not None:
        promotion = next((p for p in in_force if p.id == promotion_id), None)
    else:
        # The model sometimes passes the name it read in the ESTADO: an exact name of a
        # closing promotion of this business is unambiguous enough.
        named = [p for p in in_force if p.nombre.casefold() == raw.casefold()]
        if not raw or len(named) != 1:
            return ToolResult.error(
                "promocion_id no es válido: usa el promocion_id que muestra el ESTADO."
            )
        promotion = named[0]
    if promotion is None:
        return turn.result(
            "no_disponible",
            turn.active(),
            "Esa promoción no existe o ya no está vigente. No se la ofrezcas al cliente.",
        )

    order = turn.draft()
    lines = service.lines(order) if order is not None else []
    if order is not None and lines:
        categories = _categories(context, lines)
        priced = [
            PricingLine(
                item.product_id,
                categories.get(item.product_id),
                item.precio_unitario,
                item.cantidad,
            )
            for item in lines
        ]
        if not applies_to_order(Rule.from_promotion(promotion), priced, now):
            return turn.result(
                "no_disponible",
                order,
                f'La promoción "{promotion.nombre}" no aplica a este pedido (productos, monto '
                "mínimo o cantidad). No se la ofrezcas.",
            )

    offers = offers_of(context)
    key = str(promotion.id)
    if not offer_is_valid(offers, key, now):
        expiry = now + timedelta(hours=promotion.vigencia_horas or 0)
        offers[key] = expiry.isoformat()
    # Keep only the offers still alive: the stored JSON must not grow forever.
    alive = {k: v for k, v in offers.items() if offer_is_valid(offers, k, now)}
    context.facts_patch["promos_ofrecidas"] = alive
    hours = promotion.vigencia_horas or 0
    extra = {"promocion": promotion.nombre, "vigencia_horas": hours}

    if order is None or not lines:
        return turn.result(
            "hecho",
            order,
            f'La promoción "{promotion.nombre}" quedó ofrecida por {hours} horas. Se aplicará '
            "cuando arme su pedido; díselo así y no le inventes un monto.",
            extra=extra,
            lead=f'Promoción "{promotion.nombre}" ofrecida.',
        )

    service.with_offers(alive).reprice(order)
    applied = order.promotion_id == promotion.id
    decir = (
        "La promoción quedó aplicada. Comunica el nuevo total de este resultado."
        if applied
        else "Se aplicó otra promoción que le conviene más; las promociones no se acumulan. "
        "Comunica el total de este resultado."
    )
    return turn.result("hecho", order, decir, extra=extra)


def _categories(context: ToolContext, lines: Any) -> dict[uuid.UUID | None, str | None]:
    ids = {item.product_id for item in lines if item.product_id is not None}
    products = ProductRepository(context.session, context.tenant_id).by_ids(ids)
    return {pid: product.category for pid, product in products.items()}


def _confirmar(turn: _Turn, arguments: Mapping[str, Any]) -> ToolResult:
    context = turn.context
    order = turn.draft()
    if order is None or order.modalidad_pago != "contra_entrega":
        return turn.result(
            "no_disponible",
            turn.active(),
            "Confirmar solo aplica a un pedido contra entrega. Si el cliente ya pagó, una persona "
            "del negocio confirma el pago; no digas que quedó confirmado.",
        )
    if not turn.service.is_complete(order):
        return turn.result(
            "no_disponible", order, "El pedido todavía no está completo: termina de armarlo."
        )

    conversation = turn.conversation
    contact: Contact | None = (
        ContactRepository(context.session, context.tenant_id).get(conversation.contact_id)
        if conversation.contact_id is not None
        else None
    )
    if contact is None:
        return ToolResult.error("No se encontró al cliente de esta conversación.")
    missing = _missing(turn, order, contact)
    if missing:
        return turn.result(
            "no_disponible",
            order,
            "Faltan datos del cliente: "
            + ", ".join(missing)
            + ". Pídelos con registrar_datos_cliente.",
        )
    try:
        turn.service.submit(order, contact)
    except (NotReady, InvalidTransition):
        return turn.result("no_disponible", order, "El pedido no se puede confirmar todavía.")
    if context.notices is not None:
        # The business is told in the same transaction; the email goes out after it.
        context.notices.cash_order(order)
    return turn.result(
        "hecho",
        order,
        "El pedido quedó enviado al negocio, que lo confirmará. Dile al cliente que está en "
        "revisión: no digas que quedó confirmado.",
    )


def _missing(turn: _Turn, order: Order, contact: Contact) -> list[str]:
    options = turn.context.options
    return missing_customer_data(
        contact,
        options.datos_requeridos if options is not None else (),
        turn.service.entregas(),
        requiere_entrega=turn.service.requires_delivery(order),
        tipo_entrega=order.tipo_entrega,
        zona_envio=order.zona_envio,
    )


_ACTIONS = {
    "agregar": _agregar,
    "quitar": _quitar,
    "cantidad": _cantidad,
    "elegir": _elegir,
    "ver": _ver,
    "vaciar": _vaciar,
    "aplicar_promocion": _aplicar_promocion,
    "confirmar": _confirmar,
    "solicitar_cambio": _solicitar_cambio,
}

__all__ = ["gestionar_pedido", "offers_of", "order_text", "order_view"]
