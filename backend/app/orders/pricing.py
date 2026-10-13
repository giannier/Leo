"""Prices an order: promotions and shipping (Feature 004, TASK-424, C-10).

The model never calculates money. Every total the assistant quotes comes from
`price()`, a pure function of the order's lines, the promotions in force, the
closing promotions that were offered in this conversation, the clock and the
delivery rules. No session, no clock of its own, no float: all amounts are
`Decimal`, rounded half-up to cents.

Rules, in the order they apply:

1. **One promotion, never two.** Every promotion that qualifies is worth some
   amount on this order; the one that saves the most is applied and the rest are
   ignored (C-10: promotions do not stack). A tie goes to the automatic one, then
   to the name, so the result never depends on the order of the input.
2. **Automatic promotions apply by themselves**; **closing promotions** (coupon,
   "I'll think about it", last follow-up) only if the assistant offered them and
   the offer has not expired (`promos_ofrecidas[id] > now`). An unoffered one and
   an expired one are worth nothing, however good they are.
3. **A promotion's scope** (everything, some products, some categories) decides
   which lines it works on, and so what it takes off. `monto_minimo` is compared
   with the subtotal of the *whole order*, which is what a customer understands by
   "minimum purchase": a "20 % off the courses" coupon with a minimum of S/ 100 is
   met by S/ 100 of anything (Giannier, Milestone B, decision 7).
4. **Quantity promotions work line by line.** "Take 3, pay 2" counts the units of
   one product; mixing two products never completes a group. It is the reading
   that can never cost the business more than the owner meant.
5. **Shipping** is added after the discount. Pickup and unknown zones cost
   nothing; a zone costs what the business configured; and an order whose goods,
   after the discount, reach `envio_gratis_desde` ships free.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from decimal import ROUND_HALF_UP, Decimal
from typing import Any, Literal

CENT = Decimal("0.01")
ZERO = Decimal("0.00")
HUNDRED = Decimal(100)


def cents(amount: Decimal) -> Decimal:
    """`amount` rounded half-up to two decimals."""
    return amount.quantize(CENT, rounding=ROUND_HALF_UP)


# ------------------------------------------------------------------- inputs


@dataclass(frozen=True)
class Line:
    """One order line as pricing sees it."""

    product_id: uuid.UUID | None
    categoria: str | None
    precio_unitario: Decimal
    cantidad: int

    @property
    def subtotal(self) -> Decimal:
        return cents(self.precio_unitario * self.cantidad)


@dataclass(frozen=True)
class Rule:
    """A promotion as pricing sees it: plain values, no session behind it."""

    id: str
    nombre: str
    uso: Literal["automatica", "cierre"]
    tipo: Literal["porcentaje", "monto_fijo", "nxm", "precio_cantidad"]
    valor: Decimal | None = None
    n: int | None = None
    m: int | None = None
    aplica_a: Literal["todo", "productos", "categorias"] = "todo"
    product_ids: frozenset[uuid.UUID] = frozenset()
    categorias: frozenset[str] = frozenset()
    monto_minimo: Decimal | None = None
    vigencia_horas: int | None = None
    inicia_en: datetime | None = None
    termina_en: datetime | None = None
    activa: bool = True

    @classmethod
    def from_promotion(cls, promotion: Any) -> Rule:
        """The rule of a `Promotion` row (or of any object with its columns)."""
        return cls(
            id=str(promotion.id),
            nombre=promotion.nombre,
            uso=promotion.uso or "automatica",
            tipo=promotion.tipo,
            valor=promotion.valor,
            n=promotion.n,
            m=promotion.m,
            aplica_a=promotion.aplica_a or "todo",
            product_ids=frozenset(promotion.product_ids or ()),
            categorias=frozenset(name.casefold() for name in (promotion.categorias or ())),
            monto_minimo=promotion.monto_minimo,
            vigencia_horas=promotion.vigencia_horas,
            inicia_en=promotion.inicia_en,
            termina_en=promotion.termina_en,
            activa=True if promotion.is_active is None else bool(promotion.is_active),
        )


@dataclass(frozen=True)
class Zone:
    nombre: str
    costo: Decimal


@dataclass(frozen=True)
class Delivery:
    """How the order reaches the customer, and what the business charges."""

    tipo: Literal["delivery", "recojo"] | None = None
    zona: str | None = None
    zonas: Sequence[Zone] = ()
    # Goods (after the discount) from this amount ship free.
    gratis_desde: Decimal | None = None

    def zone_cost(self) -> Decimal | None:
        """The cost of the chosen zone, or None if there is none to charge."""
        if self.zona is None:
            return None
        wanted = self.zona.casefold()
        for zone in self.zonas:
            if zone.nombre.casefold() == wanted:
                return zone.costo
        return None


# ------------------------------------------------------------------ outputs


@dataclass(frozen=True)
class Applied:
    """The promotion the order got, as it was when applied."""

    id: str
    nombre: str
    tipo: str
    valor: Decimal | None
    uso: str
    ahorro: Decimal

    def snapshot(self) -> dict[str, Any]:
        """What `orders.promotion_snapshot` keeps (JSON-safe)."""
        return {
            "nombre": self.nombre,
            "tipo": self.tipo,
            "valor": None if self.valor is None else str(self.valor),
            "uso": self.uso,
            "ahorro": str(self.ahorro),
        }


@dataclass(frozen=True)
class Totales:
    subtotal: Decimal
    descuento: Decimal
    envio: Decimal
    total: Decimal
    promocion: Applied | None = None
    # A positive shipping cost that the free-shipping threshold waived.
    envio_gratis: bool = False
    # Delivery was chosen but there is no price to charge (no zones configured).
    envio_a_coordinar: bool = False
    # Every promotion that qualified, best first (the first one is `promocion`).
    candidatas: tuple[Applied, ...] = field(default=())


# ---------------------------------------------------------------- promotions


def in_window(rule: Rule, now: datetime) -> bool:
    """Whether the rule is active and `now` falls inside its dates."""
    if not rule.activa:
        return False
    if rule.inicia_en is not None and rule.inicia_en > now:
        return False
    return not (rule.termina_en is not None and rule.termina_en <= now)


def _covers(rule: Rule, line: Line) -> bool:
    if rule.aplica_a == "productos":
        return line.product_id is not None and line.product_id in rule.product_ids
    if rule.aplica_a == "categorias":
        return line.categoria is not None and line.categoria.casefold() in rule.categorias
    return True


def _saving_on(rule: Rule, lines: Sequence[Line], order_subtotal: Decimal) -> Decimal:
    """What `rule` takes off `lines` (already limited to its scope).

    `order_subtotal` is the whole order's, only used for the minimum amount.
    """
    eligible = sum((line.subtotal for line in lines), ZERO)
    if not lines or eligible <= 0:
        return ZERO
    if rule.monto_minimo is not None and order_subtotal < rule.monto_minimo:
        return ZERO

    saving = ZERO
    if rule.tipo == "porcentaje" and rule.valor is not None:
        saving = cents(eligible * rule.valor / HUNDRED)
    elif rule.tipo == "monto_fijo" and rule.valor is not None:
        saving = cents(rule.valor)
    elif rule.tipo == "nxm" and rule.n and rule.m and rule.n > rule.m >= 1:
        for line in lines:
            free_units = (line.cantidad // rule.n) * (rule.n - rule.m)
            saving += cents(line.precio_unitario * free_units)
    elif rule.tipo == "precio_cantidad" and rule.valor is not None and rule.m and rule.m >= 2:
        for line in lines:
            groups = line.cantidad // rule.m
            regular = cents(line.precio_unitario * rule.m)
            if groups and regular > rule.valor:
                saving += cents((regular - rule.valor) * groups)
    # Never more than the goods it works on.
    return min(max(saving, ZERO), eligible)


def saving(rule: Rule, lines: Sequence[Line]) -> Decimal:
    """What `rule` is worth on `lines`: zero when it does not qualify."""
    order_subtotal = sum((line.subtotal for line in lines), ZERO)
    return _saving_on(rule, [line for line in lines if _covers(rule, line)], order_subtotal)


def applies_to_order(rule: Rule, lines: Sequence[Line], now: datetime) -> bool:
    """Whether the rule would take something off this order right now.

    Dates, scope, minimum amount and quantity together. This is what filters the
    closing promotions the assistant may offer (an order of one unit is not
    offered "3 for 2"), and what `aplicar_promocion` checks before agreeing.
    """
    return in_window(rule, now) and saving(rule, lines) > 0


def _expiry(raw: Any) -> datetime | None:
    if isinstance(raw, datetime):
        return raw
    if isinstance(raw, str):
        try:
            return datetime.fromisoformat(raw)
        except ValueError:
            return None
    return None


def offer_is_valid(offered: Mapping[str, Any], rule_id: str, now: datetime) -> bool:
    """Whether the assistant offered this closing promotion and it has not expired."""
    expiry = _expiry(offered.get(rule_id))
    return expiry is not None and expiry > now


def _candidates(
    rules: Iterable[Rule], lines: Sequence[Line], offered: Mapping[str, Any], now: datetime
) -> list[Applied]:
    found: list[Applied] = []
    for rule in rules:
        if not in_window(rule, now):
            continue
        if rule.uso == "cierre" and not offer_is_valid(offered, rule.id, now):
            continue
        worth = saving(rule, lines)
        if worth > 0:
            found.append(
                Applied(
                    id=rule.id,
                    nombre=rule.nombre,
                    tipo=rule.tipo,
                    valor=rule.valor,
                    uso=rule.uso,
                    ahorro=worth,
                )
            )
    # Most saving first; on a tie the automatic one, then the name, then the id.
    found.sort(key=lambda a: (-a.ahorro, a.uso != "automatica", a.nombre.casefold(), a.id))
    return found


# ------------------------------------------------------------------ shipping


def shipping(delivery: Delivery | None, goods: Decimal) -> tuple[Decimal, bool, bool]:
    """`(cost, waived, to_coordinate)` for goods worth `goods` after the discount."""
    if delivery is None or delivery.tipo != "delivery":
        return ZERO, False, False
    cost = delivery.zone_cost()
    if cost is None:
        # No zone to charge: with zones configured the customer still has to pick
        # one (a missing datum, not a price); without zones it is "a coordinar".
        return ZERO, False, not delivery.zonas
    cost = cents(cost)
    if delivery.gratis_desde is not None and goods >= delivery.gratis_desde and cost > 0:
        return ZERO, True, False
    return cost, False, False


# --------------------------------------------------------------------- price


def price(
    items: Sequence[Line],
    promociones_vigentes: Iterable[Rule],
    promos_ofrecidas: Mapping[str, Any],
    now: datetime,
    *,
    entrega: Delivery | None = None,
) -> Totales:
    """The totals of an order.

    `promociones_vigentes` are the rules in force (the repository already
    filtered by date; `now` is checked again so a stale cache cannot sell an
    expired offer), `promos_ofrecidas` the closing promotions offered in this
    conversation with their expiry, and `entrega` the delivery choice and rules.
    """
    subtotal = cents(sum((line.subtotal for line in items), ZERO))
    candidates = _candidates(promociones_vigentes, items, promos_ofrecidas, now)
    best = candidates[0] if candidates else None
    discount = best.ahorro if best is not None else ZERO
    goods = subtotal - discount
    cost, waived, to_coordinate = shipping(entrega, goods)
    return Totales(
        subtotal=subtotal,
        descuento=discount,
        envio=cost,
        total=goods + cost,
        promocion=best,
        envio_gratis=waived,
        envio_a_coordinar=to_coordinate,
        candidatas=tuple(candidates),
    )
