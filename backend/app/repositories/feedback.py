"""Simulator ratings of one business (Feature 018, TASK-1804)."""

from __future__ import annotations

import uuid
from collections.abc import Collection

from app.models.feedback import SandboxFeedback
from app.repositories.base import BaseRepository


class SandboxFeedbackRepository(BaseRepository[SandboxFeedback]):
    model = SandboxFeedback

    def by_message(self, message_id: uuid.UUID) -> SandboxFeedback | None:
        statement = self._scoped().where(SandboxFeedback.message_id == message_id)
        return self._session.execute(statement).scalar_one_or_none()

    def for_messages(self, message_ids: Collection[uuid.UUID]) -> dict[uuid.UUID, SandboxFeedback]:
        if not message_ids:
            return {}
        statement = self._scoped().where(SandboxFeedback.message_id.in_(list(message_ids)))
        return {
            row.message_id: row
            for row in self._session.execute(statement).scalars()
            if row.message_id is not None
        }

    def recent(self, *, limit: int = 50) -> list[SandboxFeedback]:
        statement = self._scoped().order_by(SandboxFeedback.created_at.desc()).limit(limit)
        return list(self._session.execute(statement).scalars())
