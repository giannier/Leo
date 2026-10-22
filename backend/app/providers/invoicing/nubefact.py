"""The Nubefact adapter: issue, void and look up receipts through its JSON API.

Plain `httpx`, like the other adapters: the payloads are ordinary JSON.

Wire format, verified against Nubefact's own example files and its PHP example code (downloaded
from nubefact.com/integracion, 2026-10-21); the manual itself is a Google Doc behind a sign-in
that could not be read, so what only the manual says is marked **unverified** below and surfaces
as the provider's own error text if it is wrong:

* ``POST <route>`` with ``Authorization: Token token="<token>"`` and
  ``Content-Type: application/json``. The *route* (``https://…nubefact.com/api/v1/<uuid>``) and
  the *token* are the business's own, from its account (a demo account answers on
  ``demo.nubefact.com``).
* The body is one JSON object with ``operacion``: ``generar_comprobante``,
  ``generar_anulacion`` or ``consultar_comprobante``. ``tipo_de_comprobante`` is ``"1"``
  factura, ``"2"`` boleta, ``"3"`` nota de crédito. Dates are ``DD-MM-YYYY``; ``moneda`` is
  ``"1"`` soles, ``"2"`` dollars (with ``tipo_de_cambio``, not used here);
  ``cliente_tipo_de_documento`` is ``1`` DNI, ``6`` RUC; ``tipo_de_igv`` is ``"1"`` taxed,
  ``"8"`` exonerated, ``"9"`` unaffected; ``unidad_de_medida`` is ``NIU`` (goods) or ``ZZ``
  (services).
* The answer carries ``enlace``, ``aceptada_por_sunat``, ``sunat_description``, ``sunat_note``,
  ``sunat_responsecode``, ``sunat_soap_error``, ``pdf_zip_base64``, ``xml_zip_base64``,
  ``cdr_zip_base64``, ``cadena_para_codigo_qr`` and ``codigo_hash``; a refusal is
  ``{"errors": "<text>", "codigo": <n>}``.
* **Unverified:** the identification of a buyer without document (``"-"`` / ``"-"``) for a
  boleta under the threshold; the answer of ``consultar_comprobante`` for a receipt that does
  not exist.
"""

from __future__ import annotations

import base64
import io
import logging
import zipfile
from decimal import Decimal
from typing import Any
from urllib.parse import urlsplit

import httpx

from app.providers.invoicing.base import (
    Customer,
    InvoiceCredentials,
    InvoiceError,
    InvoiceLine,
    InvoiceRequest,
    Issued,
    Kind,
)

logger = logging.getLogger(__name__)

# The only places the route may point to: the business types it and our server calls it, so
# anything else would let a business make us call an address of its choosing.
ALLOWED_HOSTS = ("nubefact.com", "pse.pe")
TIMEOUT = httpx.Timeout(30.0, connect=10.0)

KIND_CODES: dict[str, str] = {"factura": "1", "boleta": "2"}
CUSTOMER_CODES: dict[str, str] = {"dni": "1", "ruc": "6", "ninguno": "-"}
TAX_CODES: dict[str, str] = {"gravado": "1", "exonerado": "8", "inafecto": "9"}
# The example files carry these generic product codes of the tax authority's catalogue.
PRODUCT_CODES: dict[str, str] = {"NIU": "10000000", "ZZ": "20000000"}


def check_route(route: str) -> str:
    """The route if it is an https address of the provider; raises `InvoiceError` otherwise."""
    try:
        parts = urlsplit(route.strip())
    except ValueError as exc:
        raise InvoiceError("invalid_route", "La ruta no es una dirección válida.") from exc
    host = (parts.hostname or "").lower()
    allowed = any(host == base or host.endswith(f".{base}") for base in ALLOWED_HOSTS)
    if (
        parts.scheme != "https"
        or not allowed
        or parts.username
        or parts.password
        or parts.port not in (None, 443)
    ):
        raise InvoiceError(
            "invalid_route",
            "La ruta debe ser la dirección https que te dio Nubefact (termina en nubefact.com).",
        )
    return parts.geturl()


def _number(value: Decimal, places: int = 2) -> str:
    """A decimal as the provider reads it: no exponent, trailing zeros trimmed past two places."""
    text = f"{value:.{places}f}"
    if places > 2:
        text = text.rstrip("0")
        if text.endswith("."):
            text += "00"
        elif len(text.split(".")[1]) < 2:
            text += "0"
    return text


def _blank(value: Decimal) -> str:
    return "" if value == 0 else _number(value)


def _line(line: InvoiceLine) -> dict[str, Any]:
    return {
        "unidad_de_medida": line.unit,
        "codigo": line.code,
        "descripcion": line.description,
        "cantidad": _number(line.quantity, 4),
        "valor_unitario": _number(line.unit_value, 10),
        "precio_unitario": _number(line.unit_price, 10),
        "descuento": "",
        "subtotal": _number(line.subtotal),
        "tipo_de_igv": TAX_CODES[line.tax],
        "igv": _number(line.igv),
        "total": _number(line.total),
        "anticipo_regularizacion": "false",
        "anticipo_documento_serie": "",
        "anticipo_documento_numero": "",
        "codigo_producto_sunat": PRODUCT_CODES[line.unit],
    }


def _customer(customer: Customer) -> dict[str, Any]:
    code = CUSTOMER_CODES[customer.document_type]
    return {
        "cliente_tipo_de_documento": code,
        "cliente_numero_de_documento": customer.document_number or "-",
        "cliente_denominacion": customer.name,
        "cliente_direccion": customer.address,
        "cliente_email": customer.email,
        "cliente_email_1": "",
        "cliente_email_2": "",
    }


def build_body(request: InvoiceRequest) -> dict[str, Any]:
    """The JSON of `generar_comprobante` for this request."""
    return {
        "operacion": "generar_comprobante",
        "tipo_de_comprobante": KIND_CODES[request.kind],
        "serie": request.series,
        "numero": str(request.number),
        "sunat_transaction": 1,
        **_customer(request.customer),
        "fecha_de_emision": request.issued_on.strftime("%d-%m-%Y"),
        "fecha_de_vencimiento": "",
        "moneda": "1",
        "tipo_de_cambio": "",
        "porcentaje_de_igv": _number(request.igv_percent),
        "descuento_global": _blank(request.global_discount),
        "total_descuento": _blank(request.global_discount),
        "total_anticipo": "",
        "total_gravada": _blank(request.taxed),
        "total_inafecta": _blank(request.unaffected),
        "total_exonerada": _blank(request.exonerated),
        "total_igv": _blank(request.igv),
        "total_gratuita": "",
        "total_otros_cargos": "",
        "total": _number(request.total),
        "percepcion_tipo": "",
        "percepcion_base_imponible": "",
        "total_percepcion": "",
        "total_incluido_percepcion": "",
        "detraccion": "false",
        "observaciones": request.note,
        "documento_que_se_modifica_tipo": "",
        "documento_que_se_modifica_serie": "",
        "documento_que_se_modifica_numero": "",
        "tipo_de_nota_de_credito": "",
        "tipo_de_nota_de_debito": "",
        "enviar_automaticamente_a_la_sunat": "true",
        "enviar_automaticamente_al_cliente": "false",
        "codigo_unico": request.unique_code,
        "condiciones_de_pago": "",
        "medio_de_pago": "",
        "placa_vehiculo": "",
        "orden_compra_servicio": "",
        "tabla_personalizada_codigo": "",
        "formato_de_pdf": "",
        "items": [_line(line) for line in request.lines],
    }


def pdf_of(answer: dict[str, Any]) -> bytes | None:
    """The receipt's PDF out of `pdf_zip_base64` (a zip holding the PDF), if the answer has it."""
    encoded = answer.get("pdf_zip_base64")
    if not isinstance(encoded, str) or not encoded.strip():
        return None
    try:
        raw = base64.b64decode(encoded, validate=False)
    except ValueError:
        return None
    if raw.startswith(b"%PDF"):
        return raw
    try:
        with zipfile.ZipFile(io.BytesIO(raw)) as archive:
            names = [n for n in archive.namelist() if n.lower().endswith(".pdf")]
            if not names:
                return None
            data = archive.read(names[0])
    except (zipfile.BadZipFile, ValueError):
        return None
    return data if data.startswith(b"%PDF") else None


def _issued(answer: dict[str, Any], fallback_series: str, fallback_number: int) -> Issued:
    accepted = answer.get("aceptada_por_sunat")
    try:
        number = int(answer.get("numero") or fallback_number)
    except (TypeError, ValueError):
        number = fallback_number
    return Issued(
        series=str(answer.get("serie") or fallback_series),
        number=number,
        link=str(answer.get("enlace") or ""),
        accepted=accepted if isinstance(accepted, bool) else None,
        tax_authority_message=str(answer.get("sunat_description") or ""),
        hash=str(answer.get("codigo_hash") or ""),
        qr=str(answer.get("cadena_para_codigo_qr") or ""),
        pdf=pdf_of(answer),
        raw={
            key: answer.get(key)
            for key in (
                "enlace",
                "aceptada_por_sunat",
                "sunat_description",
                "sunat_note",
                "sunat_responsecode",
                "sunat_soap_error",
            )
        },
    )


class NubefactProvider:
    """Calls the business's own Nubefact route with its own token."""

    def __init__(self, *, client: httpx.AsyncClient | None = None) -> None:
        self._client = client

    async def _post(self, credentials: InvoiceCredentials, body: dict[str, Any]) -> dict[str, Any]:
        route = check_route(credentials.route)
        headers = {
            "Authorization": f'Token token="{credentials.token}"',
            "Content-Type": "application/json",
        }
        try:
            if self._client is not None:
                response = await self._client.post(route, json=body, headers=headers)
            else:
                async with httpx.AsyncClient(timeout=TIMEOUT, follow_redirects=False) as client:
                    response = await client.post(route, json=body, headers=headers)
        except (httpx.ConnectTimeout, httpx.ConnectError) as exc:
            raise InvoiceError("unavailable", "No se pudo conectar con el proveedor.") from exc
        except httpx.HTTPError as exc:
            # The request may have gone out and been processed before the answer was lost.
            raise InvoiceError(
                "unavailable",
                "El proveedor no respondió a tiempo.",
                maybe_issued=body.get("operacion") == "generar_comprobante",
            ) from exc
        try:
            answer = response.json()
        except ValueError:
            answer = None
        if not isinstance(answer, dict):
            raise InvoiceError(
                f"http_{response.status_code}",
                "El proveedor contestó algo que no se entiende.",
            )
        if answer.get("errors"):
            raise InvoiceError(
                str(answer.get("codigo") or f"http_{response.status_code}"), str(answer["errors"])
            )
        if response.status_code >= 400:
            raise InvoiceError(f"http_{response.status_code}", "El proveedor rechazó la solicitud.")
        return answer

    async def issue(self, credentials: InvoiceCredentials, request: InvoiceRequest) -> Issued:
        answer = await self._post(credentials, build_body(request))
        return _issued(answer, request.series, request.number)

    async def void(
        self,
        credentials: InvoiceCredentials,
        *,
        kind: Kind,
        series: str,
        number: int,
        reason: str,
    ) -> None:
        await self._post(
            credentials,
            {
                "operacion": "generar_anulacion",
                "tipo_de_comprobante": KIND_CODES[kind],
                "serie": series,
                "numero": str(number),
                "motivo": reason,
                "codigo_unico": "",
            },
        )

    async def lookup(
        self, credentials: InvoiceCredentials, *, kind: Kind, series: str, number: int
    ) -> Issued | None:
        try:
            answer = await self._post(
                credentials,
                {
                    "operacion": "consultar_comprobante",
                    "tipo_de_comprobante": KIND_CODES[kind],
                    "serie": series,
                    "numero": str(number),
                },
            )
        except InvoiceError as exc:
            if exc.code.startswith("http_") or exc.code in ("unavailable", "invalid_route"):
                raise
            # Any refusal with a provider code on a lookup means it does not have that receipt (its
            # code for «does not exist» is not in the example files).
            return None
        return _issued(answer, series, number)
