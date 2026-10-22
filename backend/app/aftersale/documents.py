"""The documents of an order: boleta, factura, guía, warranty (feature 019, phase G).

The business attaches the PDF it already has (or Leo makes the order's summary), and when the
customer asks for it the assistant sends it — **only to the customer the order belongs to**. These
files live apart from the media library: that one is the business's catalogue, reachable by a key
the model can name; a customer's invoice must never be.

Nothing here issues a tax document. Electronic invoicing goes through a provider, apart
(`aftersale/invoicing.py`), and always from a person's click.
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Any

from sqlalchemy.orm import Session

from app.aftersale.pdf_writer import SimplePdf
from app.models.aftersale import DOCUMENT_TYPES, OrderDocument
from app.models.media import MediaAsset
from app.models.orders import Order, OrderItem
from app.providers.storage.base import StorageError, StorageProvider
from app.repositories.aftersale import DocumentRepository
from app.services.media_library import MediaRejected, check

logger = logging.getLogger(__name__)

TYPE_LABELS = {
    "boleta": "Boleta",
    "factura": "Factura",
    "guia": "Guía de remisión",
    "proforma": "Proforma",
    "garantia": "Garantía",
    "nota_credito": "Nota de crédito",
    "otro": "Documento",
}
# What a customer can ask for, in their words, by type.
MAX_DOCUMENTS_PER_ORDER = 20
MAX_TITLE = 120
MAX_SERIES = 20


class DocumentError(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def label_of(document: OrderDocument) -> str:
    """«Boleta B001-123», «Garantía»: how the document is named to the customer."""
    base = TYPE_LABELS.get(document.tipo, "Documento")
    number = "-".join(part for part in (document.serie, document.numero) if part)
    return f"{base} {number}".strip()


class DocumentService:
    def __init__(self, session: Session, tenant_id: uuid.UUID, storage: StorageProvider) -> None:
        self._session = session
        self._tenant_id = tenant_id
        self._storage = storage
        self._documents = DocumentRepository(session, tenant_id)

    def attach(
        self,
        order: Order,
        data: bytes,
        filename: str,
        *,
        tipo: str,
        titulo: str | None = None,
        serie: str | None = None,
        numero: str | None = None,
        user_id: uuid.UUID | None = None,
        origen: str = "subido",
        proveedor: dict[str, Any] | None = None,
    ) -> OrderDocument:
        if order.tenant_id != self._tenant_id:  # pragma: no cover - the repositories scope it
            raise DocumentError("not_found", "El pedido no existe.")
        if tipo not in DOCUMENT_TYPES:
            raise DocumentError("invalid_type", f"Tipo desconocido: {tipo}.")
        if len(self._documents.of_order(order.id)) >= MAX_DOCUMENTS_PER_ORDER:
            raise DocumentError("too_many", "Este pedido ya tiene demasiados documentos.")
        try:
            found = check(data)
        except MediaRejected as exc:
            raise DocumentError(exc.code, str(exc)) from exc
        if found.tipo not in ("documento", "imagen"):
            raise DocumentError("unsupported_file", "Sube un PDF o una imagen JPG o PNG.")
        key = f"tenants/{self._tenant_id}/orders/{order.id}/{uuid.uuid4()}.{found.extension}"
        try:
            self._storage.put(key, data, content_type=found.mime_type)
        except StorageError as exc:
            raise DocumentError("storage_unavailable", "No se pudo guardar el archivo.") from exc
        return self._documents.add(
            order_id=order.id,
            tipo=tipo,
            titulo=(titulo or TYPE_LABELS[tipo]).strip()[:MAX_TITLE],
            serie=(serie or "").strip()[:MAX_SERIES] or None,
            numero=(numero or "").strip()[:MAX_SERIES] or None,
            storage_key=key,
            filename=filename[:200] or f"documento.{found.extension}",
            mime_type=found.mime_type,
            size_bytes=len(data),
            origen=origen,
            proveedor=proveedor or {},
            subido_por=user_id,
        )

    def remove(self, document: OrderDocument) -> None:
        """Delete a file the business uploaded by mistake. What a provider issued is only voided."""
        if document.origen == "electronico":
            raise DocumentError("issued", "Un comprobante electrónico no se borra: se anula.")
        key = document.storage_key
        self._session.delete(document)
        self._session.flush()
        try:
            self._storage.delete(key)
        except StorageError:
            logger.warning("could not delete the file of a removed document")

    def void(self, document: OrderDocument) -> OrderDocument:
        document.estado = "anulado"
        self._session.flush()
        return document

    def link(self, document: OrderDocument, *, seconds: int = 600) -> str:
        try:
            return self._storage.presigned_url(document.storage_key, expires_in=seconds)
        except StorageError as exc:
            raise DocumentError("storage_unavailable", "No se pudo abrir el archivo.") from exc

    # -------------------------------------------------------------- sending

    @staticmethod
    def as_asset(document: OrderDocument, order: Order) -> MediaAsset:
        """The file as the outbound adapters send it. **Never added to the session**: it is not a
        library file, it only borrows the shape the senders already know."""
        asset = MediaAsset(
            id=document.id,
            tenant_id=document.tenant_id,
            clave="documento_pedido",
            tipo="documento" if document.mime_type == "application/pdf" else "imagen",
            titulo=label_of(document),
            cuando_usar="Documento del pedido",
            caption=f"{label_of(document)} · pedido #{order.numero}"[:1000],
            storage_key=document.storage_key,
            filename=document.filename,
            mime_type=document.mime_type,
            size_bytes=document.size_bytes,
            provider_media_id=document.provider_media_id,
            provider_media_expires_at=document.provider_media_expires_at,
            is_active=True,
        )
        return asset

    # -------------------------------------------------------------- summary

    def make_summary(
        self,
        order: Order,
        lines: Sequence[OrderItem],
        *,
        business: str,
        customer: str | None,
        user_id: uuid.UUID | None,
    ) -> OrderDocument:
        """A PDF with what the customer bought and paid. Not a tax document, and it says so."""
        data = summary_pdf(order, lines, business=business, customer=customer)
        return self.attach(
            order,
            data,
            f"pedido-{order.numero}.pdf",
            tipo="otro",
            titulo=f"Resumen del pedido #{order.numero}",
            user_id=user_id,
            origen="generado",
        )


def mark_sent(session: Session, document: OrderDocument, asset: MediaAsset) -> None:
    """Remember the id WhatsApp gave the upload, and when the customer was sent the document."""
    document.provider_media_id = asset.provider_media_id
    document.provider_media_expires_at = asset.provider_media_expires_at
    document.enviado_at = datetime.now(UTC)
    session.flush()


def _money(order: Order, amount: Any) -> str:
    symbol = {"PEN": "S/", "USD": "US$"}.get(order.moneda, order.moneda)
    return f"{symbol} {amount:.2f}"


def summary_pdf(
    order: Order, lines: Sequence[OrderItem], *, business: str, customer: str | None
) -> bytes:
    pdf = SimplePdf()
    pdf.title(f"Resumen del pedido #{order.numero}")
    pdf.text(business, bold=True)
    pdf.text(f"Fecha: {order.created_at:%d/%m/%Y}")
    if customer:
        pdf.text(f"Cliente: {customer}")
    pdf.gap()
    pdf.heading("Detalle")
    for line in lines:
        pdf.text(f"{line.cantidad} × {line.nombre} — {_money(order, line.subtotal)}")
    pdf.gap()
    if order.descuento:
        pdf.text(f"Descuento: -{_money(order, order.descuento)}")
    if order.envio:
        pdf.text(f"Envío: {_money(order, order.envio)}")
    pdf.text(f"Total: {_money(order, order.total)}", bold=True)
    if order.tipo_entrega:
        zone = f" a {order.zona_envio}" if order.zona_envio else ""
        pdf.text(f"Entrega: {order.tipo_entrega}{zone}")
    pdf.gap()
    pdf.text("Este resumen no es un comprobante de pago.")
    return pdf.render()
