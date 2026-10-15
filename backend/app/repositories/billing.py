"""Billing repositories (Feature 009): every read and write bound to one business."""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from datetime import datetime
from decimal import Decimal
from typing import Any

from app.models.billing import BillingLedger, Charge, Payment, Subscription, UsagePeriod
from app.repositories.base import BaseRepository


class SubscriptionRepository(BaseRepository[Subscription]):
    model = Subscription

    def current(self, *, lock: bool = False) -> Subscription | None:
        statement = self._scoped()
        if lock:
            statement = statement.with_for_update()
        return self._session.execute(statement).scalar_one_or_none()


class ChargeRepository(BaseRepository[Charge]):
    model = Charge

    def by_reference(self, reference: str, *, lock: bool = False) -> Charge | None:
        statement = self._scoped().where(Charge.reference == reference)
        if lock:
            statement = statement.with_for_update()
        return self._session.execute(statement).scalar_one_or_none()

    def pending(self) -> Sequence[Charge]:
        statement = (
            self._scoped()
            .where(Charge.status == "pending")
            .order_by(Charge.created_at.desc(), Charge.id)
        )
        return self._session.execute(statement).scalars().all()

    def pending_renewal_for(self, period_start: datetime) -> Charge | None:
        statement = self._scoped().where(
            Charge.kind == "renewal",
            Charge.status.in_(("pending", "paid")),
            Charge.period_start == period_start,
        )
        return self._session.execute(statement).scalars().first()


class PaymentRepository(BaseRepository[Payment]):
    model = Payment

    def history(self, limit: int = 50) -> Sequence[Payment]:
        statement = self._scoped().order_by(Payment.created_at.desc(), Payment.id).limit(limit)
        return self._session.execute(statement).scalars().all()


class UsagePeriodRepository(BaseRepository[UsagePeriod]):
    model = UsagePeriod

    def open_period(self, *, lock: bool = False) -> UsagePeriod | None:
        """The period the counter is running in (the latest one not closed)."""
        statement = (
            self._scoped()
            .where(UsagePeriod.closed.is_(False))
            .order_by(UsagePeriod.period_start.desc())
            .limit(1)
        )
        if lock:
            statement = statement.with_for_update()
        return self._session.execute(statement).scalar_one_or_none()


class LedgerRepository(BaseRepository[BillingLedger]):
    model = BillingLedger

    def append(
        self, event: str, *, amount_usd: Decimal | None = None, **detail: Any
    ) -> BillingLedger:
        return self.add(event=event, amount_usd=amount_usd, detail=_jsonable(detail))

    def entries(self) -> Sequence[BillingLedger]:
        statement = self._scoped().order_by(BillingLedger.id)
        return self._session.execute(statement).scalars().all()


def _jsonable(detail: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for key, value in detail.items():
        if isinstance(value, (Decimal, uuid.UUID)):
            out[key] = str(value)
        elif isinstance(value, datetime):
            out[key] = value.isoformat()
        else:
            out[key] = value
    return out
