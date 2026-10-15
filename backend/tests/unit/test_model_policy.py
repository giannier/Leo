"""Intelligence levels and the model's circuit breaker (Feature 004, TASK-445, C-02)."""

from __future__ import annotations

import uuid

import pytest

from app.agent.circuit import OPEN_SECONDS, CircuitBreaker
from app.agent.model_policy import credits_for, effective_level, model_for
from app.core.config import get_settings
from tests.unit.test_locks import FakeStore


class TestTheLevel:
    @pytest.mark.parametrize(
        ("configured", "allowed", "expected"),
        [
            ("medio", ["economico", "medio", "mejor"], "medio"),
            ("mejor", ["economico", "medio"], "medio"),
            ("mejor", ["economico"], "economico"),
            ("medio", ["economico"], "economico"),
            (None, ["economico", "medio"], "economico"),
            ("raro", ["economico", "medio", "mejor"], "economico"),
        ],
    )
    def test_a_level_the_plan_does_not_allow_falls_to_the_highest_it_does(
        self, configured: str | None, allowed: list[str], expected: str
    ) -> None:
        assert effective_level(configured, allowed) == expected

    def test_an_unset_level_runs_the_profile_model(self, monkeypatch: pytest.MonkeyPatch) -> None:
        settings = get_settings()
        monkeypatch.setattr(settings, "llm_model_mejor", None)
        monkeypatch.setattr(settings, "llm_model_medio", "gpt-medio")

        assert model_for("mejor", settings) == settings.openai_chat_model
        assert model_for("medio", settings) == "gpt-medio"

    def test_credits_per_level_come_from_settings(self, monkeypatch: pytest.MonkeyPatch) -> None:
        settings = get_settings()
        monkeypatch.setattr(settings, "llm_credits_medio", 2)
        monkeypatch.setattr(settings, "llm_credits_mejor", 5)

        assert [credits_for(level, settings) for level in ("economico", "medio", "mejor")] == [
            1,
            2,
            5,
        ]
        assert credits_for(None, settings) == 1


class TestTheBreaker:
    async def test_three_failures_in_a_minute_open_it_once(self) -> None:
        store, opened = FakeStore(), []
        clock = iter([0.0, 10.0, 20.0, 30.0])
        breaker = CircuitBreaker(
            store, uuid.uuid4(), on_open=lambda: opened.append(1), clock=lambda: next(clock)
        )

        results = [await breaker.record_failure() for _ in range(3)]

        assert results == [False, False, True]
        assert await breaker.is_open()
        assert opened == [1]
        assert OPEN_SECONDS in store.ttls.values()
        # A fourth failure while open does not open (or alert) again.
        assert await breaker.record_failure() is False
        assert opened == [1]

    async def test_failures_spread_over_more_than_a_minute_do_not(self) -> None:
        clock = iter([0.0, 40.0, 80.0])
        breaker = CircuitBreaker(FakeStore(), uuid.uuid4(), clock=lambda: next(clock))

        assert [await breaker.record_failure() for _ in range(3)] == [False, False, False]
        assert not await breaker.is_open()

    async def test_each_tenant_has_its_own(self) -> None:
        store = FakeStore()
        a, b = CircuitBreaker(store, uuid.uuid4()), CircuitBreaker(store, uuid.uuid4())

        for _ in range(3):
            await a.record_failure()

        assert await a.is_open()
        assert not await b.is_open()
