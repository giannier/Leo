"""The turn pipeline stays inside its tenant (Feature 004, TASK-421).

Everything asks as tenant A and must neither read nor write tenant B's rows: the
message queries the pipeline relies on (the latest message, the outbound ones after
an anchor) and the state, events and traces a turn writes.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.agent.pipeline import InboundTurn, TurnPipeline
from app.models.business import AgentConfig
from app.models.conversation import AgentTrace
from app.models.conversation_state import ConversationStageEvent, ConversationState
from app.process.service import ProcessConfigCache, ProcessConfigService
from app.providers.llm import ChatResult, MockLLMProvider
from app.repositories.conversation import MessageRepository
from tests.isolation.conftest import TenantFixture


def _count(db: Session, model: type, tenant_id: uuid.UUID) -> int:
    return db.execute(
        select(func.count()).select_from(model).where(model.tenant_id == tenant_id)  # type: ignore[attr-defined]
    ).scalar_one()


class TestMessageQueriesAreScoped:
    def test_outbound_after_never_returns_another_tenants_messages(
        self, db: Session, tenant_a: TenantFixture, tenant_b: TenantFixture
    ) -> None:
        MessageRepository(db, tenant_b.tenant_id).append(
            conversation_id=tenant_b.conversation_id,
            direction="outbound",
            author="agent",
            content="secreto de B",
        )

        leaked = MessageRepository(db, tenant_a.tenant_id).outbound_after(
            tenant_b.conversation_id, None
        )

        assert list(leaked) == []

    def test_an_anchor_from_another_tenant_reads_as_no_anchor(
        self, db: Session, tenant_a: TenantFixture, tenant_b: TenantFixture
    ) -> None:
        mine = MessageRepository(db, tenant_a.tenant_id).append(
            conversation_id=tenant_a.conversation_id,
            direction="outbound",
            author="agent",
            content="de A",
        )

        rows = MessageRepository(db, tenant_a.tenant_id).outbound_after(
            tenant_a.conversation_id, tenant_b.message_id
        )

        assert [m.id for m in rows] == [mine.id]

    def test_the_latest_message_is_the_tenants_own(
        self, db: Session, tenant_a: TenantFixture, tenant_b: TenantFixture
    ) -> None:
        repo = MessageRepository(db, tenant_a.tenant_id)

        assert repo.latest_for_conversation(tenant_b.conversation_id) is None
        latest = repo.latest_for_conversation(tenant_a.conversation_id)
        assert latest is not None and latest.id == tenant_a.message_id


class TestThePipelineOnlyTouchesItsOwnState:
    async def test_a_turn_for_another_tenants_conversation_does_not_exist(
        self, db: Session, tenant_a: TenantFixture, tenant_b: TenantFixture
    ) -> None:
        db.add(AgentConfig(tenant_id=tenant_a.tenant_id, is_active=True))
        db.flush()
        llm = MockLLMProvider()
        llm.queue(ChatResult(content="no deberia contestar"))

        pipeline = TurnPipeline(
            db,
            tenant_a.tenant_id,
            llm=llm,
            sandbox=True,
            process_config=ProcessConfigService(db, cache=ProcessConfigCache()),
        )
        with pytest.raises(LookupError):
            await pipeline.run(tenant_b.conversation_id, InboundTurn(text="hola"))

        assert llm.chat_calls == []
        assert _count(db, ConversationState, tenant_b.tenant_id) == 0

    async def test_the_state_and_events_a_turn_writes_carry_its_tenant(
        self, db: Session, tenant_a: TenantFixture, tenant_b: TenantFixture
    ) -> None:
        db.add_all(
            [
                AgentConfig(tenant_id=tenant_a.tenant_id, is_active=True),
                AgentConfig(tenant_id=tenant_b.tenant_id, is_active=True),
            ]
        )
        db.flush()
        llm = MockLLMProvider()
        llm.queue(ChatResult(content="¡Hola! ¿Cómo te llamas?"))

        await TurnPipeline(
            db,
            tenant_a.tenant_id,
            llm=llm,
            sandbox=True,
            process_config=ProcessConfigService(db, cache=ProcessConfigCache()),
        ).run(tenant_a.conversation_id, InboundTurn(text="hola"))

        assert _count(db, ConversationState, tenant_a.tenant_id) == 1
        assert _count(db, ConversationStageEvent, tenant_a.tenant_id) == 1
        assert _count(db, ConversationState, tenant_b.tenant_id) == 0
        assert _count(db, ConversationStageEvent, tenant_b.tenant_id) == 0
        assert _count(db, AgentTrace, tenant_b.tenant_id) == 0
