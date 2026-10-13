"""Payment proofs never cross tenants (Feature 004, TASK-429)."""

from __future__ import annotations

from decimal import Decimal

import pytest
from sqlalchemy.orm import Session

from app.models.conversation import Conversation
from app.models.orders import Order
from app.orders.proofs import ProofPipeline, ReceivedImage
from app.orders.service import OrderService
from app.providers.llm import MockLLMProvider
from app.providers.storage.mock import MockStorageProvider
from app.repositories.orders import PaymentProofRepository
from tests.isolation.conftest import TenantFixture


@pytest.fixture
def proof_b(db: Session, tenant_a: TenantFixture, tenant_b: TenantFixture) -> Order:
    conversation = db.get(Conversation, tenant_b.conversation_id)
    assert conversation is not None
    conversation.contact_id = tenant_b.contact_id
    db.flush()
    order = OrderService(db, tenant_b.tenant_id).open_draft(
        conversation, moneda="PEN", modalidad_pago="previo", is_sandbox=False
    )
    PaymentProofRepository(db, tenant_b.tenant_id).add(
        order_id=order.id,
        conversation_id=conversation.id,
        storage_key="tenants/b/proofs/x.jpg",
        monto=Decimal("10.00"),
        codigo_operacion="OP-SECRET",
    )
    return order


class TestProofs:
    def test_an_operation_code_of_another_tenant_is_not_seen(
        self, db: Session, tenant_a: TenantFixture, tenant_b: TenantFixture, proof_b: Order
    ) -> None:
        mine = PaymentProofRepository(db, tenant_a.tenant_id)
        theirs = PaymentProofRepository(db, tenant_b.tenant_id)

        assert mine.code_seen("OP-SECRET", sandbox=False) is False
        assert theirs.code_seen("OP-SECRET", sandbox=False) is True

    def test_the_proofs_of_a_foreign_order_are_not_listed(
        self, db: Session, tenant_a: TenantFixture, tenant_b: TenantFixture, proof_b: Order
    ) -> None:
        assert PaymentProofRepository(db, tenant_a.tenant_id).for_order(proof_b.id) == []
        assert PaymentProofRepository(db, tenant_a.tenant_id).latest_for_order(proof_b.id) is None

    async def test_an_image_on_a_foreign_conversation_records_nothing(
        self, db: Session, tenant_a: TenantFixture, tenant_b: TenantFixture, proof_b: Order
    ) -> None:
        conversation = db.get(Conversation, tenant_b.conversation_id)
        assert conversation is not None
        llm = MockLLMProvider()
        storage = MockStorageProvider()
        pipeline = ProofPipeline(db, tenant_a.tenant_id, llm=llm, storage=storage)

        outcome = await pipeline.handle(conversation, ReceivedImage(b"x", "image/jpeg"))

        assert outcome.kind == "ignorado"
        assert llm.vision_calls == []
        assert storage.objects == {}
