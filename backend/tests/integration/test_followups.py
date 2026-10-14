"""Follow-ups and stale drafts against the database (Feature 004, TASK-444, C-13).

The conditions themselves are in `tests/unit/test_followup_rules.py`; here: the
message is written `queued` with the stage's instruction, the attempt is claimed so
a second writer sends nothing, the last attempt registers its promotion, the
customer writing again resets the count, and a week-old draft is discarded.
"""

from __future__ import annotations

from datetime import UTC, datetime, time, timedelta
from decimal import Decimal
from typing import Any

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.agent.pipeline import InboundTurn
from app.models.business import AgentConfig
from app.models.channel import Contact
from app.models.conversation import Conversation, Message
from app.models.conversation_state import ConversationStageEvent, FunnelCode
from app.models.orders import Order, PaymentProof
from app.models.sales_config import Promotion
from app.models.tenant import Tenant
from app.process.service import ProcessConfigCache, ProcessConfigService
from app.process.state import StateRepo
from app.process.templates.base import Stage
from app.providers.llm import ChatResult, MockLLMProvider
from app.services import followups as module
from app.services.followups import FollowUps, discard_stale_drafts, due_candidates
from tests.integration.test_turn_pipeline import _conversation, _pipeline, _stored, _tenant

LATER = timedelta(hours=5)


@pytest.fixture(autouse=True)
def _daytime(monkeypatch: pytest.MonkeyPatch) -> None:
    """Noon where the business is, whatever the hour the suite runs at."""
    monkeypatch.setattr(module, "local_time", lambda now, zone: time(12, 0))


def _ready(
    db: Session, *, stage: Stage = Stage.PEDIDO, max_intentos: int = 2
) -> tuple[Tenant, Conversation]:
    tenant = _tenant(db, template="productos")
    config = db.execute(select(AgentConfig).where(AgentConfig.tenant_id == tenant.id)).scalar_one()
    config.seguimiento_activo = True
    config.seguimiento_max = max_intentos
    conversation = _conversation(db, tenant, channel="whatsapp")
    contact = db.get(Contact, conversation.contact_id)
    assert contact is not None
    contact.last_inbound_at = datetime.now(UTC)
    process = ProcessConfigService(db, cache=ProcessConfigCache()).effective(tenant.id)
    states = StateRepo(db, tenant.id)
    state = states.get_or_create(conversation, process.template)
    states.save(
        conversation.id,
        stage=stage,
        funnel=FunnelCode.NEGOCIACION,
        facts_patch={"saludo_enviado": True},
        reason="test",
        expected_version=state.version,
    )
    db.add(
        Message(
            tenant_id=tenant.id,
            conversation_id=conversation.id,
            direction="outbound",
            author="agent",
            content="¿Te reservo las zapatillas en talla 40?",
            delivery_status="sent",
        )
    )
    db.flush()
    return tenant, conversation


def _llm(text: str = "¿Seguimos con tu pedido? Te faltan solo un par de pasos.") -> MockLLMProvider:
    llm = MockLLMProvider()
    llm.queue(ChatResult(content=text))
    return llm


class TestAFollowUpIsWritten:
    async def test_queued_with_the_stage_instruction_and_counted(self, db: Session) -> None:
        tenant, conversation = _ready(db)
        llm = _llm()

        result = await FollowUps(db, tenant.id, llm=llm).run(
            conversation.id, now=datetime.now(UTC) + LATER
        )

        assert result.outcome == "enviado" and result.message is not None
        assert result.message.delivery_status == "queued"
        assert result.message.payload == {"seguimiento": 1}
        call = llm.chat_calls[0]
        assert call.tools == ()
        assert "Recuérdale lo que lleva y lo que falta para completar." in call.messages[0].content
        assert "no ofrezcas descuentos" in call.messages[0].content
        assert _stored(db, tenant, conversation)["seguimiento"]["intentos"] == 1

    async def test_the_real_channel_only(self, db: Session) -> None:
        tenant, _ = _ready(db)
        simulated = _conversation(db, tenant)

        result = await FollowUps(db, tenant.id, llm=MockLLMProvider()).run(
            simulated.id, now=datetime.now(UTC) + LATER
        )

        assert result.outcome in {"simulador", "etapa", "sin_respuesta_nuestra"}

    async def test_a_second_writer_loses_the_claim_and_sends_nothing(
        self, db: Session, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Another worker saves between our read and our claim: we stop."""
        tenant, conversation = _ready(db)
        original = StateRepo.save

        def concurrent(self: StateRepo, conversation_id: Any, **kwargs: Any) -> Any:
            if kwargs.get("reason") == module.REASON_FOLLOW_UP:
                monkeypatch.setattr(StateRepo, "save", original)
                current = self.get(conversation_id)
                assert current is not None
                original(
                    self,
                    conversation_id,
                    stage=Stage(current.stage_key),
                    funnel=FunnelCode(current.funnel_code),
                    facts_patch={"seguimiento": {"intentos": 1}},
                    reason="otro worker",
                    expected_version=current.version,
                )
            return original(self, conversation_id, **kwargs)

        monkeypatch.setattr(StateRepo, "save", concurrent)
        llm = MockLLMProvider()

        result = await FollowUps(db, tenant.id, llm=llm).run(
            conversation.id, now=datetime.now(UTC) + LATER
        )

        assert result.outcome == "ocupado"
        assert llm.chat_calls == []

    async def test_once_sent_the_next_run_waits(self, db: Session) -> None:
        tenant, conversation = _ready(db)
        moment = datetime.now(UTC) + LATER
        await FollowUps(db, tenant.id, llm=_llm()).run(conversation.id, now=moment)

        again = await FollowUps(db, tenant.id, llm=MockLLMProvider()).run(
            conversation.id, now=moment + timedelta(minutes=15)
        )

        assert again.outcome == "espera"

    async def test_the_last_attempt_offers_and_records_the_promotion(self, db: Session) -> None:
        tenant, conversation = _ready(db, max_intentos=1)
        promotion = Promotion(
            tenant_id=tenant.id,
            nombre="10 % si cierras hoy",
            descripcion="10 % de descuento en tu pedido",
            uso="cierre",
            tipo="porcentaje",
            valor=Decimal("10.00"),
            vigencia_horas=24,
            para_seguimiento=True,
        )
        db.add(promotion)
        db.flush()
        llm = _llm()
        moment = datetime.now(UTC) + LATER

        result = await FollowUps(db, tenant.id, llm=llm).run(conversation.id, now=moment)

        assert result.outcome == "enviado"
        assert "«10 % si cierras hoy»" in llm.chat_calls[0].messages[0].content
        offered = _stored(db, tenant, conversation)["promos_ofrecidas"][str(promotion.id)]
        assert datetime.fromisoformat(offered) == moment + timedelta(hours=24)

    async def test_the_customer_writing_resets_the_count(self, db: Session) -> None:
        tenant, conversation = _ready(db)
        await FollowUps(db, tenant.id, llm=_llm()).run(
            conversation.id, now=datetime.now(UTC) + LATER
        )
        assert "seguimiento" in _stored(db, tenant, conversation)
        answer = MockLLMProvider()
        answer.queue(ChatResult(content="¡Claro! Seguimos."))

        await _pipeline(
            db, tenant, conversation, answer, sandbox=False, outbound=None, queue_text=True
        ).run(conversation.id, InboundTurn(text="sí, sigo interesado"))

        assert "seguimiento" not in _stored(db, tenant, conversation)

    async def test_the_job_finds_real_conversations_in_selling_stages(self, db: Session) -> None:
        tenant, conversation = _ready(db)
        _, other = _ready(db, stage=Stage.CERRADO)

        found = due_candidates(db, limit=10_000)

        assert (tenant.id, conversation.id) in found
        assert all(pair[1] != other.id for pair in found)


def _draft(
    db: Session, tenant: Tenant, conversation: Conversation, *, days: int, numero: int
) -> Order:
    assert conversation.contact_id is not None
    order = Order(
        tenant_id=tenant.id,
        conversation_id=conversation.id,
        contact_id=conversation.contact_id,
        numero=numero,
        estado="borrador",
        moneda="PEN",
        is_sandbox=False,
        last_activity_at=datetime.now(UTC) - timedelta(days=days),
    )
    db.add(order)
    db.flush()
    return order


class TestStaleDrafts:
    def test_a_week_old_draft_is_discarded_with_an_event(
        self, db: Session, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(db, "commit", db.flush)
        tenant, conversation = _ready(db)
        old = _draft(db, tenant, conversation, days=8, numero=1)
        recent = _draft(db, tenant, _conversation(db, tenant, channel="whatsapp"), days=6, numero=2)

        discard_stale_drafts(db)

        assert old.estado == "descartado"
        assert recent.estado == "borrador"
        reasons = (
            db.execute(
                select(ConversationStageEvent.reason).where(
                    ConversationStageEvent.conversation_id == conversation.id
                )
            )
            .scalars()
            .all()
        )
        assert "pedido_descartado" in reasons

    def test_a_draft_with_a_receipt_is_left_for_a_person(
        self, db: Session, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(db, "commit", db.flush)
        tenant, conversation = _ready(db)
        order = _draft(db, tenant, conversation, days=10, numero=1)
        db.add(
            PaymentProof(
                tenant_id=tenant.id,
                order_id=order.id,
                conversation_id=conversation.id,
                estado="por_verificar",
                storage_key="proofs/x.png",
            )
        )
        db.flush()

        discard_stale_drafts(db)

        assert order.estado == "borrador"
