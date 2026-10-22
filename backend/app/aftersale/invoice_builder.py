"""From an order to an electronic receipt: the amounts, and who it is for (feature 019, phase G).

Pure functions, no database, because this is where a mistake costs real money (it is a tax
document). The rules, on purpose simple and conservative:

* **Prices include the tax.** A shop quotes the price the customer pays. With the tax treatment
  *gravado* (18 % by default) the base of a line is its total divided by 1.18, rounded to the cent,
  and the tax is what is left; *exonerado* and *inafecto* have no tax.
* **The receipt adds up to the order.** Its total is the order's total, to the cent; if the lines of
  the order do not give that total the receipt is refused instead of issued approximately.
* **A discount of the order is a global discount of the receipt**, in base terms: the plug between
  the sum of the bases of the lines and the base of the total, so nothing is lost to rounding.
* **Shipping is a line** (a service) with the tax of the others.
* Only soles. A dollar receipt needs an exchange rate that the platform does not have.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from decimal import ROUND_HALF_UP, Decimal

from app.providers.invoicing.base import (
    Customer,
    DocumentType,
    InvoiceLine,
    InvoiceRequest,
    Kind,
    TaxKind,
    Unit,
)

CENT = Decimal("0.01")
TEN_PLACES = Decimal("0.0000000001")
# A boleta up to this amount may go to an unidentified buyer (tax authority rule for retail sales).
ANONYMOUS_LIMIT = Decimal("700")
MAX_DESCRIPTION = 250
ANONYMOUS_NAME = "CLIENTES VARIOS"
# The prefixes a RUC can start with: persons (10, 15, 16, 17) and companies (20).
RUC_PREFIXES = ("10", "15", "16", "17", "20")
_RUC_WEIGHTS = (5, 4, 3, 2, 7, 6, 5, 4, 3, 2)


class InvoiceBuildError(ValueError):
    """The order or the customer cannot make a receipt as they are. `code` is stable."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class SaleLine:
    """One line of the order, as it was sold: the price includes the tax."""

    name: str
    quantity: int
    total: Decimal
    unit: Unit = "NIU"
    code: str = "001"


def cents(value: Decimal) -> Decimal:
    return value.quantize(CENT, rounding=ROUND_HALF_UP)


def valid_ruc(number: str) -> bool:
    """11 digits, a known prefix and the check digit of the tax authority's modulo-11 rule."""
    if len(number) != 11 or not number.isdigit() or number[:2] not in RUC_PREFIXES:
        return False
    total = sum(int(d) * w for d, w in zip(number[:10], _RUC_WEIGHTS, strict=True))
    return (11 - total % 11) % 10 == int(number[10])


def valid_dni(number: str) -> bool:
    return len(number) == 8 and number.isdigit()


def customer_for(
    kind: Kind,
    total: Decimal,
    *,
    document_type: DocumentType,
    document_number: str,
    name: str,
    address: str = "",
    email: str = "",
) -> Customer:
    """The buyer of the receipt, checked for what the kind of receipt asks."""
    number = document_number.strip()
    name = " ".join(name.split())
    if document_type == "ruc":
        if not valid_ruc(number):
            raise InvoiceBuildError("invalid_ruc", "El RUC no es válido: son 11 dígitos.")
        if not name:
            raise InvoiceBuildError("name_required", "Escribe la razón social del cliente.")
    elif kind == "factura":
        raise InvoiceBuildError("ruc_required", "Una factura se emite a un cliente con RUC.")
    elif document_type == "dni":
        if not valid_dni(number):
            raise InvoiceBuildError("invalid_dni", "El DNI no es válido: son 8 dígitos.")
        if not name:
            raise InvoiceBuildError("name_required", "Escribe el nombre del cliente.")
    else:
        if total > ANONYMOUS_LIMIT:
            raise InvoiceBuildError(
                "document_required",
                f"Una boleta de más de S/ {ANONYMOUS_LIMIT} necesita el DNI o el RUC del cliente.",
            )
        number, name = "-", name or ANONYMOUS_NAME
    return Customer(
        document_type=document_type,
        document_number=number,
        name=name[:200],
        address=" ".join(address.split())[:250],
        email=email.strip()[:100],
    )


def _line(sale: SaleLine, rate: Decimal, tax: TaxKind) -> InvoiceLine:
    total = cents(sale.total)
    base = cents(total / (1 + rate)) if rate else total
    quantity = Decimal(sale.quantity)
    return InvoiceLine(
        unit=sale.unit,
        code=sale.code,
        description=" ".join(sale.name.split())[:MAX_DESCRIPTION] or "Producto",
        quantity=quantity,
        unit_value=(base / quantity).quantize(TEN_PLACES, rounding=ROUND_HALF_UP),
        unit_price=(total / quantity).quantize(TEN_PLACES, rounding=ROUND_HALF_UP),
        subtotal=base,
        igv=total - base,
        total=total,
        tax=tax,
    )


def build_request(
    *,
    kind: Kind,
    series: str,
    number: int,
    issued_on: date,
    customer: Customer,
    sales: Sequence[SaleLine],
    shipping: Decimal,
    discount: Decimal,
    order_total: Decimal,
    tax: TaxKind,
    igv_percent: Decimal,
    note: str = "",
    unique_code: str = "",
) -> InvoiceRequest:
    if not sales:
        raise InvoiceBuildError("empty_order", "El pedido no tiene productos.")
    rate = igv_percent / 100 if tax == "gravado" else Decimal(0)
    sold = list(sales)
    if shipping > 0:
        sold.append(SaleLine("Envío", 1, shipping, unit="ZZ", code="ENVIO"))
    lines = tuple(_line(sale, rate, tax) for sale in sold)

    gross = sum((line.total for line in lines), Decimal(0))
    total = cents(gross - discount)
    if total != cents(order_total):
        raise InvoiceBuildError(
            "total_mismatch",
            "Los productos del pedido no suman su total: revisa el pedido antes de emitir.",
        )
    sum_base = sum((line.subtotal for line in lines), Decimal(0))
    sum_igv = sum((line.igv for line in lines), Decimal(0))
    if discount > 0:
        # The base of the total, and the global discount is whatever lies between the sum of the
        # bases and it: the cents of rounding stay inside it instead of being lost.
        base = cents(total / (1 + rate)) if rate else total
        igv = total - base
        global_discount = sum_base - base
    else:
        base, igv, global_discount = sum_base, sum_igv, Decimal(0)

    return InvoiceRequest(
        kind=kind,
        series=series,
        number=number,
        issued_on=issued_on,
        customer=customer,
        lines=lines,
        igv_percent=igv_percent,
        taxed=base if tax == "gravado" else Decimal(0),
        exonerated=base if tax == "exonerado" else Decimal(0),
        unaffected=base if tax == "inafecto" else Decimal(0),
        igv=igv,
        global_discount=global_discount,
        total=total,
        note=note[:250],
        unique_code=unique_code,
    )
