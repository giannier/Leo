"""Conversation, message and trace repositories."""

from __future__ import annotations

import uuid
from collections.abc import Sequence

from app.models.conversation import AgentTrace, Conversation, Message
from app.repositories.base import BaseRepository, TenantScopeError

# The one-way progression a delivery makes. `failed` is deliberately not on
# this line: it is terminal, it can happen from any point, and it is handled
# on its own in `set_delivery_status` rather than by being ranked.
_DELIVERY_ORDER = ("queued", "sent", "delivered", "read")


def _delivery_rank(status: str | None) -> int:
    """How far along the line a status is; -1 for anything not on it."""
    if status is None or status not in _DELIVERY_ORDER:
        return -1
    return _DELIVERY_ORDER.index(status)


class ConversationRepository(BaseRepository[Conversation]):
    """Conversation threads for one tenant."""

    model = Conversation

    def list_open(self, *, channel: str | None = None) -> Sequence[Conversation]:
        criteria = [Conversation.status == "open"]
        if channel is not None:
            criteria.append(Conversation.channel == channel)
        return self.list(*criteria)

    def open_for_contact(self, contact_id: uuid.UUID) -> Conversation | None:
        """The live WhatsApp thread with one person, if there is one.

        At most one is expected - a new thread is only opened after the old
        one is closed - but the newest is taken rather than asserting on it,
        because a race between two workers could briefly produce two and
        answering in the older one is a worse outcome than the anomaly itself.
        """
        statement = (
            self._scoped()
            .where(
                Conversation.contact_id == contact_id,
                Conversation.channel == "whatsapp",
                Conversation.status == "open",
            )
            .order_by(Conversation.created_at.desc())
        )
        return self._session.execute(statement).scalars().first()


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
        msg_type: str = "text",
        provider_message_id: str | None = None,
        delivery_status: str | None = None,
    ) -> Message:
        """Add a message, refusing to write into another tenant's thread.

        The last three arguments belong to the real channel (Feature 002) and
        default to what the sandbox means: a text message that was never sent
        anywhere, so no provider ever named it and it has no delivery state.
        """
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
            msg_type=msg_type,
            provider_message_id=provider_message_id,
            delivery_status=delivery_status,
        )

    def by_provider_message_id(self, provider_message_id: str) -> Message | None:
        """Find the message a provider is talking about, within this tenant.

        Used by the status webhooks, which name a message by the provider's
        id and nothing else. Scoped anyway: the id is unique globally, so an
        unscoped lookup would happily hand back another tenant's message to
        whoever guessed it.
        """
        statement = self._scoped().where(Message.provider_message_id == provider_message_id)
        return self._session.execute(statement).scalar_one_or_none()

    def set_delivery_status(self, message: Message, *, status: str) -> Message:
        """Advance the delivery state, never backwards.

        `delivered` and `read` webhooks arrive out of order often enough that
        a naive assignment would show a message as merely delivered after the
        customer had already read it.

        `failed` is terminal. It can be reached from any point on the line and
        nothing supersedes it - not even a `delivered` that turns up
        afterwards. That is a deliberate choice about which mistake to prefer:
        a message wrongly left as failed shows the tenant a problem to look
        at, while a failure wrongly overwritten by a late success is a lost
        sale nobody is ever told about.
        """
        if message.tenant_id != self.tenant_id:
            raise TenantScopeError(
                f"Message {message.id!s} does not belong to tenant {self.tenant_id!s}"
            )
        if message.delivery_status == "failed":
            return message
        if status == "failed" or _delivery_rank(status) > _delivery_rank(message.delivery_status):
            message.delivery_status = status
            self._session.flush()
        return message


class AgentTraceRepository(BaseRepository[AgentTrace]):
    """Reasoning traces for one tenant."""

    model = AgentTrace

    def for_message(self, message_id: uuid.UUID) -> Sequence[AgentTrace]:
        return self.list(AgentTrace.message_id == message_id)
