"""How each item of the catalogue closes (feature 020, the universal process).

Only products and services are sold, and what the assistant does when the customer decides
depends on the item, not on a template per business:

* `pedido`: a fixed price goes into an order, then the customer's data and the payment;
* `cotizacion`: the assistant gathers what the business asks for and a person gives the price;
* `cita`: a bookable item gets a time from the agenda;
* `informar`: what a person puts together (a programme, a project) is explained and handed to an
  advisor.

Read from `modo_venta` and `agendable`, so the closing is stored once: a bookable item closes with
an appointment whatever its price, and `informar` wins over everything (nothing to order, quote or
book).
"""

from __future__ import annotations

from typing import Literal, Protocol

Closing = Literal["pedido", "cotizacion", "cita", "informar"]
CLOSINGS: tuple[Closing, ...] = ("pedido", "cotizacion", "cita", "informar")


class _Item(Protocol):
    modo_venta: str
    agendable: bool


def closing_of(item: _Item) -> Closing:
    """How `item` closes."""
    if item.modo_venta == "informar":
        return "informar"
    if item.agendable:
        return "cita"
    if item.modo_venta == "cotizacion":
        return "cotizacion"
    return "pedido"


def closings_of(items: list[_Item] | tuple[_Item, ...]) -> tuple[Closing, ...]:
    """The closings a catalogue uses, in a fixed order, each once."""
    used = {closing_of(item) for item in items}
    return tuple(c for c in CLOSINGS if c in used)
