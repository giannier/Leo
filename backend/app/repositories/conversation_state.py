"""Stage state and stage history of a conversation (Feature 004).

`StateRepo.get_or_create` / `save` with optimistic concurrency arrive with
TASK-412. This module holds the tenant-scoped access the tables need from the
day they exist, so that the isolation suite covers them from the start.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence

from app.models.conversation_state import ConversationStageEvent, ConversationState
from app.repositories.base import BaseRepository


class ConversationStateRepository(BaseRepository[ConversationState]):
    """The state row of each conversation of one tenant."""

    model = ConversationState

    def get(self, entity_id: uuid.UUID) -> ConversationState | None:
        """Fetch the state of the conversation `entity_id`.

        `conversation_state` has no `id` of its own: the conversation id *is*
        its key, so that is what the inherited methods receive here.
        """
        return self.by_conversation(entity_id)

    def by_conversation(self, conversation_id: uuid.UUID) -> ConversationState | None:
        statement = self._scoped().where(ConversationState.conversation_id == conversation_id)
        return self._session.execute(statement).scalar_one_or_none()


class ConversationStageEventRepository(BaseRepository[ConversationStageEvent]):
    """The stage history of one tenant's conversations. Append-only."""

    model = ConversationStageEvent

    def for_conversation(self, conversation_id: uuid.UUID) -> Sequence[ConversationStageEvent]:
        """Every change of one conversation, oldest first."""
        statement = (
            self._scoped()
            .where(ConversationStageEvent.conversation_id == conversation_id)
            .order_by(ConversationStageEvent.created_at, ConversationStageEvent.id)
        )
        return self._session.execute(statement).scalars().all()
