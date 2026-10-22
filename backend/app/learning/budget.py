"""What learning may spend: off until the owner turns it on, and a ceiling of model calls a week.

Every analysis of a conversation is a call to the model, and the cost of a call is the business's
(decision of Giannier, 2026-10-21): it starts switched off, and once on it can make `tope_semanal`
calls a week (20 by default) and then waits for the next Monday. The week is the Monday that
starts it, in UTC.
"""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

from sqlalchemy.orm import Session

from app.models.learning import LearningSettings
from app.repositories.learning import LearningSettingsRepository, LearningUsageRepository

# Audio is transcribed only when a business imports a chat, and each import has its own ceiling.
AUDIO_MINUTES_PER_IMPORT = Decimal(30)


def week_start(moment: datetime | None = None) -> date:
    day = (moment or datetime.now(UTC)).astimezone(UTC).date()
    return day - timedelta(days=day.weekday())


class LearningBudget:
    def __init__(
        self, session: Session, tenant_id: uuid.UUID, *, now: datetime | None = None
    ) -> None:
        self._settings = LearningSettingsRepository(session, tenant_id)
        self._usage = LearningUsageRepository(session, tenant_id)
        self._week = week_start(now)

    @property
    def settings(self) -> LearningSettings:
        return self._settings.ensure()

    @property
    def enabled(self) -> bool:
        return self.settings.activo

    @property
    def ceiling(self) -> int:
        return self.settings.tope_semanal

    @property
    def used(self) -> int:
        row = self._usage.week(self._week)
        return row.llamadas if row else 0

    @property
    def audio_minutes(self) -> Decimal:
        row = self._usage.week(self._week)
        return row.minutos_audio if row else Decimal(0)

    @property
    def left(self) -> int:
        return max(0, self.ceiling - self.used)

    def spend(self, calls: int = 1) -> bool:
        """Take `calls` from the week. False: the ceiling would be passed (and nothing is taken)."""
        return self._usage.spend_calls(self._week, calls, self.ceiling)

    def spend_audio(self, minutes: Decimal) -> None:
        self._usage.add_audio(self._week, minutes)
