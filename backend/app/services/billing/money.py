"""Money and calendar arithmetic for billing (Feature 009). Pure functions.

Every amount is a `Decimal` with two places, in dollars; nothing here touches a
float (Constitution). Rounding always favours the customer (009 plan, "redondeo
a favor del cliente"): a discount or a credit is rounded **up** to the cent, so
what is charged is rounded down.
"""

from __future__ import annotations

import calendar
import secrets
from datetime import datetime, timedelta
from decimal import ROUND_CEILING, ROUND_HALF_UP, Decimal

CENT = Decimal("0.01")
ZERO = Decimal("0.00")
# Unambiguous on a phone screen and read aloud: no 0/O, 1/I/L.
REFERENCE_ALPHABET = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"


def money(value: Decimal | int | str) -> Decimal:
    """An exact amount, two places (half up, for values typed by a person)."""
    return Decimal(value).quantize(CENT, rounding=ROUND_HALF_UP)


def in_favour(value: Decimal) -> Decimal:
    """A discount or credit rounded up to the cent: the customer never loses the fraction."""
    return value.quantize(CENT, rounding=ROUND_CEILING)


def percent_off(amount: Decimal, percent: int) -> Decimal:
    """The discount a coupon of `percent` takes off `amount`, rounded in the customer's favour."""
    if not 0 <= percent <= 100:
        raise ValueError("percent must be between 0 and 100")
    return min(amount, in_favour(amount * Decimal(percent) / Decimal(100)))


def add_months(start: datetime, months: int) -> datetime:
    """The same day `months` later, clamped to the end of a shorter month (31 Jan -> 28 Feb)."""
    total = start.month - 1 + months
    year, month = start.year + total // 12, total % 12 + 1
    day = min(start.day, calendar.monthrange(year, month)[1])
    return start.replace(year=year, month=month, day=day)


def period_end(start: datetime, cycle: str) -> datetime:
    if cycle == "monthly":
        return add_months(start, 1)
    if cycle == "annual":
        return add_months(start, 12)
    raise ValueError(f"unknown billing cycle {cycle!r}")


def unused_credit(
    price: Decimal, period_start: datetime, period_end_: datetime, now: datetime
) -> Decimal:
    """What is left of a paid period, in money: linear by time, rounded up (009 HU-03).

    Measured in seconds, so an upgrade in the middle of a day credits that day's
    unused part too. Never negative, never more than the price.
    """
    total = (period_end_ - period_start).total_seconds()
    if total <= 0 or now >= period_end_:
        return ZERO
    if now <= period_start:
        return money(price)
    left = Decimal(str((period_end_ - now).total_seconds())) / Decimal(str(total))
    return min(money(price), in_favour(price * left))


def charge_amount(list_amount: Decimal, *, discount: Decimal, credit: Decimal) -> Decimal:
    """What is owed: never below zero."""
    return max(ZERO, money(list_amount) - discount - credit)


def remaining_days(until: datetime | None, now: datetime) -> timedelta:
    """Whole time left until `until` (a trial's end), zero when it is past (C-005)."""
    if until is None or until <= now:
        return timedelta(0)
    return until - now


def new_reference() -> str:
    """`LEO-XXXXXX`: what the business writes in the transfer's description."""
    return "LEO-" + "".join(secrets.choice(REFERENCE_ALPHABET) for _ in range(6))
