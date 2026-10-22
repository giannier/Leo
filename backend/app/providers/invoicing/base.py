"""The `InvoiceProvider` port: issue, void and look up an electronic receipt.

Provider-neutral on purpose. The business-side code (`aftersale/invoicing.py`) builds an
`InvoiceRequest` from an order; each adapter turns it into its provider's wire format. Amounts are
`Decimal`, never float, and the request is already complete and consistent (the lines add up to
the totals) when it gets here: an adapter translates, it does not do tax arithmetic.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from typing import Literal, Protocol

Kind = Literal["boleta", "factura"]
TaxKind = Literal["gravado", "exonerado", "inafecto"]
# Goods ("NIU") or a service ("ZZ"): the two units of measure of the tax authority's catalogue.
Unit = Literal["NIU", "ZZ"]
# DNI, RUC, or nothing ("-": the buyer of a small boleta is not identified).
DocumentType = Literal["dni", "ruc", "ninguno"]


class InvoiceError(Exception):
    """The provider refused or could not be reached.

    `code` is the provider's own error code when it sent one (or `unavailable` / `http_<status>`
    / `invalid_route` when the failure is ours). `maybe_issued` is True when the request may have
    reached the provider and been processed (a timeout after sending): the caller must look the
    number up before trying again, or it could issue two receipts.
    """

    def __init__(self, code: str, message: str, *, maybe_issued: bool = False) -> None:
        super().__init__(message)
        self.code = code
        self.maybe_issued = maybe_issued


@dataclass(frozen=True)
class InvoiceCredentials:
    """What the business got from its provider: the route to call and its token."""

    route: str
    token: str


@dataclass(frozen=True)
class Customer:
    document_type: DocumentType
    document_number: str
    name: str
    address: str = ""
    email: str = ""


@dataclass(frozen=True)
class InvoiceLine:
    unit: Unit
    code: str
    description: str
    quantity: Decimal
    # Without tax, and with it: the provider wants both.
    unit_value: Decimal
    unit_price: Decimal
    # The base of the line (without tax), its tax and its total.
    subtotal: Decimal
    igv: Decimal
    total: Decimal
    tax: TaxKind = "gravado"


@dataclass(frozen=True)
class InvoiceRequest:
    kind: Kind
    series: str
    number: int
    issued_on: date
    customer: Customer
    lines: tuple[InvoiceLine, ...]
    igv_percent: Decimal
    taxed: Decimal
    exonerated: Decimal
    unaffected: Decimal
    igv: Decimal
    global_discount: Decimal
    total: Decimal
    note: str = ""
    # Ours, to recognise the same receipt if the answer is lost and we ask again.
    unique_code: str = ""


@dataclass(frozen=True)
class Issued:
    """What the provider answered for a receipt that stands."""

    series: str
    number: int
    link: str = ""
    accepted: bool | None = None
    tax_authority_message: str = ""
    hash: str = ""
    qr: str = ""
    # The PDF of the receipt, when the answer carried it.
    pdf: bytes | None = None
    raw: dict[str, object] = field(default_factory=dict)


class InvoiceProvider(Protocol):
    async def issue(self, credentials: InvoiceCredentials, request: InvoiceRequest) -> Issued:
        """Issue the receipt. Raises `InvoiceError`."""
        ...

    async def void(
        self,
        credentials: InvoiceCredentials,
        *,
        kind: Kind,
        series: str,
        number: int,
        reason: str,
    ) -> None:
        """Ask the provider to void a receipt it issued. Raises `InvoiceError`."""
        ...

    async def lookup(
        self, credentials: InvoiceCredentials, *, kind: Kind, series: str, number: int
    ) -> Issued | None:
        """The receipt with that series and number if the provider has it; None if it has not."""
        ...
