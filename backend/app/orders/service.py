"""The order service: one active order per conversation (Feature 004, TASK-425).

`OrderService` is the only code that writes orders and their lines. The
`gestionar_pedido` tool, the proof pipeline and the operator's API all go through
it, so the rules live once:

* **The active order** of a conversation is the newest one that was not discarded
  (C-06). A confirmed one is finished business: adding something creates a *new*
  order. One waiting for the operator (`por_confirmar`) cannot be edited.
* **Numbering** is per tenant, from `tenant_sequences`, in one atomic statement
  (`OrderRepository.add_numbered`).
* **Transitions** follow `data-model.md` 0019. Anything else is `InvalidTransition`.
* **Prices never come from outside.** A line copies the name and the price of the
  catalogue product when it is added; totals are `pricing.price()`, recomputed
  after every change.
* **Any change of items or delivery turns `rechazo_vigente` off** (C-21): that is
  what lets a customer whose payment was rejected leave VERIFICACION.
* **The customer's data are copied into the order** when it moves to
  `por_confirmar`, so the operator reviews what was true at that moment.

The service does not know about the model, the stage or the facts: the closing
promotions the assistant offered (`promos_ofrecidas`) come in through the
constructor, as plain data, because they live in the conversation's state.
"""

from __future__ import annotations

import logging
import re
import uuid
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

from pydantic import ValidationError
from sqlalchemy.orm import Session

from app.models.business import Product
from app.models.channel import Contact
from app.models.conversation import Conversation
from app.models.orders import FULFILLMENT_STAGES, Order, OrderItem
from app.orders.pricing import Delivery, Line, Rule, Totales, Zone, cents, price
from app.process.inbound import location_of
from app.providers.whatsapp.base import InboundLocation
from app.repositories.base import TenantScopeError
from app.repositories.conversation import MessageRepository
from app.repositories.orders import OrderItemRepository, OrderRepository
from app.repositories.product import ProductRepository
from app.repositories.sales_config import BusinessInfoRepository, PromotionRepository
from app.schemas.business_info import Entregas

logger = logging.getLogger(__name__)

# `data-model.md` 0019: what an order may move to from each state.
TRANSITIONS: Mapping[str, frozenset[str]] = {
    "borrador": frozenset({"por_confirmar", "descartado"}),
    "por_confirmar": frozenset({"confirmado", "borrador", "descartado"}),
    "confirmado": frozenset({"en_curso", "descartado"}),
    "en_curso": frozenset({"completado", "descartado"}),
    "completado": frozenset(),
    "descartado": frozenset(),
}
# An order in one of these states is the sale already made (`pago_estado = confirmado`).
SALE_MADE = frozenset({"confirmado", "en_curso", "completado"})
# The states in which the business can still be working the order, and in which a pin the customer
# shares is for it (feature 016).
WORKABLE = frozenset({"confirmado", "en_curso"})
LOCATABLE = frozenset({"borrador", "por_confirmar", "confirmado", "en_curso"})
# A pin shared before the order was submitted still belongs to it for this long.
PIN_LIFETIME = timedelta(hours=12)

MAX_QUANTITY = 999
# `orders.notas`: how long one note may be and how many an order keeps.
MAX_NOTE_CHARS = 300
MAX_NOTES = 20
MAX_SELECTION_CHARS = 80
# How many catalogue names an error offers the model when a choice is not valid.
SUGGESTIONS = 10

_SPACES = re.compile(r"\s+")


# ------------------------------------------------------------------- errors


class OrderError(Exception):
    """Something the order rules refuse. The message is safe to show the model."""


class InvalidTransition(OrderError):
    """The order cannot move between these two states."""

    def __init__(self, source: str, target: str) -> None:
        super().__init__(f"an order cannot go from {source!r} to {target!r}")
        self.source = source
        self.target = target


class OrderLocked(OrderError):
    """The order is waiting for the operator; the customer cannot change it."""


class NotSellable(OrderError):
    """The product is quoted, not sold by the assistant (`modo_venta = cotizacion`)."""


class OrderLimit(OrderError):
    """The template limits how many lines an order may have."""

    def __init__(self, limit: int) -> None:
        super().__init__(f"this order admits at most {limit} line(s)")
        self.limit = limit


class LineNotFound(OrderError):
    """The product is not in the order."""


class BadQuantity(OrderError):
    """The quantity is outside 1..999."""


class SelectionRefused(OrderError):
    """A package selection that cannot be taken. `code` says why."""

    def __init__(self, code: str, message: str, suggestions: Sequence[str] = ()) -> None:
        super().__init__(message)
        self.code = code
        self.suggestions = tuple(suggestions)


class InvalidDelivery(OrderError):
    """The delivery choice is not one the business offers."""

    def __init__(self, message: str, valid_zones: Sequence[str] = ()) -> None:
        super().__init__(message)
        self.valid_zones = tuple(valid_zones)


class NotReady(OrderError):
    """The order is not complete enough for the step asked."""


class InvalidStage(OrderError):
    """The stage does not exist, or does not fit how this order is delivered."""


# ------------------------------------------------------------------ service


def clean_selection(raw: str) -> str:
    """A selection name on one line, trimmed and bounded."""
    return _SPACES.sub(" ", raw).strip()[:MAX_SELECTION_CHARS].strip()


def load_entregas(session: Session, tenant_id: uuid.UUID) -> Entregas | None:
    """The tenant's `entregas` section, or None if missing or no longer valid."""
    row = BusinessInfoRepository(session, tenant_id).section("entregas")
    if row is None:
        return None
    try:
        return Entregas.model_validate(row.data)
    except ValidationError:
        logger.warning("the entregas section is invalid", extra={"tenant_id": str(tenant_id)})
        return None


class OrderService:
    """Reads and writes the orders of one tenant."""

    def __init__(
        self,
        session: Session,
        tenant_id: uuid.UUID,
        *,
        offers: Mapping[str, Any] | None = None,
        now: datetime | None = None,
    ) -> None:
        self._session = session
        self._tenant_id = tenant_id
        self._orders = OrderRepository(session, tenant_id)
        self._items = OrderItemRepository(session, tenant_id)
        self._products = ProductRepository(session, tenant_id)
        # The closing promotions the assistant offered in this conversation, with
        # their expiry: the only thing pricing needs from the conversation.
        self._offers: Mapping[str, Any] = offers or {}
        self._fixed_now = now

    # ------------------------------------------------------------------ reads

    @property
    def now(self) -> datetime:
        return self._fixed_now or datetime.now(UTC)

    def with_offers(self, offers: Mapping[str, Any]) -> OrderService:
        """The same service, pricing against a different set of offers."""
        return OrderService(self._session, self._tenant_id, offers=offers, now=self._fixed_now)

    def active_for(self, conversation_id: uuid.UUID) -> Order | None:
        """The order the conversation is working on (C-06)."""
        return self._orders.active_for_conversation(conversation_id)

    def lines(self, order: Order) -> Sequence[OrderItem]:
        self._own(order)
        return self._items.for_order(order.id)

    def missing_selections(self, order: Order) -> int:
        """How many package selections the order still lacks."""
        return sum(
            max(0, (item.bundle_size or 0) * item.cantidad - len(item.bundle_selections))
            for item in self.lines(order)
            if item.bundle_size
        )

    def is_complete(self, order: Order) -> bool:
        """At least one line, and every package has all its selections."""
        return bool(self.lines(order)) and self.missing_selections(order) == 0

    def entregas(self) -> Entregas | None:
        return load_entregas(self._session, self._tenant_id)

    def add_note(self, order: Order, texto: str, *, origen: str, **extra: str) -> dict[str, str]:
        """Append a note for the operator (`orders.notas`), newest last, at most 20.

        `origen` is `cliente` (a change asked while the order was under review,
        decision 8) or `sistema` (an image that was not a receipt, P-1/D-4).
        """
        entry = {"en": self.now.isoformat(), "texto": texto[:MAX_NOTE_CHARS], "origen": origen}
        entry.update(extra)
        order.notas = [*(order.notas or []), entry][-MAX_NOTES:]
        self._session.flush()
        return entry

    def requires_delivery(self, order: Order) -> bool:
        """Whether the order needs delivery data: something physical in it and a
        business that offers delivery or pickup (C-22)."""
        entregas = self.entregas()
        if entregas is None or not (entregas.hace_delivery or entregas.recojo_en_tienda):
            return False
        lines = self.lines(order)
        products = self._products_of(lines)
        # A line whose product is gone counts as physical: asking is the safe side.
        return any(
            item.product_id is None
            or (product := products.get(item.product_id)) is None
            or not product.es_digital
            for item in lines
        )

    # ----------------------------------------------------------- the order

    def open_draft(
        self,
        conversation: Conversation,
        *,
        moneda: str,
        modalidad_pago: str,
        is_sandbox: bool,
    ) -> Order:
        """The draft the customer is building: the active one, or a new one.

        A confirmed order is closed business, so the customer who wants something
        else starts a new order (C-06). One waiting for the operator is locked.
        """
        self._own_conversation(conversation)
        active = self.active_for(conversation.id)
        if active is not None and active.estado == "borrador":
            return active
        if active is not None and active.estado == "por_confirmar":
            raise OrderLocked("the order is waiting for the business to review it")
        if conversation.contact_id is None:
            raise OrderError("the conversation has no contact to order for")
        return self._orders.add_numbered(
            conversation_id=conversation.id,
            contact_id=conversation.contact_id,
            moneda=moneda.upper()[:3],
            modalidad_pago=modalidad_pago,
            is_sandbox=is_sandbox,
        )

    def transition(
        self,
        order: Order,
        target: str,
        *,
        datos_cliente: Mapping[str, Any] | None = None,
        motivo: str | None = None,
        rechazo_vigente: bool = False,
        user_id: uuid.UUID | None = None,
    ) -> Order:
        """Move the order to `target`, or raise `InvalidTransition`.

        Side effects that belong to a state: `por_confirmar` stores the customer's
        data as they are now (and cannot be reached without them); `confirmado`
        stamps who and when; going back to `borrador` records why and whether a
        proof rejection is in force (`rechazo_vigente`, C-21).
        """
        self._own(order)
        if target not in TRANSITIONS.get(order.estado, frozenset()):
            raise InvalidTransition(order.estado, target)
        if target == "por_confirmar":
            if datos_cliente is None:
                raise NotReady("the customer's data are copied when the order is submitted")
            order.datos_cliente = dict(datos_cliente)
        elif target == "confirmado":
            order.confirmed_at = self.now
            order.confirmed_by = user_id
        elif target == "en_curso":
            # Starting the work is the first stage; a stage already set (a move forward from
            # `confirmado` through `set_stage`) is kept.
            if order.etapa is None:
                order.etapa = "preparando"
                order.etapa_at = self.now
        elif target == "completado":
            order.completado_at = self.now
            order.etapa = None
            order.etapa_at = self.now
        elif target == "descartado":
            order.etapa = None
        elif target == "borrador":
            order.motivo_rechazo = motivo
            order.rechazo_vigente = rechazo_vigente
        order.estado = target
        order.last_activity_at = self.now
        self._session.flush()
        return order

    def set_stage(self, order: Order, etapa: str) -> Order:
        """Where the business is in its work on a confirmed order (feature 016).

        From `confirmado` the first stage starts the work (`en_curso`). Any stage may follow any
        other, because an operator taps the wrong one; only `en_camino` is bound to how the
        order is delivered: a pickup is never on its way.
        """
        self._own(order)
        if etapa not in FULFILLMENT_STAGES:
            raise InvalidStage(f"{etapa!r} is not a stage of the work")
        if order.estado not in WORKABLE:
            raise InvalidTransition(order.estado, "en_curso")
        if etapa == "en_camino" and order.tipo_entrega != "delivery":
            raise InvalidStage("only an order that is delivered can be on its way")
        if order.estado == "confirmado":
            self.transition(order, "en_curso")
        if order.etapa != etapa:
            order.etapa = etapa
            order.etapa_at = self.now
        order.last_activity_at = self.now
        self._session.flush()
        return order

    def attach_location(self, order: Order, location: Mapping[str, Any]) -> bool:
        """Keep the pin the customer shared for the order it is about; False if it is closed."""
        self._own(order)
        if order.estado not in LOCATABLE:
            return False
        order.ubicacion_entrega = dict(location)
        self._session.flush()
        return True

    def submit(self, order: Order, contact: Contact) -> Order:
        """`borrador` -> `por_confirmar` with a snapshot of the customer's data.

        The order has to be complete: the customer does not hand the operator an
        order that still lacks selections.
        """
        if order.estado == "borrador" and not self.is_complete(order):
            raise NotReady("the order is not complete")
        if order.ubicacion_entrega is None:
            self._adopt_recent_pin(order)
        return self.transition(order, "por_confirmar", datos_cliente=customer_snapshot(contact))

    def _adopt_recent_pin(self, order: Order) -> None:
        """The customer sent the pin before the order existed: it is theirs if it is recent."""
        pins = MessageRepository(self._session, self._tenant_id).latest_location(
            order.conversation_id, since=self.now - PIN_LIFETIME
        )
        found = location_of(pins.payload) if pins is not None else None
        if found is not None and pins is not None:
            order.ubicacion_entrega = pin_of(found, pins.created_at)

    # ----------------------------------------------------------------- lines

    def assert_sellable(self, product: Product) -> None:
        """Raise unless the assistant may put this product in an order.

        It must be this tenant's, and sold at a fixed price: a quoted product's
        price is the operator's to give, so the assistant hands the customer over
        instead of adding it.
        """
        if product.tenant_id != self._tenant_id:
            raise TenantScopeError("the product belongs to another tenant")
        if product.modo_venta == "cotizacion":
            raise NotSellable(f"{product.name} is quoted, not sold at a fixed price")

    def add_product(
        self,
        order: Order,
        product: Product,
        quantity: int = 1,
        *,
        max_lines: int | None = None,
    ) -> OrderItem:
        """Add `quantity` of a product, at the catalogue's price.

        The same product twice is one line with the quantities added. Quoted
        products are refused: their price is the operator's to give.
        """
        self._require_draft(order)
        self.assert_sellable(product)
        _check_quantity(quantity)

        lines = list(self._items.for_order(order.id))
        existing = next((item for item in lines if item.product_id == product.id), None)
        if existing is not None:
            _check_quantity(existing.cantidad + quantity)
            existing.cantidad += quantity
            existing.subtotal = cents(existing.precio_unitario * existing.cantidad)
            item = existing
        else:
            if max_lines is not None and len(lines) >= max_lines:
                raise OrderLimit(max_lines)
            item = self._items.add(
                order_id=order.id,
                product_id=product.id,
                nombre=product.name,
                precio_unitario=cents(product.price),
                cantidad=quantity,
                bundle_size=product.bundle_size,
                bundle_selections=[],
                subtotal=cents(product.price * quantity),
                # `now()` ties inside one transaction, and the lines are read back in
                # the order they were added: two products added in one turn must not
                # swap places.
                created_at=self.now,
            )
        self._changed(order)
        return item

    def set_quantity(self, order: Order, product_id: uuid.UUID, quantity: int) -> OrderItem:
        """Change how many of a product the order has."""
        self._require_draft(order)
        _check_quantity(quantity)
        item = self._line(order, product_id)
        item.cantidad = quantity
        item.subtotal = cents(item.precio_unitario * quantity)
        if item.bundle_size:
            # Fewer packages hold fewer selections: the extras go.
            room = item.bundle_size * quantity
            item.bundle_selections = list(item.bundle_selections)[:room]
        self._changed(order)
        return item

    def remove_product(self, order: Order, product_id: uuid.UUID) -> None:
        """Take a product out of the order."""
        self._require_draft(order)
        item = self._line(order, product_id)
        self._items.delete(item.id)
        self._changed(order)

    def clear(self, order: Order) -> None:
        """Empty the order. It stays a draft, with no lines and no promotion."""
        self._require_draft(order)
        for item in self._items.for_order(order.id):
            self._items.delete(item.id)
        self._changed(order)

    # ------------------------------------------------------------ selections

    def add_selection(
        self, order: Order, name: str, *, package_id: uuid.UUID | None = None
    ) -> dict[str, Any]:
        """Add one selection to a package of the order.

        Which package: `package_id` if given, else the first one still incomplete.
        What counts: a catalogue product, or - only if the package allows free
        choices (`bundle_permite_libres`) - any name the customer said. Returns the
        stored selection.
        """
        self._require_draft(order)
        cleaned = clean_selection(name)
        if not cleaned:
            raise SelectionRefused("empty", "the selection is empty")

        item = self._package(order, package_id)
        selections = list(item.bundle_selections)
        capacity = (item.bundle_size or 0) * item.cantidad
        if len(selections) >= capacity:
            raise SelectionRefused("full", f"{item.nombre} already has all its selections")
        if any(str(s.get("nombre", "")).casefold() == cleaned.casefold() for s in selections):
            raise SelectionRefused("duplicate", f"{cleaned} was already chosen")

        chosen = self._resolve_selection(item, cleaned)
        item.bundle_selections = [*selections, chosen]
        self._changed(order)
        return chosen

    def remove_selection(
        self, order: Order, name: str, *, package_id: uuid.UUID | None = None
    ) -> None:
        """Take one selection out of a package."""
        self._require_draft(order)
        wanted = clean_selection(name).casefold()
        candidates = [
            item
            for item in self._items.for_order(order.id)
            if item.bundle_size and (package_id is None or item.product_id == package_id)
        ]
        for item in candidates:
            kept = [
                s for s in item.bundle_selections if str(s.get("nombre", "")).casefold() != wanted
            ]
            if len(kept) != len(item.bundle_selections):
                item.bundle_selections = kept
                self._changed(order)
                return
        raise SelectionRefused("not_chosen", f"{name} is not among the selections")

    # -------------------------------------------------------------- delivery

    def set_delivery(
        self, order: Order, *, tipo_entrega: str | None = None, zona: str | None = None
    ) -> Order:
        """Record how the order is delivered, checked against what the business offers.

        `recojo` clears the zone. A zone that is not one of the business's lists
        the valid ones. Shipping is recomputed.
        """
        self._require_draft(order)
        entregas = self.entregas()
        if entregas is None or not (entregas.hace_delivery or entregas.recojo_en_tienda):
            raise InvalidDelivery("the business does not offer delivery or pickup")

        # Everything is checked before anything is written: a refused zone must not
        # leave a half-applied choice behind.
        if tipo_entrega == "delivery" and not entregas.hace_delivery:
            raise InvalidDelivery("the business does not deliver; it offers pickup only")
        if tipo_entrega == "recojo" and not entregas.recojo_en_tienda:
            raise InvalidDelivery("the business does not offer pickup; it delivers")
        tipo = tipo_entrega or order.tipo_entrega

        new_zone = order.zona_envio
        if tipo == "recojo":
            new_zone = None
        elif zona is not None:
            if not entregas.zonas:
                # Nothing to choose from: the cost is arranged (envio 0).
                new_zone = None
            else:
                match = next(
                    (z for z in entregas.zonas if z.nombre.casefold() == zona.casefold()), None
                )
                if match is None:
                    raise InvalidDelivery(
                        f"{zona} is not a delivery zone", [z.nombre for z in entregas.zonas]
                    )
                new_zone = match.nombre

        if tipo_entrega is not None:
            order.tipo_entrega = tipo_entrega
        order.zona_envio = new_zone
        self._changed(order)
        return order

    # ---------------------------------------------------------------- totals

    def recalculate(self, order: Order) -> Totales:
        """Price the order again and store the result on it and on its lines.

        Called after every change. The promotion that wins (if any) is recorded
        with a snapshot of its name, type and value, because the owner may edit it
        later without that rewriting what the customer was promised.
        """
        self._own(order)
        lines = list(self._items.for_order(order.id))
        products = self._products_of(lines)
        priced = [
            Line(
                product_id=item.product_id,
                categoria=_category_of(item, products),
                precio_unitario=item.precio_unitario,
                cantidad=item.cantidad,
            )
            for item in lines
        ]
        for item in lines:
            item.subtotal = cents(item.precio_unitario * item.cantidad)

        rules = [
            Rule.from_promotion(promotion)
            for promotion in PromotionRepository(self._session, self._tenant_id).in_force(
                now=self.now
            )
        ]
        totals = price(priced, rules, self._offers, self.now, entrega=self._delivery_of(order))
        order.subtotal = totals.subtotal
        order.descuento = totals.descuento
        order.envio = totals.envio
        order.total = totals.total
        if totals.promocion is None:
            order.promotion_id = None
            order.promotion_snapshot = None
        else:
            order.promotion_id = uuid.UUID(totals.promocion.id)
            order.promotion_snapshot = totals.promocion.snapshot()
        self._session.flush()
        return totals

    def reprice(self, order: Order) -> Totales:
        """Price the order again as a change (a promotion was applied): the
        rejection is over and the order is alive, like after any edit."""
        self._require_draft(order)
        order.rechazo_vigente = False
        order.last_activity_at = self.now
        return self.recalculate(order)

    def customer_data_changed(self, order: Order) -> None:
        """The customer's data changed: a rejection no longer holds (C-21)."""
        self._own(order)
        order.rechazo_vigente = False
        order.last_activity_at = self.now
        self._session.flush()

    # ---------------------------------------------------------------- helpers

    def _changed(self, order: Order) -> None:
        """Common to every edit: the rejection is over, the order is alive, re-price."""
        order.rechazo_vigente = False
        order.last_activity_at = self.now
        self.recalculate(order)

    def _delivery_of(self, order: Order) -> Delivery | None:
        if order.tipo_entrega is None:
            return None
        entregas = self.entregas()
        if entregas is None:
            return None
        return Delivery(
            tipo="delivery" if order.tipo_entrega == "delivery" else "recojo",
            zona=order.zona_envio,
            zonas=[Zone(z.nombre, Decimal(z.costo)) for z in entregas.zonas],
            gratis_desde=entregas.envio_gratis_desde,
        )

    def _products_of(self, lines: Sequence[OrderItem]) -> dict[uuid.UUID, Product]:
        ids = {item.product_id for item in lines if item.product_id is not None}
        return self._products.by_ids(ids)

    def _line(self, order: Order, product_id: uuid.UUID) -> OrderItem:
        for item in self._items.for_order(order.id):
            if item.product_id == product_id:
                return item
        raise LineNotFound("that product is not in the order")

    def _package(self, order: Order, package_id: uuid.UUID | None) -> OrderItem:
        packages = [item for item in self._items.for_order(order.id) if item.bundle_size]
        if package_id is not None:
            packages = [item for item in packages if item.product_id == package_id]
        if not packages:
            raise SelectionRefused("no_package", "the order has no package to choose for")
        pending = [
            item
            for item in packages
            if len(item.bundle_selections) < (item.bundle_size or 0) * item.cantidad
        ]
        return (pending or packages)[0]

    def _resolve_selection(self, package: OrderItem, name: str) -> dict[str, Any]:
        """The stored form of a selection: a catalogue product, or a free name."""
        matches = [
            product
            for product in self._products.search_active(name, limit=25)
            if product.name.casefold() == name.casefold() and not product.bundle_size
        ]
        if matches:
            return {"nombre": matches[0].name, "product_id": str(matches[0].id)}
        owner = self._products.get(package.product_id) if package.product_id else None
        if owner is not None and owner.bundle_permite_libres:
            return {"nombre": name, "product_id": None}
        options = [
            product.name
            for product in self._products.search_active("", limit=SUGGESTIONS + 1)
            if not product.bundle_size
        ][:SUGGESTIONS]
        raise SelectionRefused(
            "not_in_catalog",
            f"{name} is not in the catalogue and {package.nombre} only admits catalogue products",
            options,
        )

    def _require_draft(self, order: Order) -> None:
        self._own(order)
        if order.estado != "borrador":
            raise OrderLocked(f"the order is {order.estado}; only a draft can be changed")

    def _own(self, order: Order) -> None:
        if order.tenant_id != self._tenant_id:
            raise TenantScopeError(
                f"Order {order.id!s} belongs to tenant {order.tenant_id!s}, "
                f"not to {self._tenant_id!s}"
            )

    def _own_conversation(self, conversation: Conversation) -> None:
        if conversation.tenant_id != self._tenant_id:
            raise TenantScopeError(
                f"Conversation {conversation.id!s} belongs to tenant "
                f"{conversation.tenant_id!s}, not to {self._tenant_id!s}"
            )


def pin_of(location: InboundLocation, at: datetime) -> dict[str, Any]:
    """A shared pin as the order keeps it."""
    return {
        "lat": location.lat,
        "lng": location.lng,
        "nombre": location.nombre,
        "direccion": location.direccion,
        "en": at.isoformat(),
    }


def customer_snapshot(contact: Contact) -> dict[str, Any]:
    """The customer's data as they are now: what they told us plus the phone they write from."""
    data: dict[str, Any] = dict(contact.custom_data or {})
    if contact.phone_e164 and not data.get("telefono"):
        data["telefono"] = contact.phone_e164
    return data


def _category_of(item: OrderItem, products: Mapping[uuid.UUID, Product]) -> str | None:
    """The category of a line's product today (None when the product is gone)."""
    if item.product_id is None:
        return None
    product = products.get(item.product_id)
    return product.category if product is not None else None


def _check_quantity(quantity: int) -> None:
    if not 1 <= quantity <= MAX_QUANTITY:
        raise BadQuantity(f"the quantity must be between 1 and {MAX_QUANTITY}")
