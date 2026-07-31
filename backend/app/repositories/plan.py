"""Access to the plan catalogue.

Plans are global reference data keyed by a natural id (`trial`, `emprende`,
...), not tenant-scoped rows, so this is a plain repository rather than a
`BaseRepository`. Reading limits from here instead of hardcoding them is the
point: the trial length, seat count and quotas live in one row that Feature
009 can change without touching this code.
"""

from __future__ import annotations

from collections.abc import Sequence

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.plan import Plan

TRIAL_PLAN_ID = "trial"


class PlanRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def get(self, plan_id: str) -> Plan | None:
        return self._session.get(Plan, plan_id)

    def list_active(self) -> Sequence[Plan]:
        statement = select(Plan).where(Plan.is_active.is_(True)).order_by(Plan.id)
        return self._session.execute(statement).scalars().all()
