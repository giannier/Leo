"""Issuing electronic receipts for an order through the business's own provider account.

Always from a person's click, never from the assistant: a receipt is a tax document and the
business answers for it. This module checks what can be checked before asking the provider (the
order is a sale made, in soles, with no receipt standing yet, and the buyer is valid for the kind
of receipt), takes the next number of the series **under a lock**, asks the provider, and keeps
the answer as a document of the order. The counter moves only when the provider accepts, so a
refusal gives the number back; if the answer is lost after the request went out, the number is
looked up before anything else is tried, so two receipts are never issued.
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlalchemy.orm import Session

from app.aftersale.documents import DocumentError, DocumentService
from app.aftersale.invoice_builder import (
    InvoiceBuildError,
    SaleLine,
    build_request,
    customer_for,
)
from app.aftersale.pdf_writer import SimplePdf
from app.core.crypto import CredentialDecryptionError, decrypt_secret
from app.models.aftersale import InvoicingSettings, OrderDocument
from app.models.business import Product
from app.models.orders import Order, OrderItem
from app.orders.service import SALE_MADE
from app.providers.invoicing.base import (
    DocumentType,
    InvoiceCredentials,
    InvoiceError,
    InvoiceProvider,
    InvoiceRequest,
    Issued,
    Kind,
)
from app.providers.invoicing.nubefact import check_route
from app.providers.storage.base import StorageProvider
from app.repositories.aftersale import DocumentRepository, InvoicingSettingsRepository
from app.repositories.business import BusinessProfileRepository

logger = logging.getLogger(__name__)

MIN_VOID_REASON = 5
MAX_VOID_REASON = 200


class InvoicingError(Exception):
    """A rule of the receipts refused it. `code` is stable; the message is for the person."""

    def __init__(self, code: str, message: str, *, provider_code: str | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.provider_code = provider_code


@dataclass(frozen=True)
class BuyerInput:
    document_type: DocumentType
    document_number: str
    name: str
    address: str = ""
    email: str = ""


def credentials_of(settings: InvoicingSettings) -> InvoiceCredentials:
    """The route and the (decrypted) token; raises `not_configured` if either is missing."""
    if not settings.ruta or not settings.token_enc:
        raise InvoicingError(
            "not_configured", "Conecta tu cuenta del proveedor en Facturación electrónica."
        )
    try:
        route = check_route(settings.ruta)
    except InvoiceError as exc:
        raise InvoicingError("invalid_route", str(exc)) from exc
    try:
        token = decrypt_secret(settings.token_enc)
    except CredentialDecryptionError as exc:
        raise InvoicingError(
            "not_configured", "El token guardado ya no se puede leer: vuelve a escribirlo."
        ) from exc
    return InvoiceCredentials(route=route, token=token)


def suggested_buyer(data: dict[str, Any] | None) -> BuyerInput:
    """The buyer the order's data suggests: RUC and company, else DNI and name, else none."""
    data = data or {}
    names = " ".join(str(data.get(k) or "").strip() for k in ("nombres", "apellidos")).strip()
    address = " ".join(
        str(data.get(k) or "").strip() for k in ("direccion", "distrito") if data.get(k)
    )
    email = str(data.get("correo") or data.get("email") or "")
    ruc, dni = str(data.get("ruc") or "").strip(), str(data.get("dni") or "").strip()
    if ruc:
        return BuyerInput("ruc", ruc, str(data.get("empresa") or names), address, email)
    if dni:
        return BuyerInput("dni", dni, names, address, email)
    return BuyerInput("ninguno", "", names, address, email)


def _today(session: Session, tenant_id: uuid.UUID) -> date:
    profile = BusinessProfileRepository(session, tenant_id).current()
    try:
        zone = ZoneInfo(profile.timezone if profile else "America/Lima")
    except ZoneInfoNotFoundError:
        zone = ZoneInfo("America/Lima")
    return datetime.now(UTC).astimezone(zone).date()


def _sales(session: Session, lines: Sequence[OrderItem]) -> list[SaleLine]:
    sales: list[SaleLine] = []
    for index, line in enumerate(lines, start=1):
        product = session.get(Product, line.product_id) if line.product_id else None
        service = product is not None and (
            product.agendable or product.es_digital or product.modo_venta == "cotizacion"
        )
        sales.append(
            SaleLine(
                name=line.nombre,
                quantity=line.cantidad,
                total=line.subtotal,
                unit="ZZ" if service else "NIU",
                code=f"{index:03d}",
            )
        )
    return sales


def stub_pdf(kind: str, series: str, number: int, link: str) -> bytes:
    """When the provider's answer carried no PDF: a page that says where the receipt is."""
    pdf = SimplePdf()
    pdf.title(f"{kind.capitalize()} electrónica {series}-{number}")
    pdf.text("El comprobante fue emitido por tu proveedor de facturación electrónica.")
    if link:
        pdf.text("Puedes verlo y descargarlo aquí:")
        pdf.text(link)
    return pdf.render()


class InvoicingService:
    def __init__(
        self,
        session: Session,
        tenant_id: uuid.UUID,
        storage: StorageProvider,
        provider: InvoiceProvider,
    ) -> None:
        self._session = session
        self._tenant_id = tenant_id
        self._storage = storage
        self._provider = provider
        self._settings = InvoicingSettingsRepository(session, tenant_id)
        self._documents = DocumentRepository(session, tenant_id)

    # ------------------------------------------------------------- checks

    def _check(
        self, order: Order, settings: InvoicingSettings, *, require_active: bool = True
    ) -> None:
        if require_active and not settings.activo:
            raise InvoicingError("disabled", "La facturación electrónica está apagada.")
        if order.is_sandbox:
            raise InvoicingError("sandbox", "Un pedido de prueba no se factura.")
        if order.estado not in SALE_MADE:
            raise InvoicingError("order_not_sold", "Solo se factura un pedido confirmado.")
        if order.moneda != "PEN":
            raise InvoicingError("only_soles", "Por ahora solo se emiten comprobantes en soles.")
        if self._documents.live_electronic(order.id) is not None:
            raise InvoicingError(
                "already_issued",
                "Este pedido ya tiene un comprobante electrónico: anúlalo primero.",
            )

    def _request(
        self,
        order: Order,
        lines: Sequence[OrderItem],
        settings: InvoicingSettings,
        kind: Kind,
        buyer: BuyerInput,
        number: int,
    ) -> InvoiceRequest:
        series = settings.serie_boleta if kind == "boleta" else settings.serie_factura
        try:
            customer = customer_for(
                kind,
                order.total,
                document_type=buyer.document_type,
                document_number=buyer.document_number,
                name=buyer.name,
                address=buyer.address,
                email=buyer.email,
            )
            return build_request(
                kind=kind,
                series=series,
                number=number,
                issued_on=_today(self._session, self._tenant_id),
                customer=customer,
                sales=_sales(self._session, lines),
                shipping=order.envio,
                discount=order.descuento,
                order_total=order.total,
                tax=settings.tratamiento_igv,  # type: ignore[arg-type]
                igv_percent=settings.igv_pct,
                note=f"Pedido #{order.numero}",
                unique_code=f"leo-{order.id}-{kind}",
            )
        except InvoiceBuildError as exc:
            raise InvoicingError(exc.code, str(exc)) from exc

    # ------------------------------------------------------------ preview

    def preview(
        self, order: Order, lines: Sequence[OrderItem], kind: Kind, buyer: BuyerInput
    ) -> InvoiceRequest:
        """What would be issued, with the next number; touches nothing."""
        settings = self._settings.ensure()
        # A preview is allowed while the account is still being connected.
        self._check(order, settings, require_active=False)
        number = settings.siguiente_boleta if kind == "boleta" else settings.siguiente_factura
        return self._request(order, lines, settings, kind, buyer, number)

    # -------------------------------------------------------------- issue

    async def issue(
        self,
        order: Order,
        lines: Sequence[OrderItem],
        kind: Kind,
        buyer: BuyerInput,
        *,
        user_id: uuid.UUID,
    ) -> OrderDocument:
        settings = self._settings.locked()
        self._check(order, settings)
        credentials = credentials_of(settings)
        number = settings.siguiente_boleta if kind == "boleta" else settings.siguiente_factura
        request = self._request(order, lines, settings, kind, buyer, number)

        try:
            issued = await self._provider.issue(credentials, request)
        except InvoiceError as exc:
            issued = await self._recover(credentials, request, exc)

        if kind == "boleta":
            settings.siguiente_boleta = number + 1
        else:
            settings.siguiente_factura = number + 1
        self._session.flush()
        return self._keep(order, request, issued, user_id)

    async def _recover(
        self, credentials: InvoiceCredentials, request: InvoiceRequest, error: InvoiceError
    ) -> Issued:
        """The provider refused, or the answer was lost: never issue twice, never lose one."""
        if error.maybe_issued:
            try:
                found = await self._provider.lookup(
                    credentials, kind=request.kind, series=request.series, number=request.number
                )
            except InvoiceError:
                found = None
            if found is not None:
                logger.warning(
                    "an answer was lost but the receipt exists; adopting it",
                    extra={"tenant_id": str(self._tenant_id), "number": request.number},
                )
                return found
            raise InvoicingError(
                "unknown_outcome",
                "No sabemos si el comprobante se emitió. Revisa tu cuenta del proveedor antes de "
                "volver a intentarlo.",
            ) from error
        raise InvoicingError("provider_refused", str(error), provider_code=error.code) from error

    def _keep(
        self, order: Order, request: InvoiceRequest, issued: Issued, user_id: uuid.UUID
    ) -> OrderDocument:
        pdf = issued.pdf or stub_pdf(request.kind, issued.series, issued.number, issued.link)
        try:
            return DocumentService(self._session, self._tenant_id, self._storage).attach(
                order,
                pdf,
                f"{issued.series}-{issued.number}.pdf",
                tipo=request.kind,
                titulo=f"{request.kind.capitalize()} electrónica",
                serie=issued.series,
                numero=str(issued.number),
                user_id=user_id,
                origen="electronico",
                proveedor={
                    "proveedor": "nubefact",
                    "enlace": issued.link,
                    "aceptada": issued.accepted,
                    "mensaje": issued.tax_authority_message,
                    "hash": issued.hash,
                    "qr": issued.qr,
                    "total": str(request.total),
                },
            )
        except DocumentError as exc:
            # The receipt exists at the provider: say so loudly, it must not be lost silently.
            logger.error(
                "a receipt was issued but could not be kept",
                extra={
                    "tenant_id": str(self._tenant_id),
                    "series": issued.series,
                    "number": issued.number,
                },
            )
            raise InvoicingError(
                "issued_not_saved",
                f"El comprobante {issued.series}-{issued.number} se emitió pero no se pudo guardar "
                f"aquí ({exc}). Búscalo en tu cuenta del proveedor.",
            ) from exc

    # --------------------------------------------------------------- void

    async def void(self, document: OrderDocument, reason: str) -> OrderDocument:
        if document.origen != "electronico" or document.tipo not in ("boleta", "factura"):
            raise InvoicingError("not_electronic", "Solo se anula aquí un comprobante electrónico.")
        if document.estado != "emitido":
            raise InvoicingError("already_void", "Ese comprobante ya está anulado.")
        text = " ".join(reason.split())
        if len(text) < MIN_VOID_REASON:
            raise InvoicingError("reason_required", "Escribe el motivo de la anulación.")
        settings = self._settings.locked()
        credentials = credentials_of(settings)
        try:
            await self._provider.void(
                credentials,
                kind=document.tipo,  # type: ignore[arg-type]
                series=document.serie or "",
                number=int(document.numero or 0),
                reason=text[:MAX_VOID_REASON],
            )
        except InvoiceError as exc:
            raise InvoicingError("provider_refused", str(exc), provider_code=exc.code) from exc
        document.estado = "anulado"
        document.proveedor = {
            **(document.proveedor or {}),
            "anulado": {"motivo": text[:MAX_VOID_REASON], "en": datetime.now(UTC).isoformat()},
        }
        self._session.flush()
        return document


def money(value: Decimal) -> str:
    return f"{value:.2f}"
