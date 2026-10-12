"""Resetting a simulator conversation stays inside its tenant (Feature 004, TASK-422, C-19).

Two businesses each have a sandbox conversation with state, messages, a trace and a
sandbox order. The reset is the only code that deletes those tables, so everything
asks as tenant A and must neither read nor remove tenant B's rows, nor touch a real
WhatsApp conversation or an order that is not a sandbox order.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.business import AgentConfig
from app.models.channel import Contact
from app.models.conversation import AgentTrace, Conversation, Message
from app.models.conversation_state import ConversationState, FunnelCode
from app.models.orders import Order
from app.process.service import ProcessConfigCache, ProcessConfigService
from app.process.state import StateRepo
from app.process.templates.base import Stage
from app.repositories.base import TenantScopeError
from app.repositories.sandbox_reset import SandboxResetRepository
from tests.isolation.conftest import TenantFixture


def _seed_turn_data(db: Session, tenant: TenantFixture) -> None:
    """State, a stage event, a sandbox order and a trace on the fixture's conversation."""
    contact = Contact(tenant_id=tenant.tenant_id, external_user_id=f"sandbox:{uuid.uuid4()}")
    db.add(contact)
    db.flush()
    config = ProcessConfigService(db, cache=ProcessConfigCache())
    db.add(AgentConfig(tenant_id=tenant.tenant_id, is_active=True))
    db.flush()
    effective = config.effective(tenant.tenant_id)
    state = StateRepo(db, tenant.tenant_id)
    row = db.get(Conversation, tenant.conversation_id)
    assert row is not None
    row.contact_id = contact.id
    created = state.get_or_create(row, effective.template)
    state.save(
        tenant.conversation_id,
        stage=Stage.IDENTIFICAR,
        funnel=FunnelCode.ATENDIDO,
        facts_patch={"saludo_enviado": True},
        reason="seed",
        expected_version=created.version,
    )
    db.add(
        Order(
            tenant_id=tenant.tenant_id,
            conversation_id=tenant.conversation_id,
            contact_id=contact.id,
            numero=1,
            moneda="PEN",
            is_sandbox=True,
        )
    )
    db.add(AgentTrace(tenant_id=tenant.tenant_id, message_id=tenant.message_id, model="m"))
    db.flush()


def _count(db: Session, model: type, tenant_id: uuid.UUID) -> int:
    return db.execute(
        select(func.count()).select_from(model).where(model.tenant_id == tenant_id)  # type: ignore[attr-defined]
    ).scalar_one()


class TestResetStaysInsideTheTenant:
    def test_a_tenant_cannot_wipe_another_tenants_conversation(
        self, db: Session, tenant_a: TenantFixture, tenant_b: TenantFixture
    ) -> None:
        _seed_turn_data(db, tenant_b)

        with pytest.raises(TenantScopeError):
            SandboxResetRepository(db, tenant_a.tenant_id).wipe(tenant_b.conversation_id)

        assert _count(db, Message, tenant_b.tenant_id) == 1
        assert _count(db, ConversationState, tenant_b.tenant_id) == 1
        assert _count(db, Order, tenant_b.tenant_id) == 1
        assert _count(db, AgentTrace, tenant_b.tenant_id) == 1

    def test_wiping_one_tenant_leaves_the_other_intact(
        self, db: Session, tenant_a: TenantFixture, tenant_b: TenantFixture
    ) -> None:
        _seed_turn_data(db, tenant_a)
        _seed_turn_data(db, tenant_b)

        SandboxResetRepository(db, tenant_a.tenant_id).wipe(tenant_a.conversation_id)

        for model in (Message, ConversationState, Order, AgentTrace):
            assert _count(db, model, tenant_a.tenant_id) == 0, model
            assert _count(db, model, tenant_b.tenant_id) == 1, model

    def test_a_real_whatsapp_conversation_is_never_wiped(
        self, db: Session, tenant_a: TenantFixture
    ) -> None:
        """The reset is for the simulator: a real conversation refuses it."""
        row = db.get(Conversation, tenant_a.conversation_id)
        assert row is not None
        row.channel = "whatsapp"
        db.flush()

        with pytest.raises(TenantScopeError):
            SandboxResetRepository(db, tenant_a.tenant_id).wipe(tenant_a.conversation_id)

        assert _count(db, Message, tenant_a.tenant_id) == 1

    def test_only_sandbox_orders_are_removed(self, db: Session, tenant_a: TenantFixture) -> None:
        _seed_turn_data(db, tenant_a)
        order = db.execute(select(Order).where(Order.tenant_id == tenant_a.tenant_id)).scalar_one()
        order.is_sandbox = False
        db.flush()

        SandboxResetRepository(db, tenant_a.tenant_id).wipe(tenant_a.conversation_id)

        assert _count(db, Order, tenant_a.tenant_id) == 1
