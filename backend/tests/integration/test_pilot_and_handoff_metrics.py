"""Pilot mode and how fast a person answers after a handover (feature 019, phase C)."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.agent.pipeline import InboundTurn
from app.models.business import AgentConfig
from app.models.channel import Contact
from app.models.conversation import Conversation, Message
from app.models.conversation_state import ConversationStageEvent
from app.providers.llm import MockLLMProvider
from app.services.metrics.handoffs import handoff_response
from app.services.pilot import BUCKETS, bucket, in_pilot
from tests.integration.test_turn_pipeline import _conversation, _pipeline, _tenant


def _contact_in_bucket(db: Session, tenant_id: uuid.UUID, wanted: int) -> Contact:
    """A contact whose bucket is exactly `wanted` (ids are random: draw until one fits)."""
    while True:
        candidate = uuid.uuid4()
        if bucket(candidate) == wanted:
            contact = Contact(
                id=candidate,
                tenant_id=tenant_id,
                phone_e164=f"+5198{uuid.uuid4().int % 10**7:07d}",
                last_inbound_at=datetime.now(UTC),
            )
            db.add(contact)
            db.flush()
            return contact


def _share(db: Session, tenant_id: uuid.UUID, pct: int) -> None:
    config = db.get(AgentConfig, tenant_id)
    assert config is not None
    config.piloto_pct = pct
    db.flush()


class TestPilotMode:
    def test_a_share_of_the_customers_and_always_the_same_ones(self, db: Session) -> None:
        tenant = _tenant(db, template="productos")
        _share(db, tenant.id, 25)
        ids = [uuid.uuid4() for _ in range(400)]

        mine = [c for c in ids if in_pilot(db, tenant.id, c)]

        assert 60 <= len(mine) <= 140  # about a quarter of 400
        assert mine == [c for c in ids if in_pilot(db, tenant.id, c)]  # stable
        assert all(bucket(c) < 25 for c in mine)

    @pytest.mark.parametrize(("pct", "everyone", "nobody"), [(100, True, False), (0, False, True)])
    def test_the_extremes(self, db: Session, pct: int, everyone: bool, nobody: bool) -> None:
        tenant = _tenant(db, template="productos")
        _share(db, tenant.id, pct)
        sample = [uuid.uuid4() for _ in range(50)]

        answered = [in_pilot(db, tenant.id, c) for c in sample]

        assert all(answered) is everyone and (not any(answered)) is nobody
        assert BUCKETS == 100

    async def test_a_customer_outside_the_pilot_waits_for_the_team(self, db: Session) -> None:
        tenant = _tenant(db, template="productos")
        _share(db, tenant.id, 50)
        outside = _contact_in_bucket(db, tenant.id, 75)
        conversation = Conversation(
            tenant_id=tenant.id, channel="whatsapp", contact_id=outside.id, status="open"
        )
        db.add(conversation)
        db.flush()
        llm = MockLLMProvider()

        result = await _pipeline(db, tenant, conversation, llm, sandbox=False, outbound=None).run(
            conversation.id, InboundTurn(text="hola")
        )

        assert (result.outcome, result.gate) == ("gated", "outside_pilot")
        assert llm.chat_calls == [] and result.messages == []

    async def test_a_customer_inside_the_pilot_is_answered_and_the_simulator_always_is(
        self, db: Session
    ) -> None:
        tenant = _tenant(db, template="productos")
        _share(db, tenant.id, 50)
        inside = _contact_in_bucket(db, tenant.id, 10)
        real = Conversation(
            tenant_id=tenant.id, channel="whatsapp", contact_id=inside.id, status="open"
        )
        db.add(real)
        db.flush()
        simulated = _conversation(db, tenant, channel="sandbox")

        answered = await _pipeline(
            db, tenant, real, MockLLMProvider(), sandbox=False, outbound=None
        ).run(real.id, InboundTurn(text="hola"))
        tried = await _pipeline(db, tenant, simulated, MockLLMProvider()).run(
            simulated.id, InboundTurn(text="hola")
        )

        assert answered.outcome == "answered" and tried.outcome == "answered"


def _handed_over(
    db: Session, tenant_id: uuid.UUID, conversation: Conversation, at: datetime
) -> None:
    db.add(
        ConversationStageEvent(
            tenant_id=tenant_id,
            conversation_id=conversation.id,
            from_stage="IDENTIFICAR",
            to_stage="DERIVADO",
            from_funnel="negociacion",
            to_funnel="derivado",
            reason="herramienta:derivar_a_humano",
            created_at=at,
        )
    )
    db.flush()


def _person_writes(
    db: Session, tenant_id: uuid.UUID, conversation: Conversation, at: datetime
) -> None:
    db.add(
        Message(
            tenant_id=tenant_id,
            conversation_id=conversation.id,
            direction="outbound",
            author="human_operator",
            content="Hola, te ayudo yo",
            created_at=at,
        )
    )
    db.flush()


class TestHowFastAPersonAnswers:
    def test_median_p90_and_the_ones_nobody_answered(self, db: Session) -> None:
        tenant = _tenant(db, template="productos")
        today = datetime.now(UTC).date()
        start = datetime.now(UTC) - timedelta(hours=10)
        waits = [2, 4, 6, 8, 30]  # minutes
        for minutes in waits:
            conversation = _conversation(db, tenant, channel="whatsapp")
            _handed_over(db, tenant.id, conversation, start)
            _person_writes(db, tenant.id, conversation, start + timedelta(minutes=minutes))
        nobody = _conversation(db, tenant, channel="whatsapp")
        _handed_over(db, tenant.id, nobody, start)

        report = handoff_response(db, tenant.id, today - timedelta(days=1), today)

        assert report["total"] == 6 and report["atendidos"] == 5 and report["sin_atender"] == 1
        assert report["mediana_minutos"] == 6.0
        assert report["p90_minutos"] == 30.0

    def test_a_reply_before_the_handover_or_a_week_later_does_not_count_and_the_simulator_never(
        self, db: Session
    ) -> None:
        tenant = _tenant(db, template="productos")
        today = datetime.now(UTC).date()
        start = datetime.now(UTC) - timedelta(hours=10)
        before = _conversation(db, tenant, channel="whatsapp")
        _person_writes(db, tenant.id, before, start - timedelta(minutes=5))
        _handed_over(db, tenant.id, before, start)
        late = _conversation(db, tenant, channel="whatsapp")
        _handed_over(db, tenant.id, late, start)
        _person_writes(db, tenant.id, late, start + timedelta(days=8))
        simulated = _conversation(db, tenant, channel="sandbox")
        _handed_over(db, tenant.id, simulated, start)

        report = handoff_response(db, tenant.id, today - timedelta(days=1), today)

        assert (
            report["total"] == 2 and report["atendidos"] == 0 and report["mediana_minutos"] is None
        )

    def test_another_business_s_handovers_are_not_mine(self, db: Session) -> None:
        mine, other = _tenant(db, template="productos"), _tenant(db, template="productos")
        today = datetime.now(UTC).date()
        conversation = _conversation(db, other, channel="whatsapp")
        _handed_over(db, other.id, conversation, datetime.now(UTC) - timedelta(hours=1))

        assert handoff_response(db, mine.id, today - timedelta(days=1), today)["total"] == 0
        assert db.execute(select(ConversationStageEvent)).first() is not None


class TestThePilotInTheBehavior:
    async def test_the_owner_sets_the_share_and_an_empty_value_leaves_it(self, api, mail) -> None:  # type: ignore[no-untyped-def]
        from tests.integration.test_assistant_config_api import _account

        _, headers = await _account(api, mail)
        url = "/api/v1/assistant/behavior"

        default = (await api.get(url, headers=headers)).json()["piloto_pct"]
        half = await api.put(url, headers=headers, json={"piloto_pct": 50})
        untouched = await api.put(url, headers=headers, json={"agent_name": "Lucía"})
        nothing = await api.put(url, headers=headers, json={"piloto_pct": None})
        too_much = await api.put(url, headers=headers, json={"piloto_pct": 150})

        assert default == 100 and half.json()["piloto_pct"] == 50
        assert untouched.json()["piloto_pct"] == 50 and nothing.json()["piloto_pct"] == 50
        assert too_much.status_code == 400
