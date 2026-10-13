"""The operator's order tools never cross tenants (Feature 004, TASK-430)."""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy.orm import Session

from app.models.conversation import Conversation
from app.models.orders import Order
from app.models.user import User
from app.orders.review import OrderNotFound, OrderReviewService
from app.orders.service import OrderService
from app.process.recompute import recompute_stage
from app.repositories.audit import AuditLogRepository
from app.repositories.base import TenantScopeError
from app.repositories.orders import OrderRepository
from tests.isolation.conftest import TenantFixture


@pytest.fixture
def order_b(db: Session, tenant_a: TenantFixture, tenant_b: TenantFixture) -> Order:
    conversation = db.get(Conversation, tenant_b.conversation_id)
    assert conversation is not None
    conversation.contact_id = tenant_b.contact_id
    db.flush()
    order = OrderService(db, tenant_b.tenant_id).open_draft(
        conversation, moneda="PEN", modalidad_pago="previo", is_sandbox=False
    )
    order.estado = "por_confirmar"
    db.flush()
    return order


class TestOrdersOfAnotherTenant:
    def test_the_list_search_and_summary_never_see_them(
        self, db: Session, tenant_a: TenantFixture, tenant_b: TenantFixture, order_b: Order
    ) -> None:
        mine = OrderRepository(db, tenant_a.tenant_id)
        theirs = OrderRepository(db, tenant_b.tenant_id)

        assert mine.search()[1] == 0
        assert mine.search(q=str(order_b.numero))[1] == 0
        assert theirs.search()[1] == 1
        assert mine.get_for_update(order_b.id) is None

    def test_the_operator_cannot_decide_a_foreign_order(
        self, db: Session, tenant_a: TenantFixture, tenant_b: TenantFixture, order_b: Order
    ) -> None:
        review = OrderReviewService(db, tenant_a.tenant_id)

        with pytest.raises(OrderNotFound):
            review.confirm(order_b.id, tenant_a.contact_id)
        with pytest.raises(OrderNotFound):
            review.reject(order_b.id, tenant_a.contact_id, motivo="no corresponde")
        with pytest.raises(OrderNotFound):
            review.set_status(order_b.id, tenant_a.contact_id, "descartado")
        db.refresh(order_b)
        assert order_b.estado == "por_confirmar"

    def test_the_stage_of_a_foreign_conversation_is_not_recomputed(
        self, db: Session, tenant_a: TenantFixture, tenant_b: TenantFixture
    ) -> None:
        conversation = db.get(Conversation, tenant_b.conversation_id)
        assert conversation is not None

        with pytest.raises(TenantScopeError):
            recompute_stage(db, tenant_a.tenant_id, conversation, reason="operador:confirmar")

    def test_the_audit_trail_of_a_foreign_order_is_not_readable(
        self, db: Session, tenant_a: TenantFixture, tenant_b: TenantFixture, order_b: Order
    ) -> None:
        actor = User(
            email=f"op-{uuid.uuid4().hex[:8]}@example.com", password_hash="x", full_name="Op"
        )
        db.add(actor)
        db.flush()
        AuditLogRepository(db, tenant_b.tenant_id).record(
            actor_id=actor.id, action="order.confirmed", detail={"order_id": str(order_b.id)}
        )

        assert len(AuditLogRepository(db, tenant_b.tenant_id).for_order(order_b.id)) == 1
        assert AuditLogRepository(db, tenant_a.tenant_id).for_order(order_b.id) == []
