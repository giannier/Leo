"""Conversation, message and trace repositories."""

from __future__ import annotations

import uuid
from collections.abc import Sequence

from app.models.conversation import AgentTrace, Conversation, Message
from app.repositories.base import BaseRepository, TenantScopeError


class ConversationRepository(BaseRepository[Conversation]):
    """Conversation threads for one tenant."""

    model = Conversation

    def list_open(self, *, channel: str | None = None) -> Sequence[Conversation]:
        criteria = [Conversation.status == "open"]
        if channel is not None:
            criteria.append(Conversation.channel == channel)
        return self.list(*criteria)


class MessageRepository(BaseRepository[Message]):
    """Messages for one tenant."""

    model = Message

    def list_for_conversation(
        self, conversation_id: uuid.UUID, *, limit: int | None = None
    ) -> Sequence[Message]:
        """Transcript of one thread, oldest first.

        The conversation id alone is not enough: it is a UUID supplied by the
        caller, so it must still be filtered by tenant. That happens in
        `_scoped()`, which is why this cannot be a bare query.
        """
        statement = (
            self._scoped()
            .where(Message.conversation_id == conversation_id)
            .order_by(Message.created_at)
        )
        if limit is not None:
            statement = statement.limit(limit)
        return self._session.execute(statement).scalars().all()

    def append(
        self,
        *,
        conversation_id: uuid.UUID,
        direction: str,
        author: str,
        content: str,
    ) -> Message:
        """Add a message, refusing to write into another tenant's thread."""
        conversations = ConversationRepository(self._session, self.tenant_id)
        if not conversations.exists(conversation_id):
            raise TenantScopeError(
                f"Conversation {conversation_id!s} does not belong to tenant {self.tenant_id!s}"
            )
        return self.add(
            conversation_id=conversation_id,
            direction=direction,
            author=author,
            content=content,
        )


class AgentTraceRepository(BaseRepository[AgentTrace]):
    """Reasoning traces for one tenant."""

    model = AgentTrace

    def for_message(self, message_id: uuid.UUID) -> Sequence[AgentTrace]:
        return self.list(AgentTrace.message_id == message_id)
