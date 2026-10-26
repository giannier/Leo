"""The circuit breaker on the live channel, the level per plan and the memory window
(Feature 004, TASK-445, C-02)."""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy.orm import Session

from app.agent.circuit import CircuitBreaker
from app.agent.core import AgentCore
from app.agent.model_policy import chat_provider_for, tenant_level
from app.agent.pipeline import InboundTurn
from app.core.config import get_settings
from app.models.business import AgentConfig
from app.models.conversation import Message
from app.models.llm_settings import TenantLLMSettings
from app.providers.llm import MockLLMProvider
from app.services.channel import DEFAULT_UNSUPPORTED_FALLBACK, ConversationResponder
from tests.integration.test_turn_pipeline import _conversation, _pipeline, _say, _tenant
from tests.unit.test_locks import FakeStore


class TestTheBreakerOnTheLiveChannel:
    async def test_three_failures_open_it_and_the_fourth_customer_gets_the_fallback(
        self, db: Session
    ) -> None:
        tenant = _tenant(db, template="productos")
        store, opened = FakeStore(), []
        breaker = CircuitBreaker(store, tenant.id, on_open=lambda: opened.append(tenant.id))

        for _ in range(3):
            conversation = _conversation(db, tenant, channel="whatsapp")
            _say(db, conversation, "hola")
            # An empty mock raises LLMError: the provider is down.
            await ConversationResponder(
                db, tenant.id, llm=MockLLMProvider(), breaker=breaker
            ).respond_turn(conversation.id)
        assert opened == [tenant.id]

        conversation = _conversation(db, tenant, channel="whatsapp")
        _say(db, conversation, "¿hay alguien?")
        llm = MockLLMProvider()
        answers = await ConversationResponder(db, tenant.id, llm=llm, breaker=breaker).respond_turn(
            conversation.id
        )

        assert llm.chat_calls == []
        assert [answer.content for answer in answers] == [DEFAULT_UNSUPPORTED_FALLBACK]

    async def test_an_open_circuit_is_not_counted_as_an_agent_reply(self, db: Session) -> None:
        tenant = _tenant(db, template="productos")
        store = FakeStore()
        await store.set(f"llm:open:{tenant.id}", "1")
        conversation = _conversation(db, tenant, channel="whatsapp")
        _say(db, conversation, "hola")
        before = tenant.ai_messages_used

        await ConversationResponder(
            db, tenant.id, llm=MockLLMProvider(), breaker=CircuitBreaker(store, tenant.id)
        ).respond_turn(conversation.id)

        db.refresh(tenant)
        assert tenant.ai_messages_used == before

    async def test_a_handover_still_wins_over_the_fallback(self, db: Session) -> None:
        """Open circuit or not, a conversation a person has stays quiet."""
        tenant = _tenant(db, template="productos", active=False)
        conversation = _conversation(db, tenant, channel="whatsapp")

        result = await _pipeline(
            db,
            tenant,
            conversation,
            MockLLMProvider(),
            sandbox=False,
            outbound=None,
            llm_open=True,
        ).run(conversation.id, InboundTurn(text="hola"))

        assert (result.outcome, result.gate) == ("gated", "paused")


class TestTheLevel:
    def _configure(self, db: Session, level: str) -> uuid.UUID:
        tenant = _tenant(db, template="productos")
        config = db.get(AgentConfig, tenant.id)
        assert config is not None
        config.nivel_inteligencia = level
        db.flush()
        return tenant.id

    def test_the_trial_runs_economico_when_billing_is_on(self, db: Session) -> None:
        tenant_id = self._configure(db, "mejor")

        assert tenant_level(db, tenant_id) == "economico"

    def test_with_billing_off_the_chosen_level_runs(
        self, db: Session, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(get_settings(), "billing_enforced", False)
        tenant_id = self._configure(db, "mejor")

        assert tenant_level(db, tenant_id) == "mejor"

    def test_the_level_picks_its_model(self, db: Session, monkeypatch: pytest.MonkeyPatch) -> None:
        settings = get_settings()
        monkeypatch.setattr(settings, "billing_enforced", False)
        monkeypatch.setattr(settings, "llm_profile", "openai")
        monkeypatch.setattr(settings, "openai_api_key", "sk-test-" + "x" * 40)
        monkeypatch.setattr(settings, "llm_model_medio", "modelo-medio")
        tenant_id = self._configure(db, "medio")

        provider, level = chat_provider_for(db, tenant_id, settings)

        assert (level, provider.chat_model) == ("medio", "modelo-medio")

    def test_a_tenant_with_its_own_model_has_no_level(self, db: Session) -> None:
        tenant_id = self._configure(db, "mejor")
        db.add(TenantLLMSettings(tenant_id=tenant_id, mode="byo_openai"))
        db.flush()

        assert tenant_level(db, tenant_id) is None


class TestTheMemory:
    def test_the_model_sees_the_whole_conversation(self, db: Session) -> None:
        tenant = _tenant(db, template="productos")
        conversation = _conversation(db, tenant)
        for index in range(30):
            db.add(
                Message(
                    tenant_id=tenant.id,
                    conversation_id=conversation.id,
                    direction="inbound" if index % 2 == 0 else "outbound",
                    author="customer" if index % 2 == 0 else "agent",
                    content=f"mensaje {index}",
                )
            )
        db.flush()

        history = AgentCore(db, tenant.id, llm=MockLLMProvider()).history(conversation.id)

        # It used to be the last twenty: what was said at the start of a long order was lost.
        assert [m.content for m in history] == [f"mensaje {i}" for i in range(30)]
