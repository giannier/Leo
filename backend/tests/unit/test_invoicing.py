"""The amounts of an electronic receipt and the Nubefact adapter (feature 019, phase G).

The expected numbers are the ones of Nubefact's own example files (downloaded from
nubefact.com/integracion): a boleta of 708 with a base of 600 and a tax of 108, and the same with a
global discount of 300 that leaves a base of 300, a tax of 54 and a total of 354.
"""

from __future__ import annotations

import base64
import io
import json
import random
import zipfile
from datetime import date
from decimal import Decimal
from typing import Any

import httpx
import pytest

from app.aftersale.invoice_builder import (
    InvoiceBuildError,
    SaleLine,
    build_request,
    customer_for,
    valid_dni,
    valid_ruc,
)
from app.providers.invoicing.base import (
    Customer,
    InvoiceCredentials,
    InvoiceError,
)
from app.providers.invoicing.nubefact import (
    NubefactProvider,
    build_body,
    check_route,
    pdf_of,
)

D = Decimal
BUYER = Customer("dni", "41920371", "JORGE LOPEZ", "JIRON COLOR 211", "demo@gmail.com")
TODAY = date(2026, 9, 4)
OFFICIAL_TOP_LEVEL_KEYS = {
    "operacion", "tipo_de_comprobante", "serie", "numero", "sunat_transaction",
    "cliente_tipo_de_documento", "cliente_numero_de_documento", "cliente_denominacion",
    "cliente_direccion", "cliente_email", "cliente_email_1", "cliente_email_2",
    "fecha_de_emision", "fecha_de_vencimiento", "moneda", "tipo_de_cambio",
    "porcentaje_de_igv", "descuento_global", "total_descuento", "total_anticipo",
    "total_gravada", "total_inafecta", "total_exonerada", "total_igv", "total_gratuita",
    "total_otros_cargos", "total", "percepcion_tipo", "percepcion_base_imponible",
    "total_percepcion", "total_incluido_percepcion", "detraccion", "observaciones",
    "documento_que_se_modifica_tipo", "documento_que_se_modifica_serie",
    "documento_que_se_modifica_numero", "tipo_de_nota_de_credito", "tipo_de_nota_de_debito",
    "enviar_automaticamente_a_la_sunat", "enviar_automaticamente_al_cliente", "codigo_unico",
    "condiciones_de_pago", "medio_de_pago", "placa_vehiculo", "orden_compra_servicio",
    "tabla_personalizada_codigo", "formato_de_pdf", "items",
}  # fmt: skip
OFFICIAL_ITEM_KEYS = {
    "unidad_de_medida", "codigo", "descripcion", "cantidad", "valor_unitario", "precio_unitario",
    "descuento", "subtotal", "tipo_de_igv", "igv", "total", "anticipo_regularizacion",
    "anticipo_documento_serie", "anticipo_documento_numero", "codigo_producto_sunat",
}  # fmt: skip


def request(
    *, discount: str = "0", shipping: str = "0", lines: list[SaleLine] | None = None, **extra: Any
):  # type: ignore[no-untyped-def]
    sales = lines or [
        SaleLine("DETALLE DEL PRODUCTO", 1, D("590")),
        SaleLine("DETALLE DEL SERVICIO", 5, D("118"), unit="ZZ"),
    ]
    gross = sum((s.total for s in sales), D(0)) + D(shipping)
    return build_request(
        kind="boleta",
        series="BBB1",
        number=1,
        issued_on=TODAY,
        customer=BUYER,
        sales=sales,
        shipping=D(shipping),
        discount=D(discount),
        order_total=extra.pop("order_total", gross - D(discount)),
        tax=extra.pop("tax", "gravado"),
        igv_percent=D("18"),
        **extra,
    )


class TestTheAmounts:
    def test_it_reproduces_the_official_boleta_of_708(self) -> None:
        built = request()

        assert (built.taxed, built.igv, built.total, built.global_discount) == (
            D("600.00"), D("108.00"), D("708.00"), D("0"),
        )  # fmt: skip
        first, second = built.lines
        assert (first.unit_value, first.unit_price, first.subtotal, first.igv, first.total) == (
            D("500"), D("590"), D("500.00"), D("90.00"), D("590.00"),
        )  # fmt: skip
        assert (second.unit_value, second.unit_price, second.quantity) == (
            D("20"),
            D("23.6"),
            D("5"),
        )

    def test_it_reproduces_the_official_global_discount(self) -> None:
        built = request(discount="354")

        assert (built.taxed, built.igv, built.total, built.global_discount) == (
            D("300.00"), D("54.00"), D("354.00"), D("300.00"),
        )  # fmt: skip

    def test_shipping_is_a_service_line_with_the_tax_of_the_others(self) -> None:
        built = request(shipping="11.80")

        shipping = built.lines[-1]
        assert (shipping.description, shipping.unit, shipping.total) == ("Envío", "ZZ", D("11.80"))
        assert (shipping.subtotal, shipping.igv) == (D("10.00"), D("1.80"))
        assert built.total == D("719.80")

    def test_exonerated_and_unaffected_have_no_tax(self) -> None:
        exonerated = request(tax="exonerado")
        unaffected = request(tax="inafecto")

        assert (exonerated.exonerated, exonerated.taxed, exonerated.igv) == (
            D("708.00"),
            D(0),
            D(0),
        )
        assert (unaffected.unaffected, unaffected.taxed, unaffected.igv) == (
            D("708.00"),
            D(0),
            D(0),
        )
        assert all(line.igv == 0 and line.subtotal == line.total for line in exonerated.lines)

    def test_a_receipt_that_does_not_add_up_to_the_order_is_refused(self) -> None:
        with pytest.raises(InvoiceBuildError) as caught:
            request(order_total=D("700"))

        assert caught.value.code == "total_mismatch"

    def test_an_order_without_lines_is_refused(self) -> None:
        with pytest.raises(InvoiceBuildError) as caught:
            build_request(
                kind="boleta", series="B001", number=1, issued_on=TODAY, customer=BUYER, sales=[],
                shipping=D(0), discount=D(0), order_total=D(0), tax="gravado", igv_percent=D(18),
            )  # fmt: skip

        assert caught.value.code == "empty_order"

    def test_whatever_the_prices_the_receipt_adds_up_to_the_cent(self) -> None:
        rng = random.Random(7)
        for _ in range(500):
            sales = [
                SaleLine(f"P{i}", rng.randint(1, 9), D(rng.randint(1, 99999)) / 100)
                for i in range(rng.randint(1, 5))
            ]
            shipping = D(rng.choice([0, 5, 8, 12]))
            gross = sum((s.total.quantize(D("0.01")) for s in sales), D(0)) + shipping
            discount = D(rng.choice([0, 0, rng.randint(1, int(gross) or 1)]))
            built = request(lines=sales, shipping=str(shipping), discount=str(discount))

            assert built.total == gross - discount
            assert built.taxed + built.igv == built.total
            assert built.taxed + built.global_discount == sum(
                (line.subtotal for line in built.lines), D(0)
            )
            assert built.global_discount >= 0
            for line in built.lines:
                assert line.subtotal + line.igv == line.total


class TestTheBuyer:
    def test_the_ruc_of_the_official_example_is_valid_and_a_changed_digit_is_not(self) -> None:
        assert valid_ruc("20600695771")
        assert not valid_ruc("20600695772")
        assert not valid_ruc("2060069577")
        assert not valid_ruc("30600695771")  # no such prefix
        assert valid_dni("41920371") and not valid_dni("4192037") and not valid_dni("4192037a")

    def test_a_factura_needs_a_valid_ruc_and_a_name(self) -> None:
        with pytest.raises(InvoiceBuildError) as no_ruc:
            customer_for(
                "factura", D("100"), document_type="dni", document_number="41920371", name="Ana"
            )
        with pytest.raises(InvoiceBuildError) as bad_ruc:
            customer_for(
                "factura", D("100"), document_type="ruc", document_number="20600695772", name="X SA"
            )
        with pytest.raises(InvoiceBuildError) as nameless:
            customer_for(
                "factura", D("100"), document_type="ruc", document_number="20600695771", name=" "
            )

        assert (no_ruc.value.code, bad_ruc.value.code, nameless.value.code) == (
            "ruc_required", "invalid_ruc", "name_required",
        )  # fmt: skip

    def test_a_small_boleta_may_go_to_nobody_and_a_big_one_may_not(self) -> None:
        small = customer_for(
            "boleta", D("700"), document_type="ninguno", document_number="", name=""
        )
        with pytest.raises(InvoiceBuildError) as big:
            customer_for(
                "boleta", D("700.01"), document_type="ninguno", document_number="", name=""
            )

        assert (small.document_number, small.name) == ("-", "CLIENTES VARIOS")
        assert big.value.code == "document_required"

    def test_a_boleta_to_a_company_is_allowed(self) -> None:
        buyer = customer_for(
            "boleta",
            D("50"),
            document_type="ruc",
            document_number="20600695771",
            name="NUBEFACT SA",
        )

        assert buyer.document_type == "ruc"


class TestTheNubefactBody:
    def test_it_has_exactly_the_fields_of_the_official_example(self) -> None:
        body = build_body(request())

        assert set(body) == OFFICIAL_TOP_LEVEL_KEYS
        assert all(set(item) == OFFICIAL_ITEM_KEYS for item in body["items"])

    def test_it_says_what_the_official_example_says(self) -> None:
        body = build_body(request())

        assert body["operacion"] == "generar_comprobante"
        assert (body["tipo_de_comprobante"], body["serie"], body["numero"]) == ("2", "BBB1", "1")
        assert body["fecha_de_emision"] == "04-09-2026" and body["moneda"] == "1"
        assert body["cliente_tipo_de_documento"] == "1"
        assert (D(body["total_gravada"]), D(body["total_igv"]), D(body["total"])) == (
            D(600),
            D(108),
            D(708),
        )
        assert body["descuento_global"] == "" and body["porcentaje_de_igv"] == "18.00"
        first = body["items"][0]
        assert first["unidad_de_medida"] == "NIU" and first["tipo_de_igv"] == "1"
        assert first["codigo_producto_sunat"] == "10000000"
        assert (D(first["valor_unitario"]), D(first["precio_unitario"])) == (D(500), D(590))
        assert body["items"][1]["unidad_de_medida"] == "ZZ"
        assert body["items"][1]["codigo_producto_sunat"] == "20000000"
        assert D(body["items"][1]["precio_unitario"]) == D("23.6")

    def test_a_factura_goes_to_a_ruc_and_a_global_discount_is_written(self) -> None:
        company = customer_for(
            "factura",
            D("354"),
            document_type="ruc",
            document_number="20600695771",
            name="NUBEFACT SA",
        )
        built = build_request(
            kind="factura", series="FFF1", number=3, issued_on=TODAY, customer=company,
            sales=[SaleLine("X", 1, D("708"))], shipping=D(0), discount=D("354"),
            order_total=D("354"), tax="gravado", igv_percent=D(18),
        )  # fmt: skip

        body = build_body(built)

        assert (body["tipo_de_comprobante"], body["cliente_tipo_de_documento"]) == ("1", "6")
        assert (D(body["descuento_global"]), D(body["total_descuento"])) == (D(300), D(300))
        assert (D(body["total_gravada"]), D(body["total_igv"]), D(body["total"])) == (
            D(300),
            D(54),
            D(354),
        )

    def test_exonerated_items_carry_the_exonerated_code(self) -> None:
        body = build_body(request(tax="exonerado"))

        assert {item["tipo_de_igv"] for item in body["items"]} == {"8"}
        assert D(body["total_exonerada"]) == D(708) and body["total_gravada"] == ""


def answer(**extra: Any) -> dict[str, Any]:
    return {
        "tipo_de_comprobante": 2, "serie": "BBB1", "numero": 1,
        "enlace": "https://www.nubefact.com/cpe/abc", "aceptada_por_sunat": True,
        "sunat_description": "La Boleta numero BBB1-1, ha sido aceptada", "sunat_note": None,
        "sunat_responsecode": "0", "sunat_soap_error": "", "pdf_zip_base64": None,
        "xml_zip_base64": None, "cdr_zip_base64": None, "cadena_para_codigo_qr": "qr",
        "codigo_hash": "hash", **extra,
    }  # fmt: skip


def provider(handler: Any) -> NubefactProvider:
    return NubefactProvider(client=httpx.AsyncClient(transport=httpx.MockTransport(handler)))


CREDENTIALS = InvoiceCredentials("https://demo.nubefact.com/api/v1/abc-123", "secret-token")


def zipped_pdf(content: bytes = b"%PDF-1.4 receipt") -> str:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("BBB1-1.pdf", content)
    return base64.b64encode(buffer.getvalue()).decode()


class TestTheNubefactCalls:
    async def test_it_posts_the_body_to_the_route_with_the_token(self) -> None:
        seen: list[httpx.Request] = []

        def handler(req: httpx.Request) -> httpx.Response:
            seen.append(req)
            return httpx.Response(200, json=answer(pdf_zip_base64=zipped_pdf()))

        issued = await provider(handler).issue(CREDENTIALS, request())

        (sent,) = seen
        assert sent.method == "POST" and str(sent.url) == CREDENTIALS.route
        assert sent.headers["authorization"] == 'Token token="secret-token"'
        assert sent.headers["content-type"] == "application/json"
        assert json.loads(sent.content)["operacion"] == "generar_comprobante"
        assert (issued.series, issued.number, issued.accepted) == ("BBB1", 1, True)
        assert issued.link.endswith("/cpe/abc") and issued.hash == "hash"
        assert issued.pdf == b"%PDF-1.4 receipt"

    async def test_a_refusal_carries_the_providers_code_and_text(self) -> None:
        def handler(req: httpx.Request) -> httpx.Response:
            return httpx.Response(400, json={"errors": "Documento ya existe", "codigo": 23})

        with pytest.raises(InvoiceError) as caught:
            await provider(handler).issue(CREDENTIALS, request())

        assert (caught.value.code, str(caught.value), caught.value.maybe_issued) == (
            "23", "Documento ya existe", False,
        )  # fmt: skip

    async def test_something_that_is_not_json_is_an_error_with_the_status(self) -> None:
        def handler(req: httpx.Request) -> httpx.Response:
            return httpx.Response(502, text="<html>bad gateway</html>")

        with pytest.raises(InvoiceError) as caught:
            await provider(handler).issue(CREDENTIALS, request())

        assert caught.value.code == "http_502"

    async def test_a_timeout_after_sending_may_have_issued_it_and_a_refused_connection_not(
        self,
    ) -> None:
        def slow(req: httpx.Request) -> httpx.Response:
            raise httpx.ReadTimeout("slow", request=req)

        def down(req: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("down", request=req)

        with pytest.raises(InvoiceError) as lost:
            await provider(slow).issue(CREDENTIALS, request())
        with pytest.raises(InvoiceError) as unreachable:
            await provider(down).issue(CREDENTIALS, request())

        assert lost.value.maybe_issued is True and lost.value.code == "unavailable"
        assert unreachable.value.maybe_issued is False

    async def test_void_and_lookup_use_their_own_operation(self) -> None:
        operations: list[dict[str, Any]] = []

        def handler(req: httpx.Request) -> httpx.Response:
            body = json.loads(req.content)
            operations.append(body)
            if body["operacion"] == "consultar_comprobante":
                return httpx.Response(200, json=answer())
            return httpx.Response(200, json={"numero": 1, "serie": "BBB1"})

        service = provider(handler)
        await service.void(CREDENTIALS, kind="boleta", series="BBB1", number=1, reason="ERROR")
        found = await service.lookup(CREDENTIALS, kind="boleta", series="BBB1", number=1)

        assert operations[0] == {
            "operacion": "generar_anulacion", "tipo_de_comprobante": "2", "serie": "BBB1",
            "numero": "1", "motivo": "ERROR", "codigo_unico": "",
        }  # fmt: skip
        assert operations[1]["operacion"] == "consultar_comprobante" and found is not None

    async def test_a_lookup_the_provider_refuses_means_it_does_not_have_it(self) -> None:
        def handler(req: httpx.Request) -> httpx.Response:
            return httpx.Response(404, json={"errors": "no existe", "codigo": 24})

        found = await provider(handler).lookup(CREDENTIALS, kind="boleta", series="BBB1", number=9)

        assert found is None


class TestTheRoute:
    @pytest.mark.parametrize(
        "route",
        [
            "https://demo.nubefact.com/api/v1/abc",
            "https://api.nubefact.com/api/v1/abc",
            "https://www.pse.pe/api/v1/abc",
            "https://nubefact.com/api/v1/abc",
        ],
    )
    def test_the_providers_addresses_pass(self, route: str) -> None:
        assert check_route(route) == route

    @pytest.mark.parametrize(
        "route",
        [
            "http://demo.nubefact.com/api/v1/abc",
            "https://nubefact.com.evil.example/api",
            "https://evil.example/nubefact.com",
            "https://user:pass@demo.nubefact.com/api",
            "https://demo.nubefact.com:8443/api",
            "https://127.0.0.1/api",
            "ftp://demo.nubefact.com/x",
            "not a url",
            "",
        ],
    )
    def test_anything_else_is_refused(self, route: str) -> None:
        with pytest.raises(InvoiceError) as caught:
            check_route(route)

        assert caught.value.code == "invalid_route"

    async def test_the_adapter_checks_it_again_before_calling(self) -> None:
        called = False

        def handler(req: httpx.Request) -> httpx.Response:
            nonlocal called
            called = True
            return httpx.Response(200, json=answer())

        with pytest.raises(InvoiceError):
            await provider(handler).issue(
                InvoiceCredentials("https://evil.example/x", "t"), request()
            )

        assert called is False


class TestThePdfOfTheAnswer:
    def test_a_zip_a_plain_pdf_and_nothing_are_all_understood(self) -> None:
        assert pdf_of({"pdf_zip_base64": zipped_pdf()}) == b"%PDF-1.4 receipt"
        assert (
            pdf_of({"pdf_zip_base64": base64.b64encode(b"%PDF-1.4 raw").decode()})
            == b"%PDF-1.4 raw"
        )
        assert pdf_of({"pdf_zip_base64": None}) is None
        assert pdf_of({}) is None
        assert pdf_of({"pdf_zip_base64": "not base64 at all !!"}) is None
        assert pdf_of({"pdf_zip_base64": zipped_pdf(b"MZ not a pdf")}) is None
