"""Returns, exchanges, warranty claims and complaints about an order already made.

The assistant **registers** a request with what the customer said and the photos they sent; the
business decides, because refunding is money. Nothing here moves money or stock. Every change of
state leaves an event with who did it, and the customer is told the decision in their own chat.

Rules, on purpose conservative:
* only an order that is a sale made (confirmed, in progress or completed) can have one;
* one open request of a kind per order, so asking twice finds the first;
* a customer may name what they send back, and it has to be in the order;
* the business's own policy (does it accept returns, in how many days) is checked by the *tool*,
  never here, so a person can still register an exception the customer phoned in.
"""

from __future__ import annotations

import unicodedata
import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from sqlalchemy.orm import Session

from app.models.aftersale import (
    MAX_DETAIL,
    MAX_RESOLUTION,
    RETURN_REASONS,
    RETURN_TYPES,
    OrderReturn,
)
from app.models.orders import Order, OrderItem
from app.orders.service import SALE_MADE
from app.repositories.aftersale import ReturnEventRepository, ReturnRepository

TYPE_LABELS: Mapping[str, str] = {
    "devolucion": "devolución",
    "cambio": "cambio",
    "garantia": "garantía",
    "reclamo": "reclamo",
}

TRANSITIONS: Mapping[str, frozenset[str]] = {
    "solicitada": frozenset({"aprobada", "rechazada", "cancelada"}),
    "aprobada": frozenset({"resuelta", "cancelada"}),
    "rechazada": frozenset(),
    "resuelta": frozenset(),
    "cancelada": frozenset(),
}
# Deciding needs to say what happens next (approve) or why not (reject): the customer reads it.
NEEDS_RESOLUTION = frozenset({"aprobada", "rechazada"})
MAX_ITEMS = 10


class ReturnError(Exception):
    """A rule of the returns refused the request. `code` is stable; the message is for people."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class Opened:
    """The request, and whether this call created it (False: it was already open)."""

    ret: OrderReturn
    created: bool


def _plain(text: str) -> str:
    folded = unicodedata.normalize("NFKD", text.casefold())
    return "".join(c for c in folded if not unicodedata.combining(c)).strip()


def pick_lines(
    lines: Sequence[OrderItem], wanted: Sequence[Mapping[str, Any]]
) -> list[dict[str, Any]]:
    """The order's lines the customer named, as `{nombre, cantidad, producto_id?}`.

    A name matches a line when one contains the other (accents and case aside); a quantity above
    what was bought is cut to it. Empty `wanted` means the whole order. A name that matches nothing
    is refused: the request would point at something the customer never bought.
    """
    if not wanted:
        return []
    picked: list[dict[str, Any]] = []
    for entry in wanted[:MAX_ITEMS]:
        name = _plain(str(entry.get("nombre") or entry.get("producto") or ""))
        product_id = str(entry.get("producto_id") or "")
        found = next(
            (
                line
                for line in lines
                if (product_id and str(line.product_id) == product_id)
                or (name and (name in _plain(line.nombre) or _plain(line.nombre) in name))
            ),
            None,
        )
        if found is None:
            raise ReturnError(
                "item_not_in_order",
                "Ese producto no está en el pedido: " + ", ".join(line.nombre for line in lines),
            )
        try:
            quantity = int(entry.get("cantidad") or found.cantidad)
        except (TypeError, ValueError):
            quantity = found.cantidad
        item: dict[str, Any] = {
            "nombre": found.nombre,
            "cantidad": max(1, min(quantity, found.cantidad)),
        }
        if found.product_id:
            item["producto_id"] = str(found.product_id)
        picked.append(item)
    return picked


class ReturnService:
    def __init__(self, session: Session, tenant_id: uuid.UUID) -> None:
        self._session = session
        self._tenant_id = tenant_id
        self._returns = ReturnRepository(session, tenant_id)
        self._events = ReturnEventRepository(session, tenant_id)

    def open(
        self,
        order: Order,
        *,
        tipo: str,
        motivo: str,
        detalle: str,
        items: Sequence[Mapping[str, Any]],
        lines: Sequence[OrderItem],
        evidencia: Sequence[Mapping[str, Any]] = (),
        actor: str,
        user_id: uuid.UUID | None = None,
        conversation_id: uuid.UUID | None = None,
    ) -> Opened:
        if order.tenant_id != self._tenant_id:  # pragma: no cover - the repositories scope it
            raise ReturnError("not_found", "El pedido no existe.")
        if tipo not in RETURN_TYPES:
            raise ReturnError("invalid_type", f"Tipo desconocido: {tipo}.")
        if motivo not in RETURN_REASONS:
            raise ReturnError("invalid_reason", f"Motivo desconocido: {motivo}.")
        if order.estado not in SALE_MADE:
            raise ReturnError(
                "order_not_sold", "Solo un pedido confirmado puede tener una solicitud."
            )
        existing = self._returns.open_for(order.id, tipo)
        if existing is not None:
            return Opened(existing, created=False)
        ret = self._returns.add_numbered(
            order_id=order.id,
            contact_id=order.contact_id,
            conversation_id=conversation_id or order.conversation_id,
            tipo=tipo,
            motivo=motivo,
            detalle=(detalle or "").strip()[:MAX_DETAIL],
            items=pick_lines(lines, items),
            evidencia=[dict(e) for e in evidencia][:10],
            estado="solicitada",
            created_by=actor,
        )
        self._event(ret, None, "solicitada", actor, user_id)
        return Opened(ret, created=True)

    def decide(
        self,
        ret: OrderReturn,
        *,
        to: str,
        user_id: uuid.UUID,
        resolucion: str | None = None,
    ) -> OrderReturn:
        """Move a request along. The caller holds the row (`get_for_update`)."""
        if to not in TRANSITIONS.get(ret.estado, frozenset()):
            raise ReturnError(
                "invalid_transition", f"Una solicitud {ret.estado} no puede pasar a {to}."
            )
        text = (resolucion or "").strip()
        if to in NEEDS_RESOLUTION and len(text) < 3:
            raise ReturnError(
                "resolution_required",
                "Escribe qué pasará (o por qué no): el cliente lo va a leer.",
            )
        before = ret.estado
        ret.estado = to
        if text:
            ret.resolucion = text[:MAX_RESOLUTION]
        if to in ("rechazada", "resuelta", "cancelada"):
            ret.resuelta_por = user_id
            ret.resuelta_at = datetime.now(UTC)
        self._session.flush()
        self._event(ret, before, to, "user", user_id)
        return ret

    def _event(
        self,
        ret: OrderReturn,
        before: str | None,
        after: str,
        actor: str,
        user_id: uuid.UUID | None,
    ) -> None:
        # Stamped here, not by the database: `now()` is the start of the transaction, and a
        # request that opens and decides in one would leave two events with the same moment.
        self._events.add(
            created_at=datetime.now(UTC),
            return_id=ret.id,
            desde=before,
            hacia=after,
            actor=actor,
            user_id=user_id,
            detalle={"resolucion": ret.resolucion} if ret.resolucion else {},
        )


def customer_notice(ret: OrderReturn, order: Order, *, name: str | None) -> str | None:
    """What the customer is told when the business decides; None when there is nothing to tell."""
    label = TYPE_LABELS[ret.tipo]
    greeting = f"Hola {name}: " if name else "Hola: "
    head = f"{greeting}sobre tu solicitud de {label} #{ret.numero} (pedido #{order.numero}), "
    detail = f" {ret.resolucion}" if ret.resolucion else ""
    if ret.estado == "aprobada":
        return f"{head}la aprobamos.{detail}"
    if ret.estado == "rechazada":
        return f"{head}no pudimos aprobarla.{detail}"
    if ret.estado == "resuelta":
        return f"{head}ya quedó resuelta.{detail}"
    return None
