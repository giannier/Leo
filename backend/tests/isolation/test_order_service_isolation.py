"""The order service never works across tenants (Feature 004, TASK-425).

`OrderService` receives ORM objects (an order, a conversation, a product) that
were read through some repository. These cases hand it tenant B's real objects
while it is bound to tenant A, and expect it to refuse rather than write.
"""

from __future__ import annotations

from decimal import Decimal

import pytest
from sqlalchemy.orm import Session

from app.models.business import Product
from app.models.conversation import Conversation
from app.models.orders import Order
from app.orders.service import OrderService, SelectionRefused
from app.repositories.base import TenantScopeError
from app.repositories.product import ProductRepository
from tests.isolation.conftest import TenantFixture


@pytest.fixture
def order_b(db: Session, tenant_a: TenantFixture, tenant_b: TenantFixture) -> Order:
    conversation = db.get(Conversation, tenant_b.conversation_id)
    assert conversation is not None
    conversation.contact_id = tenant_b.contact_id
    db.flush()
    return OrderService(db, tenant_b.tenant_id).open_draft(
        conversation, moneda="PEN", modalidad_pago="previo", is_sandbox=False
    )


class TestAnotherTenantsObjects:
    def test_it_cannot_read_the_active_order_of_a_foreign_conversation(
        self, db: Session, tenant_a: TenantFixture, tenant_b: TenantFixture, order_b: Order
    ) -> None:
        assert OrderService(db, tenant_a.tenant_id).active_for(tenant_b.conversation_id) is None

    def test_it_will_not_open_a_draft_on_a_foreign_conversation(
        self, db: Session, tenant_a: TenantFixture, tenant_b: TenantFixture
    ) -> None:
        conversation = db.get(Conversation, tenant_b.conversation_id)
        assert conversation is not None

        with pytest.raises(TenantScopeError):
            OrderService(db, tenant_a.tenant_id).open_draft(
                conversation, moneda="PEN", modalidad_pago="previo", is_sandbox=False
            )

    def test_it_will_not_change_a_foreign_order(
        self, db: Session, tenant_a: TenantFixture, tenant_b: TenantFixture, order_b: Order
    ) -> None:
        product = db.get(Product, tenant_a.product_id)
        assert product is not None
        service = OrderService(db, tenant_a.tenant_id)

        with pytest.raises(TenantScopeError):
            service.add_product(order_b, product)
        with pytest.raises(TenantScopeError):
            service.transition(order_b, "descartado")
        with pytest.raises(TenantScopeError):
            service.recalculate(order_b)
        with pytest.raises(TenantScopeError):
            service.customer_data_changed(order_b)
        with pytest.raises(TenantScopeError):
            service.lines(order_b)
        assert order_b.estado == "borrador"

    def test_it_will_not_add_a_foreign_product(
        self, db: Session, tenant_a: TenantFixture, tenant_b: TenantFixture
    ) -> None:
        conversation = db.get(Conversation, tenant_a.conversation_id)
        assert conversation is not None
        conversation.contact_id = tenant_a.contact_id
        db.flush()
        service = OrderService(db, tenant_a.tenant_id)
        order = service.open_draft(
            conversation, moneda="PEN", modalidad_pago="previo", is_sandbox=False
        )
        foreign = db.get(Product, tenant_b.product_id)
        assert foreign is not None

        with pytest.raises(TenantScopeError):
            service.add_product(order, foreign)

    def test_the_product_lookup_by_ids_stays_inside_the_tenant(
        self, db: Session, tenant_a: TenantFixture, tenant_b: TenantFixture
    ) -> None:
        repository = ProductRepository(db, tenant_a.tenant_id)

        found = repository.by_ids({tenant_a.product_id, tenant_b.product_id})

        assert set(found) == {tenant_a.product_id}

    def test_the_selection_lookup_sees_only_this_tenants_catalogue(
        self, db: Session, tenant_a: TenantFixture, tenant_b: TenantFixture
    ) -> None:
        """A closed package must not accept the name of a product of another business."""
        conversation = db.get(Conversation, tenant_a.conversation_id)
        assert conversation is not None
        conversation.contact_id = tenant_a.contact_id
        package = Product(
            tenant_id=tenant_a.tenant_id,
            name="Paquete cerrado",
            price=Decimal("100.00"),
            bundle_size=2,
        )
        db.add(package)
        db.flush()
        service = OrderService(db, tenant_a.tenant_id)
        order = service.open_draft(
            conversation, moneda="PEN", modalidad_pago="previo", is_sandbox=False
        )
        service.add_product(order, package)

        with pytest.raises(SelectionRefused):
            service.add_selection(order, "Product B")
