"""The per-turn outbound message budget (TASK-415, C-11)."""

from __future__ import annotations

import pytest

from app.agent.budget import TurnBudget


def test_the_default_is_three_messages_with_one_kept_for_the_text() -> None:
    budget = TurnBudget()

    assert (budget.max, budget.reserva_texto) == (3, 1)
    assert budget.remaining_for_tools == 2
    assert budget.remaining_text_bubbles() == 3


def test_tools_can_spend_two_and_the_third_is_refused() -> None:
    budget = TurnBudget()

    assert budget.consume() is True
    assert budget.consume() is True
    assert budget.can_consume() is False
    assert budget.consume() is False
    assert budget.spent == 2


def test_a_refused_consume_spends_nothing() -> None:
    budget = TurnBudget()
    budget.consume()
    budget.consume()

    budget.consume()

    assert budget.spent == 2
    assert budget.remaining_text_bubbles() == 1


@pytest.mark.parametrize(("spent", "bubbles"), [(0, 3), (1, 2), (2, 1)])
def test_the_text_gets_what_the_tools_left(spent: int, bubbles: int) -> None:
    budget = TurnBudget()
    for _ in range(spent):
        budget.consume()

    assert budget.remaining_text_bubbles() == bubbles


def test_the_text_reserve_is_never_spendable() -> None:
    budget = TurnBudget(max=2, reserva_texto=2)

    assert budget.consume() is False
    assert budget.remaining_text_bubbles() == 2


def test_with_no_reserve_the_tools_can_use_it_all() -> None:
    budget = TurnBudget(max=2, reserva_texto=0)

    assert [budget.consume() for _ in range(3)] == [True, True, False]
    assert budget.remaining_text_bubbles() == 0


def test_each_turn_has_its_own_budget() -> None:
    first, second = TurnBudget(), TurnBudget()
    first.consume()

    assert second.spent == 0


@pytest.mark.parametrize(("size", "reserve"), [(0, 0), (3, 4), (3, -1)])
def test_an_impossible_budget_is_rejected(size: int, reserve: int) -> None:
    with pytest.raises(ValueError):
        TurnBudget(max=size, reserva_texto=reserve)
