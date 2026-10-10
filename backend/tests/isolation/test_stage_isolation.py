"""One business never reaches another's conversation state or stage history
(Feature 004, TASK-402).

`conversation_state` is keyed by the conversation id, which is not a secret a
tenant can be assumed not to hold: ids travel in URLs and logs. So the cases
below hand tenant A tenant B's real conversation id and expect nothing back.
"""

from __future__ import annotations

import pytest
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models.conversation_state import ConversationState
from app.repositories.base import TenantScopeError
from app.repositories.conversation_state import (
    ConversationStageEventRepository,
    ConversationStateRepository,
)
from tests.isolation.conftest import TenantFixture


def _seed(db: Session, fixture: TenantFixture, label: str) -> None:
    """One state row and two stage events for a tenant's conversation."""
    states = ConversationStateRepository(db, fixture.tenant_id)
    states.add(
        conversation_id=fixture.conversation_id,
        template_key="productos",
        template_version=1,
        stage_key=f"STAGE_{label}",
        funnel_code="NUEVO",
        facts={"saludo_enviado": label == "A"},
    )
    events = ConversationStageEventRepository(db, fixture.tenant_id)
    events.add(
        conversation_id=fixture.conversation_id,
        from_stage=None,
        to_stage="INICIO",
        from_funnel=None,
        to_funnel="NUEVO",
        reason=f"turno-{label}",
    )
    events.add(
        conversation_id=fixture.conversation_id,
        from_stage="INICIO",
        to_stage=f"STAGE_{label}",
        from_funnel="NUEVO",
        to_funnel="ATENDIDO",
        reason=f"turno-{label}",
    )


@pytest.fixture
def seeded(db: Session, tenant_a: TenantFixture, tenant_b: TenantFixture) -> None:
    _seed(db, tenant_a, "A")
    _seed(db, tenant_b, "B")


class TestConversationStateIsPerTenant:
    def test_a_foreign_conversation_has_no_state_for_you(
        self, db: Session, tenant_a: TenantFixture, tenant_b: TenantFixture, seeded: None
    ) -> None:
        repository = ConversationStateRepository(db, tenant_a.tenant_id)

        assert repository.by_conversation(tenant_b.conversation_id) is None
        assert repository.get(tenant_b.conversation_id) is None
        assert not repository.exists(tenant_b.conversation_id)

    def test_you_read_your_own_state(
        self, db: Session, tenant_a: TenantFixture, tenant_b: TenantFixture, seeded: None
    ) -> None:
        state = ConversationStateRepository(db, tenant_a.tenant_id).by_conversation(
            tenant_a.conversation_id
        )

        assert state is not None
        assert state.stage_key == "STAGE_A"
        assert state.facts == {"saludo_enviado": True}

    def test_listing_never_includes_the_other_business(
        self, db: Session, tenant_a: TenantFixture, tenant_b: TenantFixture, seeded: None
    ) -> None:
        rows = ConversationStateRepository(db, tenant_a.tenant_id).list()

        assert [row.conversation_id for row in rows] == [tenant_a.conversation_id]

    def test_counting_is_scoped(
        self, db: Session, tenant_a: TenantFixture, tenant_b: TenantFixture, seeded: None
    ) -> None:
        assert ConversationStateRepository(db, tenant_a.tenant_id).count() == 1

    def test_updating_a_foreign_state_changes_nothing(
        self, db: Session, tenant_a: TenantFixture, tenant_b: TenantFixture, seeded: None
    ) -> None:
        result = ConversationStateRepository(db, tenant_a.tenant_id).update(
            tenant_b.conversation_id, stage_key="HIJACKED", funnel_code="GANADO"
        )

        assert result is None
        untouched = ConversationStateRepository(db, tenant_b.tenant_id).by_conversation(
            tenant_b.conversation_id
        )
        assert untouched is not None
        assert untouched.stage_key == "STAGE_B"
        assert untouched.funnel_code == "NUEVO"

    def test_deleting_a_foreign_state_is_refused(
        self, db: Session, tenant_a: TenantFixture, tenant_b: TenantFixture, seeded: None
    ) -> None:
        assert (
            ConversationStateRepository(db, tenant_a.tenant_id).delete(tenant_b.conversation_id)
            is False
        )

        assert (
            ConversationStateRepository(db, tenant_b.tenant_id).by_conversation(
                tenant_b.conversation_id
            )
            is not None
        )

    def test_a_repository_cannot_write_for_another_tenant(
        self, db: Session, tenant_a: TenantFixture, tenant_b: TenantFixture
    ) -> None:
        with pytest.raises(TenantScopeError):
            ConversationStateRepository(db, tenant_a.tenant_id).add(
                tenant_id=tenant_b.tenant_id,
                conversation_id=tenant_b.conversation_id,
                template_key="productos",
                template_version=1,
                stage_key="INICIO",
                funnel_code="NUEVO",
            )

    def test_deleting_all_of_your_states_leaves_the_other_alone(
        self, db: Session, tenant_a: TenantFixture, tenant_b: TenantFixture, seeded: None
    ) -> None:
        assert ConversationStateRepository(db, tenant_a.tenant_id).delete_all() == 1

        assert ConversationStateRepository(db, tenant_b.tenant_id).count() == 1


class TestStageEventsArePerTenant:
    def test_history_never_includes_the_other_business(
        self, db: Session, tenant_a: TenantFixture, tenant_b: TenantFixture, seeded: None
    ) -> None:
        events = ConversationStageEventRepository(db, tenant_a.tenant_id)

        mine = events.for_conversation(tenant_a.conversation_id)

        assert [e.reason for e in mine] == ["turno-A", "turno-A"]
        assert [e.to_stage for e in mine] == ["INICIO", "STAGE_A"]

    def test_asking_for_a_foreign_conversations_history_returns_nothing(
        self, db: Session, tenant_a: TenantFixture, tenant_b: TenantFixture, seeded: None
    ) -> None:
        events = ConversationStageEventRepository(db, tenant_a.tenant_id)

        assert events.for_conversation(tenant_b.conversation_id) == []

    def test_listing_and_counting_are_scoped(
        self, db: Session, tenant_a: TenantFixture, tenant_b: TenantFixture, seeded: None
    ) -> None:
        events = ConversationStageEventRepository(db, tenant_a.tenant_id)

        assert events.count() == 2
        assert {e.tenant_id for e in events.list()} == {tenant_a.tenant_id}

    def test_a_foreign_event_id_reads_as_missing(
        self, db: Session, tenant_a: TenantFixture, tenant_b: TenantFixture, seeded: None
    ) -> None:
        foreign = ConversationStageEventRepository(db, tenant_b.tenant_id).list()[0]

        assert ConversationStageEventRepository(db, tenant_a.tenant_id).get(foreign.id) is None

    def test_deleting_a_foreign_event_is_refused(
        self, db: Session, tenant_a: TenantFixture, tenant_b: TenantFixture, seeded: None
    ) -> None:
        foreign = ConversationStageEventRepository(db, tenant_b.tenant_id).list()[0]

        assert ConversationStageEventRepository(db, tenant_a.tenant_id).delete(foreign.id) is False
        assert ConversationStageEventRepository(db, tenant_b.tenant_id).count() == 2

    def test_a_repository_cannot_append_for_another_tenant(
        self, db: Session, tenant_a: TenantFixture, tenant_b: TenantFixture
    ) -> None:
        with pytest.raises(TenantScopeError):
            ConversationStageEventRepository(db, tenant_a.tenant_id).add(
                tenant_id=tenant_b.tenant_id,
                conversation_id=tenant_b.conversation_id,
                to_stage="INICIO",
                to_funnel="NUEVO",
                reason="turno",
            )

    def test_events_of_one_turn_keep_their_order(
        self, db: Session, tenant_a: TenantFixture, tenant_b: TenantFixture
    ) -> None:
        """Both events are written in one transaction. With `now()` they would
        carry the same timestamp and the history's order would be arbitrary."""
        events = ConversationStageEventRepository(db, tenant_a.tenant_id)
        for index in range(5):
            events.add(
                conversation_id=tenant_a.conversation_id,
                to_stage=f"S{index}",
                to_funnel="NUEVO",
                reason="turno",
            )

        history = events.for_conversation(tenant_a.conversation_id)

        assert [e.to_stage for e in history] == [f"S{index}" for index in range(5)]


class TestTheSchemaItself:
    def test_an_unknown_funnel_code_is_rejected_by_the_database(
        self, db: Session, tenant_a: TenantFixture
    ) -> None:
        with pytest.raises(IntegrityError), db.begin_nested():
            db.add(
                ConversationState(
                    conversation_id=tenant_a.conversation_id,
                    tenant_id=tenant_a.tenant_id,
                    template_key="productos",
                    template_version=1,
                    stage_key="INICIO",
                    funnel_code="INVENTADO",
                )
            )
            db.flush()

    def test_a_state_starts_at_version_one(self, db: Session, tenant_a: TenantFixture) -> None:
        state = ConversationStateRepository(db, tenant_a.tenant_id).add(
            conversation_id=tenant_a.conversation_id,
            template_key="productos",
            template_version=1,
            stage_key="INICIO",
            funnel_code="NUEVO",
        )
        db.refresh(state)

        assert state.version == 1
        assert state.facts == {}
        assert state.is_sandbox is False

    def test_a_conversation_has_one_state_only(self, db: Session, tenant_a: TenantFixture) -> None:
        states = ConversationStateRepository(db, tenant_a.tenant_id)
        values = {
            "conversation_id": tenant_a.conversation_id,
            "template_key": "productos",
            "template_version": 1,
            "stage_key": "INICIO",
            "funnel_code": "NUEVO",
        }
        states.add(**values)

        with pytest.raises(IntegrityError), db.begin_nested():
            states.add(**values)
