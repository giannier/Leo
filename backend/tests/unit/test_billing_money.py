"""Billing arithmetic (Feature 009): exact to the cent, always in the customer's favour.

Property-based where the claim is general (Hypothesis, approved 2026-10-14).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from hypothesis import given
from hypothesis import strategies as st

from app.services.billing.money import (
    REFERENCE_ALPHABET,
    add_months,
    charge_amount,
    money,
    new_reference,
    percent_off,
    period_end,
    unused_credit,
)

prices = st.decimals(min_value=Decimal("0.01"), max_value=Decimal("9999.99"), places=2)
moments = st.datetimes(
    min_value=datetime(2026, 1, 1), max_value=datetime(2030, 12, 31), timezones=st.just(UTC)
)


class TestCredit:
    @given(price=prices, start=moments, elapsed=st.floats(min_value=0, max_value=1))
    def test_the_credit_is_between_zero_and_the_price_with_two_places(
        self, price: Decimal, start: datetime, elapsed: float
    ) -> None:
        end = add_months(start, 1)
        now = start + (end - start) * elapsed
        credit = unused_credit(price, start, end, now)
        assert Decimal("0.00") <= credit <= price
        assert credit == credit.quantize(Decimal("0.01"))

    @given(price=prices, start=moments, elapsed=st.floats(min_value=0.01, max_value=0.99))
    def test_it_never_credits_less_than_the_exact_unused_share(
        self, price: Decimal, start: datetime, elapsed: float
    ) -> None:
        end = add_months(start, 1)
        now = start + (end - start) * elapsed
        exact = (
            price
            * Decimal(str((end - now).total_seconds()))
            / Decimal(str((end - start).total_seconds()))
        )
        credit = unused_credit(price, start, end, now)
        assert credit >= exact - Decimal("0.000001")
        assert credit - exact < Decimal("0.01")

    def test_half_a_month_of_29_dollars(self) -> None:
        start = datetime(2026, 4, 1, tzinfo=UTC)
        end = add_months(start, 1)  # 30 days
        assert unused_credit(Decimal("29.00"), start, end, start + timedelta(days=15)) == Decimal(
            "14.50"
        )
        # A third of the period left: 9.666... is credited as 9.67.
        assert unused_credit(Decimal("29.00"), start, end, start + timedelta(days=20)) == Decimal(
            "9.67"
        )

    def test_before_and_after_the_period(self) -> None:
        start = datetime(2026, 4, 1, tzinfo=UTC)
        end = add_months(start, 1)
        assert unused_credit(Decimal("59.00"), start, end, start) == Decimal("59.00")
        assert unused_credit(Decimal("59.00"), start, end, end) == Decimal("0.00")


class TestDiscounts:
    @given(amount=prices, percent=st.integers(min_value=0, max_value=100))
    def test_a_coupon_never_takes_off_more_than_the_amount_nor_less_than_its_share(
        self, amount: Decimal, percent: int
    ) -> None:
        discount = percent_off(amount, percent)
        assert Decimal("0") <= discount <= amount
        assert discount >= amount * percent / 100
        assert discount == discount.quantize(Decimal("0.01"))

    def test_examples(self) -> None:
        assert percent_off(Decimal("29.00"), 15) == Decimal("4.35")
        assert percent_off(Decimal("59.00"), 33) == Decimal("19.47")  # 19.47 exactly
        assert percent_off(Decimal("8.00"), 100) == Decimal("8.00")

    @given(list_amount=prices, discount=prices, credit=prices)
    def test_what_is_owed_is_never_negative(
        self, list_amount: Decimal, discount: Decimal, credit: Decimal
    ) -> None:
        assert charge_amount(list_amount, discount=discount, credit=credit) >= 0

    def test_upgrade_from_emprende_to_crece_half_way(self) -> None:
        start = datetime(2026, 4, 1, tzinfo=UTC)
        credit = unused_credit(
            Decimal("29.00"), start, add_months(start, 1), start + timedelta(days=15)
        )
        assert charge_amount(Decimal("59.00"), discount=Decimal("0"), credit=credit) == Decimal(
            "44.50"
        )


class TestCalendar:
    @pytest.mark.parametrize(
        ("start", "months", "expected"),
        [
            (datetime(2026, 1, 31, tzinfo=UTC), 1, datetime(2026, 2, 28, tzinfo=UTC)),
            (datetime(2028, 1, 31, tzinfo=UTC), 1, datetime(2028, 2, 29, tzinfo=UTC)),
            (datetime(2026, 12, 15, tzinfo=UTC), 1, datetime(2027, 1, 15, tzinfo=UTC)),
            (datetime(2026, 3, 31, tzinfo=UTC), 12, datetime(2027, 3, 31, tzinfo=UTC)),
        ],
    )
    def test_months_are_calendar_months(
        self, start: datetime, months: int, expected: datetime
    ) -> None:
        assert add_months(start, months) == expected

    @given(start=moments)
    def test_a_period_always_moves_forward(self, start: datetime) -> None:
        assert period_end(start, "monthly") > start
        assert period_end(start, "annual") > period_end(start, "monthly")


def test_money_rounds_typed_amounts_half_up() -> None:
    assert money("10.005") == Decimal("10.01")
    assert money(8) == Decimal("8.00")


def test_references_are_unambiguous() -> None:
    reference = new_reference()
    assert reference.startswith("LEO-") and len(reference) == 10
    assert set(reference[4:]) <= set(REFERENCE_ALPHABET)
