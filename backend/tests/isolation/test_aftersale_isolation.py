"""One business never reaches another's returns, their events or the documents of its orders
(feature 019, phase G).

A return carries what a customer said and the photos they sent; a document is their invoice. The
cases hand tenant A tenant B's real ids and expect nothing back, and check that each business
numbers its own requests from 1.
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest
from sqlalchemy.orm import Session

from app.models.aftersale import OrderDocument, OrderReturn
from app.models.orders import Order
from app.repositories.aftersale import (
    DocumentRepository,
    InvoicingSettingsRepository,
    ReturnEventRepository,
    ReturnRepository,
)
from app.repositories.base import TenantScopeError
from app.repositories.orders import OrderRepository
from tests.isolation.conftest import TenantFixture


def _order(db: Session, fixture: TenantFixture) -> Order:
    return OrderRepository(db, fixture.tenant_id).add_numbered(
        conversation_id=fixture.conversation_id,
        contact_id=fixture.contact_id,
        moneda="PEN",
        estado="confirmado",
    )


def _return(db: Session, fixture: TenantFixture, order: Order, **extra: Any) -> OrderReturn:
    return ReturnRepository(db, fixture.tenant_id).add_numbered(
        order_id=order.id,
        contact_id=fixture.contact_id,
        tipo="devolucion",
        motivo="defectuoso",
        created_by="agent",
        **extra,
    )


@pytest.fixture
def seeded(
    db: Session, tenant_a: TenantFixture, tenant_b: TenantFixture
) -> dict[str, tuple[Order, OrderReturn, OrderDocument]]:
    out: dict[str, tuple[Order, OrderReturn, OrderDocument]] = {}
    for label, fixture in (("a", tenant_a), ("b", tenant_b)):
        order = _order(db, fixture)
        ret = _return(db, fixture, order, detalle=f"de {label}")
        document = DocumentRepository(db, fixture.tenant_id).add(
            order_id=order.id,
            tipo="boleta",
            titulo=f"Boleta {label}",
            storage_key=f"tenants/{fixture.tenant_id}/orders/{order.id}/{uuid.uuid4()}.pdf",
            filename="boleta.pdf",
            mime_type="application/pdf",
            size_bytes=10,
        )
        out[label] = (order, ret, document)
    return out


class TestReturns:
    def test_a_tenant_reads_only_its_own(
        self,
        db: Session,
        tenant_a: TenantFixture,
        tenant_b: TenantFixture,
        seeded: dict[str, tuple[Order, OrderReturn, OrderDocument]],
    ) -> None:
        mine = ReturnRepository(db, tenant_a.tenant_id)

        assert [r.detalle for r in mine.list()] == ["de a"]
        assert mine.get(seeded["b"][1].id) is None
        assert mine.of_orders([seeded["b"][0].id]) == {}
        rows, total = mine.search()
        assert total == 1 and [r.detalle for r, _, _ in rows] == ["de a"]

    def test_each_business_numbers_its_own_requests_from_one(
        self,
        db: Session,
        tenant_a: TenantFixture,
        tenant_b: TenantFixture,
        seeded: dict[str, tuple[Order, OrderReturn, OrderDocument]],
    ) -> None:
        assert (seeded["a"][1].numero, seeded["b"][1].numero) == (1, 1)

    def test_the_number_cannot_be_chosen(self, db: Session, tenant_a: TenantFixture) -> None:
        order = _order(db, tenant_a)

        with pytest.raises(ValueError, match="numero"):
            ReturnRepository(db, tenant_a.tenant_id).add_numbered(
                order_id=order.id,
                contact_id=tenant_a.contact_id,
                tipo="cambio",
                motivo="otro",
                created_by="user",
                numero=9,
            )

    def test_an_open_request_of_another_tenants_order_is_not_found(
        self,
        db: Session,
        tenant_a: TenantFixture,
        seeded: dict[str, tuple[Order, OrderReturn, OrderDocument]],
    ) -> None:
        assert (
            ReturnRepository(db, tenant_a.tenant_id).open_for(seeded["b"][0].id, "devolucion")
            is None
        )

    def test_a_row_cannot_be_written_for_another_tenant(
        self, db: Session, tenant_a: TenantFixture, tenant_b: TenantFixture
    ) -> None:
        order = _order(db, tenant_a)

        with pytest.raises(TenantScopeError):
            ReturnRepository(db, tenant_a.tenant_id).add(
                tenant_id=tenant_b.tenant_id,
                order_id=order.id,
                contact_id=tenant_a.contact_id,
                numero=1,
                tipo="cambio",
                motivo="otro",
                created_by="user",
            )

    def test_the_counts_of_the_panel_are_the_tenants_own(
        self,
        db: Session,
        tenant_a: TenantFixture,
        tenant_b: TenantFixture,
        seeded: dict[str, tuple[Order, OrderReturn, OrderDocument]],
    ) -> None:
        assert ReturnRepository(db, tenant_a.tenant_id).count_waiting() == 1
        assert ReturnEventRepository(db, tenant_a.tenant_id).of_return(seeded["b"][1].id) == []


class TestDocuments:
    def test_a_tenant_reads_only_the_documents_of_its_orders(
        self,
        db: Session,
        tenant_a: TenantFixture,
        tenant_b: TenantFixture,
        seeded: dict[str, tuple[Order, OrderReturn, OrderDocument]],
    ) -> None:
        mine = DocumentRepository(db, tenant_a.tenant_id)

        assert [d.titulo for d in mine.list()] == ["Boleta a"]
        assert mine.get(seeded["b"][2].id) is None
        assert mine.of_order(seeded["b"][0].id) == []
        assert mine.of_orders([seeded["b"][0].id]) == {}


class TestInvoicingSettings:
    def test_each_business_has_its_own_account_and_numbers(
        self, db: Session, tenant_a: TenantFixture, tenant_b: TenantFixture
    ) -> None:
        mine = InvoicingSettingsRepository(db, tenant_a.tenant_id).ensure()
        theirs = InvoicingSettingsRepository(db, tenant_b.tenant_id).ensure()
        mine.ruta = "https://demo.nubefact.com/api/v1/a"
        mine.siguiente_boleta = 41
        db.flush()

        assert InvoicingSettingsRepository(db, tenant_a.tenant_id).current() is mine
        assert (theirs.ruta, theirs.siguiente_boleta) == (None, 1)
        assert theirs.tenant_id == tenant_b.tenant_id

    def test_asking_twice_finds_the_same_row(self, db: Session, tenant_a: TenantFixture) -> None:
        repo = InvoicingSettingsRepository(db, tenant_a.tenant_id)

        assert repo.ensure() is repo.ensure() is repo.locked()
