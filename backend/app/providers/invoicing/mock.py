"""An in-memory provider for tests and for development without an account."""

from __future__ import annotations

from app.providers.invoicing.base import (
    InvoiceCredentials,
    InvoiceError,
    InvoiceRequest,
    Issued,
    Kind,
)


class MockInvoiceProvider:
    """Issues receipts into a dict. `fail` makes the next call raise that error."""

    def __init__(self) -> None:
        self.issued: dict[tuple[str, int], InvoiceRequest] = {}
        self.voided: list[tuple[str, int, str]] = []
        self.fail: InvoiceError | None = None
        self.pdf = b"%PDF-1.4\n% mock receipt\n"

    def _maybe_fail(self) -> None:
        if self.fail is not None:
            error, self.fail = self.fail, None
            raise error

    async def issue(self, credentials: InvoiceCredentials, request: InvoiceRequest) -> Issued:
        self._maybe_fail()
        key = (request.series, request.number)
        if key in self.issued:
            raise InvoiceError("23", "El comprobante ya existe.")
        self.issued[key] = request
        return self._answer(request.series, request.number)

    async def void(
        self,
        credentials: InvoiceCredentials,
        *,
        kind: Kind,
        series: str,
        number: int,
        reason: str,
    ) -> None:
        self._maybe_fail()
        if (series, number) not in self.issued:
            raise InvoiceError("24", "El comprobante no existe.")
        self.voided.append((series, number, reason))

    async def lookup(
        self, credentials: InvoiceCredentials, *, kind: Kind, series: str, number: int
    ) -> Issued | None:
        return self._answer(series, number) if (series, number) in self.issued else None

    def _answer(self, series: str, number: int) -> Issued:
        return Issued(
            series=series,
            number=number,
            link=f"https://demo.example/{series}-{number}",
            accepted=True,
            tax_authority_message="aceptado",
            hash="hash",
            qr="qr",
            pdf=self.pdf,
        )
