"""Electronic receipts through the business's own provider account (019, phase G)."""

from __future__ import annotations

# ruff: noqa: F811
import uuid
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.aftersale.invoicing import BuyerInput, InvoicingError, InvoicingService, suggested_buyer
from app.api.deps import get_invoice_provider
from app.core.crypto import decrypt_secret, encrypt_secret
from app.models.aftersale import InvoicingSettings, OrderDocument
from app.models.audit import AuditLog
from app.models.business import Product
from app.models.orders import Order, OrderItem
from app.providers.invoicing.base import InvoiceError
from app.providers.invoicing.mock import MockInvoiceProvider
from app.providers.storage.mock import MockStorageProvider
from app.repositories.aftersale import DocumentRepository, InvoicingSettingsRepository
from app.repositories.orders import OrderRepository
from tests.integration.test_crm_leads import _user
from tests.integration.test_orders_api import (  # noqa: F401 - fixtures
    operator_for,
    seed_order,
    storage,
    whatsapp,
)
from tests.integration.test_team import _owner
from tests.integration.test_turn_pipeline import _conversation, _tenant

ROUTE = "https://demo.nubefact.com/api/v1/3989d1a-demo"
INVOICING = "/api/v1/tenant/invoicing"
ORDERS = "/api/v1/tenant/orders"
D = Decimal
DNI_BUYER = BuyerInput("dni", "41920371", "Jorge López", "Jr. Color 211", "jorge@example.com")


def _sale(db: Session, tenant: Any, *, estado: str = "completado", price: str = "118.00") -> Order:
    """A completed order of two polos at `price` each, whose lines add up to its total."""
    conversation = _conversation(db, tenant, channel="whatsapp")
    polo = Product(tenant_id=tenant.id, name="Polo básico", price=D(price))
    db.add(polo)
    db.flush()
    total = D(price) * 2
    order = OrderRepository(db, tenant.id).add_numbered(
        conversation_id=conversation.id,
        contact_id=conversation.contact_id,
        moneda="PEN",
        subtotal=total,
        total=total,
        estado=estado,
        confirmed_at=datetime.now(UTC),
    )
    db.add(
        OrderItem(
            tenant_id=tenant.id,
            order_id=order.id,
            product_id=polo.id,
            nombre="Polo básico",
            precio_unitario=D(price),
            cantidad=2,
            subtotal=total,
        )
    )
    db.flush()
    return order


def _configured(db: Session, tenant: Any, *, active: bool = True) -> InvoicingSettings:
    settings = InvoicingSettingsRepository(db, tenant.id).ensure()
    settings.ruta = ROUTE
    settings.token_enc = encrypt_secret("tok-123456")
    settings.activo = active
    db.flush()
    return settings


def _service(db: Session, tenant: Any, provider: MockInvoiceProvider) -> InvoicingService:
    return InvoicingService(db, tenant.id, MockStorageProvider(), provider)


def _lines(db: Session, order: Order) -> list[OrderItem]:
    return list(db.execute(select(OrderItem).where(OrderItem.order_id == order.id)).scalars())


class TestIssuing:
    async def test_it_issues_keeps_the_pdf_and_moves_the_counter(self, db: Session) -> None:
        tenant = _tenant(db, template="productos")
        settings = _configured(db, tenant)
        order = _sale(db, tenant)
        provider = MockInvoiceProvider()

        document = await _service(db, tenant, provider).issue(
            order, _lines(db, order), "boleta", DNI_BUYER, user_id=_user(db)
        )

        assert (document.tipo, document.serie, document.numero) == ("boleta", "B001", "1")
        assert document.origen == "electronico" and document.estado == "emitido"
        assert document.proveedor["aceptada"] is True and document.proveedor["total"] == "236.00"
        assert settings.siguiente_boleta == 2 and settings.siguiente_factura == 1
        sent = provider.issued[("B001", 1)]
        assert (sent.taxed, sent.igv, sent.total) == (D("200.00"), D("36.00"), D("236.00"))
        assert sent.customer.name == "Jorge López" and sent.note == f"Pedido #{order.numero}"
        assert document.mime_type == "application/pdf"

    async def test_the_numbers_of_a_series_are_consecutive(self, db: Session) -> None:
        tenant = _tenant(db, template="productos")
        _configured(db, tenant)
        provider = MockInvoiceProvider()
        first, second = _sale(db, tenant), _sale(db, tenant)
        service = _service(db, tenant, provider)
        person = _user(db)

        one = await service.issue(first, _lines(db, first), "boleta", DNI_BUYER, user_id=person)
        two = await service.issue(second, _lines(db, second), "boleta", DNI_BUYER, user_id=person)

        assert (one.numero, two.numero) == ("1", "2")

    async def test_a_second_receipt_for_the_same_order_is_refused_until_the_first_is_voided(
        self, db: Session
    ) -> None:
        tenant = _tenant(db, template="productos")
        _configured(db, tenant)
        provider = MockInvoiceProvider()
        order = _sale(db, tenant)
        service = _service(db, tenant, provider)
        person = _user(db)
        lines = _lines(db, order)
        first = await service.issue(order, lines, "boleta", DNI_BUYER, user_id=person)

        with pytest.raises(InvoicingError) as again:
            await service.issue(order, lines, "boleta", DNI_BUYER, user_id=person)
        await service.void(first, "Error en el DNI del cliente")
        reissued = await service.issue(order, lines, "boleta", DNI_BUYER, user_id=person)

        assert again.value.code == "already_issued"
        assert first.estado == "anulado" and first.proveedor["anulado"]["motivo"]
        assert provider.voided == [("B001", 1, "Error en el DNI del cliente")]
        assert reissued.numero == "2"

    async def test_a_refusal_gives_the_number_back_and_keeps_nothing(self, db: Session) -> None:
        tenant = _tenant(db, template="productos")
        settings = _configured(db, tenant)
        provider = MockInvoiceProvider()
        provider.fail = InvoiceError("11", "Ruta inválida")
        order = _sale(db, tenant)

        with pytest.raises(InvoicingError) as caught:
            await _service(db, tenant, provider).issue(
                order, _lines(db, order), "boleta", DNI_BUYER, user_id=_user(db)
            )

        assert caught.value.code == "provider_refused" and caught.value.provider_code == "11"
        assert "Ruta inválida" in str(caught.value)
        assert settings.siguiente_boleta == 1
        assert db.execute(select(OrderDocument)).first() is None

    async def test_a_lost_answer_for_a_receipt_that_exists_is_adopted_not_issued_twice(
        self, db: Session
    ) -> None:
        tenant = _tenant(db, template="productos")
        settings = _configured(db, tenant)
        order = _sale(db, tenant)

        class LosesTheAnswer(MockInvoiceProvider):
            async def issue(self, credentials: Any, request: Any) -> Any:
                self.issued[(request.series, request.number)] = request  # it was processed...
                raise InvoiceError(
                    "unavailable", "timeout", maybe_issued=True
                )  # ...but we heard nothing

        provider = LosesTheAnswer()

        document = await _service(db, tenant, provider).issue(
            order, _lines(db, order), "boleta", DNI_BUYER, user_id=_user(db)
        )

        assert (document.serie, document.numero) == ("B001", "1")
        assert settings.siguiente_boleta == 2
        assert len(provider.issued) == 1

    async def test_a_lost_answer_that_cannot_be_confirmed_stops_everything(
        self, db: Session
    ) -> None:
        tenant = _tenant(db, template="productos")
        settings = _configured(db, tenant)
        provider = MockInvoiceProvider()
        provider.fail = InvoiceError("unavailable", "timeout", maybe_issued=True)
        order = _sale(db, tenant)

        with pytest.raises(InvoicingError) as caught:
            await _service(db, tenant, provider).issue(
                order, _lines(db, order), "boleta", DNI_BUYER, user_id=_user(db)
            )

        assert caught.value.code == "unknown_outcome"
        assert settings.siguiente_boleta == 1 and db.execute(select(OrderDocument)).first() is None

    @pytest.mark.parametrize(
        ("setup", "code"),
        [
            ("disabled", "disabled"),
            ("no_token", "not_configured"),
            ("not_sold", "order_not_sold"),
            ("sandbox", "sandbox"),
            ("dollars", "only_soles"),
        ],
    )
    async def test_what_cannot_be_invoiced_is_refused_before_asking_the_provider(
        self, db: Session, setup: str, code: str
    ) -> None:
        tenant = _tenant(db, template="productos")
        settings = _configured(db, tenant, active=setup != "disabled")
        order = _sale(db, tenant, estado="por_confirmar" if setup == "not_sold" else "completado")
        if setup == "no_token":
            settings.token_enc = None
        if setup == "sandbox":
            order.is_sandbox = True
        if setup == "dollars":
            order.moneda = "USD"
        db.flush()
        provider = MockInvoiceProvider()

        with pytest.raises(InvoicingError) as caught:
            await _service(db, tenant, provider).issue(
                order, _lines(db, order), "boleta", DNI_BUYER, user_id=_user(db)
            )

        assert caught.value.code == code and provider.issued == {}

    async def test_a_factura_needs_a_ruc_and_a_big_boleta_needs_a_document(
        self, db: Session
    ) -> None:
        tenant = _tenant(db, template="productos")
        _configured(db, tenant)
        order = _sale(db, tenant, price="400.00")  # 800 in total
        service = _service(db, tenant, MockInvoiceProvider())
        person = _user(db)
        lines = _lines(db, order)

        with pytest.raises(InvoicingError) as factura:
            await service.issue(order, lines, "factura", DNI_BUYER, user_id=person)
        with pytest.raises(InvoicingError) as big:
            await service.issue(
                order, lines, "boleta", BuyerInput("ninguno", "", ""), user_id=person
            )

        assert (factura.value.code, big.value.code) == ("ruc_required", "document_required")

    async def test_a_factura_to_a_company_is_issued(self, db: Session) -> None:
        tenant = _tenant(db, template="productos")
        _configured(db, tenant)
        order = _sale(db, tenant)

        document = await _service(db, tenant, MockInvoiceProvider()).issue(
            order,
            _lines(db, order),
            "factura",
            BuyerInput("ruc", "20600695771", "NUBEFACT SA"),
            user_id=_user(db),
        )

        assert (document.tipo, document.serie, document.numero) == ("factura", "F001", "1")

    async def test_a_voided_receipt_is_not_voided_twice(self, db: Session) -> None:
        tenant = _tenant(db, template="productos")
        _configured(db, tenant)
        provider = MockInvoiceProvider()
        order = _sale(db, tenant)
        service = _service(db, tenant, provider)
        document = await service.issue(
            order, _lines(db, order), "boleta", DNI_BUYER, user_id=_user(db)
        )
        await service.void(document, "Se emitió por error")

        with pytest.raises(InvoicingError) as twice:
            await service.void(document, "Se emitió por error")
        with pytest.raises(InvoicingError) as short:
            await service.void(
                await service.issue(
                    order, _lines(db, order), "boleta", DNI_BUYER, user_id=_user(db)
                ),
                "no",
            )

        assert (twice.value.code, short.value.code) == ("already_void", "reason_required")

    def test_the_order_s_own_data_suggest_the_buyer(self) -> None:
        company = suggested_buyer(
            {"ruc": "20600695771", "empresa": "NUBEFACT SA", "nombres": "Ana"}
        )
        person = suggested_buyer({"dni": "41920371", "nombres": "Jorge", "apellidos": "López"})
        nobody = suggested_buyer({"nombres": "Ana"})
        empty = suggested_buyer(None)

        assert (company.document_type, company.name) == ("ruc", "NUBEFACT SA")
        assert (person.document_type, person.name) == ("dni", "Jorge López")
        assert nobody.document_type == "ninguno" and empty.document_type == "ninguno"


class TestThePanel:
    @pytest.fixture
    def provider(self, app: Any) -> MockInvoiceProvider:
        mock = MockInvoiceProvider()
        app.dependency_overrides[get_invoice_provider] = lambda: mock
        return mock

    async def _confirmed(self, db: Session, tenant_id: str) -> Order:
        order, _ = seed_order(db, tenant_id)
        order.estado = "confirmado"
        order.confirmed_at = datetime.now(UTC)
        db.commit()
        return order

    async def test_it_needs_a_login_and_only_managers_configure(
        self, api: AsyncClient, mail: Any, db: Session
    ) -> None:
        anonymous = await api.get(INVOICING)
        account, _, operator = await operator_for(api, mail, db)

        read = await api.get(INVOICING, headers=operator)
        write = await api.put(INVOICING, headers=operator, json={"activo": True})

        assert anonymous.status_code == 401
        assert (read.status_code, write.status_code) == (403, 403)
        assert account["tenant_id"]

    async def test_the_token_is_kept_encrypted_never_shown_and_the_change_is_audited(
        self, api: AsyncClient, mail: Any, db: Session
    ) -> None:
        account, headers = await _owner(api, mail)

        saved = await api.put(
            INVOICING,
            headers=headers,
            json={
                "ruta": ROUTE,
                "token": "tok-123456",
                "serie_boleta": "B002",
                "siguiente_boleta": 41,
            },
        )
        shown = await api.get(INVOICING, headers=headers)

        assert saved.status_code == 200, saved.text
        body = shown.json()
        assert body["ruta"] == ROUTE and body["tiene_token"] is True and body["es_demo"] is True
        assert (body["serie_boleta"], body["siguiente_boleta"], body["activo"]) == (
            "B002",
            41,
            False,
        )
        assert "token" not in body and "tok-123456" not in shown.text
        stored = InvoicingSettingsRepository(db, uuid.UUID(account["tenant_id"])).current()
        assert stored is not None and stored.token_enc != b"tok-123456"
        assert decrypt_secret(stored.token_enc) == "tok-123456"
        entry = db.execute(
            select(AuditLog).where(AuditLog.action == "invoicing.updated")
        ).scalar_one()
        assert "tok-123456" not in str(entry.detail) and "token" in entry.detail["campos"]

    @pytest.mark.parametrize(
        "route",
        [
            "http://demo.nubefact.com/x",
            "https://evil.example/api",
            "https://127.0.0.1/api",
            "javascript:1",
        ],
    )
    async def test_a_route_that_is_not_the_providers_is_refused(
        self, api: AsyncClient, mail: Any, route: str
    ) -> None:
        _, headers = await _owner(api, mail)

        response = await api.put(INVOICING, headers=headers, json={"ruta": route})

        assert response.status_code == 400 and response.json()["fields"]["ruta"]

    async def test_it_cannot_be_turned_on_without_the_account(
        self, api: AsyncClient, mail: Any
    ) -> None:
        _, headers = await _owner(api, mail)

        refused = await api.put(INVOICING, headers=headers, json={"activo": True})
        bad_series = await api.put(INVOICING, headers=headers, json={"serie_boleta": "F001"})
        ready = await api.put(
            INVOICING, headers=headers, json={"ruta": ROUTE, "token": "tok-123456", "activo": True}
        )

        assert refused.status_code == 409 and refused.json()["error"] == "not_configured"
        assert bad_series.status_code in (400, 422)
        assert ready.status_code == 200 and ready.json()["activo"] is True

    async def test_the_team_previews_issues_and_the_owner_voids(
        self,
        api: AsyncClient,
        mail: Any,
        db: Session,
        provider: MockInvoiceProvider,
        storage: MockStorageProvider,
    ) -> None:
        account, owner, operator = await operator_for(api, mail, db)
        await api.put(
            INVOICING, headers=owner, json={"ruta": ROUTE, "token": "tok-123456", "activo": True}
        )
        order = await self._confirmed(db, account["tenant_id"])
        buyer = {"documento_tipo": "dni", "documento_numero": "41920371", "nombre": "Ana Pérez"}

        info = await api.get(f"{ORDERS}/{order.id}/invoice", headers=operator)
        preview = await api.post(
            f"{ORDERS}/{order.id}/invoice/preview",
            headers=operator,
            json={"tipo": "boleta", "cliente": buyer},
        )
        issued_by_the_preview = dict(provider.issued)
        issued = await api.post(
            f"{ORDERS}/{order.id}/invoice",
            headers=operator,
            json={"tipo": "boleta", "cliente": buyer},
        )
        twice = await api.post(
            f"{ORDERS}/{order.id}/invoice",
            headers=operator,
            json={"tipo": "boleta", "cliente": buyer},
        )
        operator_void = await api.post(
            f"{ORDERS}/{order.id}/invoice/void",
            headers=operator,
            json={"motivo": "Error en el nombre"},
        )
        generic_void = await api.post(
            f"{ORDERS}/{order.id}/documents/{issued.json()['id']}/void", headers=owner
        )
        voided = await api.post(
            f"{ORDERS}/{order.id}/invoice/void",
            headers=owner,
            json={"motivo": "Error en el nombre"},
        )

        assert info.json()["disponible"] is True and info.json()["siguiente_boleta"] == "B001-1"
        assert info.json()["solo_prueba"] is True and info.json()["comprobante"] is None
        assert preview.status_code == 200, preview.text
        assert preview.json()["comprobante"] == "B001-1" and D(preview.json()["total"]) == D(
            "120.00"
        )
        assert issued_by_the_preview == {}  # looking at it issued nothing
        assert len(provider.issued) == 1
        assert issued.status_code == 201, issued.text
        assert (
            issued.json()["nombre"] == "Boleta B001-1" and issued.json()["origen"] == "electronico"
        )
        assert twice.status_code == 409 and twice.json()["error"] == "already_issued"
        assert operator_void.status_code == 403
        assert generic_void.status_code == 409 and generic_void.json()["error"] == "electronic"
        assert voided.status_code == 200 and voided.json()["estado"] == "anulado"
        actions = {
            row.action
            for row in db.execute(
                select(AuditLog).where(AuditLog.action.like("order.invoice_%"))
            ).scalars()
        }
        assert actions == {"order.invoice_issued", "order.invoice_voided"}

    async def test_a_refusal_of_the_provider_is_shown_as_it_came(
        self,
        api: AsyncClient,
        mail: Any,
        db: Session,
        provider: MockInvoiceProvider,
        storage: MockStorageProvider,
    ) -> None:
        account, owner = await _owner(api, mail)
        await api.put(
            INVOICING, headers=owner, json={"ruta": ROUTE, "token": "tok-123456", "activo": True}
        )
        order = await self._confirmed(db, account["tenant_id"])
        provider.fail = InvoiceError("10", "Token inválido")

        response = await api.post(
            f"{ORDERS}/{order.id}/invoice",
            headers=owner,
            json={
                "tipo": "boleta",
                "cliente": {
                    "documento_tipo": "dni",
                    "documento_numero": "41920371",
                    "nombre": "Ana",
                },
            },
        )
        again = await api.get(INVOICING, headers=owner)

        assert response.status_code == 422 and response.json()["error"] == "provider_refused"
        assert "Token inválido" in response.json()["message"]
        assert response.json()["fields"]["proveedor"] == "10"
        assert again.json()["siguiente_boleta"] == 1

    async def test_another_businesss_order_is_a_404(
        self, api: AsyncClient, mail: Any, db: Session, provider: MockInvoiceProvider
    ) -> None:
        _, mine = await _owner(api, mail)
        theirs, _ = await _owner(api, mail)
        order = await self._confirmed(db, theirs["tenant_id"])
        buyer = {"documento_tipo": "ninguno"}

        responses = [
            await api.get(f"{ORDERS}/{order.id}/invoice", headers=mine),
            await api.post(
                f"{ORDERS}/{order.id}/invoice/preview",
                headers=mine,
                json={"tipo": "boleta", "cliente": buyer},
            ),
            await api.post(
                f"{ORDERS}/{order.id}/invoice",
                headers=mine,
                json={"tipo": "boleta", "cliente": buyer},
            ),
            await api.post(
                f"{ORDERS}/{order.id}/invoice/void", headers=mine, json={"motivo": "no importa"}
            ),
        ]

        assert {r.status_code for r in responses} == {404}
        assert provider.issued == {}
        assert DocumentRepository(db, uuid.UUID(theirs["tenant_id"])).list() == []
