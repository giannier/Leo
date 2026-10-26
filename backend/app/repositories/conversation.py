"""Conversation, message and trace repositories."""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from datetime import datetime
from typing import Any

from sqlalchemy import func, select, update

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


# A thread item that is not a message: takeover, return, close (Feature 007).
EVENT = "event"


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
        self,
        conversation_id: uuid.UUID,
        *,
        limit: int | None = None,
        include_events: bool = False,
    ) -> Sequence[Message]:
        """Transcript of one thread, oldest first.

        The conversation id alone is not enough: it is a UUID supplied by the
        caller, so it must still be filtered by tenant. That happens in
        `_scoped()`, which is why this cannot be a bare query.

        The inbox's events (takeover, return, close: `msg_type='event'`) are left
        out unless asked for: they are not words of the customer or the business,
        and a reader that took them for a reply (the burst, the model's history)
        would act on something nobody said.
        """
        statement = (
            self._scoped()
            .where(Message.conversation_id == conversation_id)
            .order_by(Message.created_at)
        )
        if not include_events:
            statement = statement.where(Message.msg_type != EVENT)
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
        payload: dict[str, Any] | None = None,
    ) -> Message:
        """Add a message, refusing to write into another tenant's thread.

        The last three arguments belong to the real channel (Feature 002) and
        default to what the sandbox means: a text message that was never sent
        anywhere, so no provider ever named it and it has no delivery state.
        `payload` is the structure of a typed message (buttons, a list, a file,
        the option a customer tapped).
        """
        conversations = ConversationRepository(self._session, self.tenant_id)
        if not conversations.exists(conversation_id):
            raise TenantScopeError(
                f"Conversation {conversation_id!s} does not belong to tenant {self.tenant_id!s}"
            )
        message = self.add(
            conversation_id=conversation_id,
            direction=direction,
            author=author,
            content=content,
            msg_type=msg_type,
            provider_message_id=provider_message_id,
            delivery_status=delivery_status,
            payload=payload,
        )
        # The inbox's order and its unread counter (Feature 007). One UPDATE in the
        # same transaction as the message, so the two never disagree.
        values: dict[str, Any] = {"last_message_at": func.clock_timestamp()}
        if direction == "inbound":
            values["unread_count"] = Conversation.unread_count + 1
        self._session.execute(
            update(Conversation)
            .where(Conversation.id == conversation_id, Conversation.tenant_id == self.tenant_id)
            .values(**values)
            .execution_options(synchronize_session=False)
        )
        return message

    def latest_for_conversation(self, conversation_id: uuid.UUID) -> Message | None:
        """The newest message of a thread, whoever sent it (events aside)."""
        statement = (
            self._scoped()
            .where(Message.conversation_id == conversation_id, Message.msg_type != EVENT)
            .order_by(Message.created_at.desc())
            .limit(1)
        )
        return self._session.execute(statement).scalar_one_or_none()

    def latest_location(self, conversation_id: uuid.UUID, *, since: datetime) -> Message | None:
        """The newest pin the customer shared in a thread since `since`, if any."""
        statement = (
            self._scoped()
            .where(
                Message.conversation_id == conversation_id,
                Message.direction == "inbound",
                Message.msg_type == "location",
                Message.created_at >= since,
            )
            .order_by(Message.created_at.desc())
            .limit(1)
        )
        return self._session.execute(statement).scalar_one_or_none()

    def last_reply_at(self, conversation_id: uuid.UUID) -> datetime | None:
        """When the assistant (or a person of the business) last wrote in this thread."""
        statement = select(func.max(Message.created_at)).where(
            self._tenant_predicate,
            Message.conversation_id == conversation_id,
            Message.direction == "outbound",
            Message.msg_type != EVENT,
        )
        return self._session.execute(statement).scalar_one_or_none()

    def outbound_after(
        self, conversation_id: uuid.UUID, after_id: uuid.UUID | None = None
    ) -> Sequence[Message]:
        """What the business said in a thread after one message, oldest first.

        With no `after_id`, everything it said. An id that is not in this
        thread (or this tenant) reads as no anchor at all: the caller gets the
        whole outbound history rather than an error, which is the safe side for
        a poll that may outlive a reset.
        """
        anchor: Message | None = None
        if after_id is not None:
            anchor = self._session.execute(
                self._scoped().where(
                    Message.id == after_id, Message.conversation_id == conversation_id
                )
            ).scalar_one_or_none()
        statement = self._scoped().where(
            Message.conversation_id == conversation_id,
            Message.direction == "outbound",
            Message.msg_type != EVENT,
        )
        if anchor is not None:
            statement = statement.where(Message.created_at > anchor.created_at)
        return self._session.execute(statement.order_by(Message.created_at)).scalars().all()

    def count_on_channel(self, channel: str, *, direction: str, since: datetime) -> int:
        """How many messages one channel carried, one way, since a moment.

        Joined to `conversations` because the channel lives there: a message
        does not know whether it travelled over WhatsApp or was typed into the
        sandbox, and counting sandbox traffic as real traffic would tell an
        owner their number was busy on a day nobody wrote to them.
        """
        statement = (
            select(func.count())
            .select_from(Message)
            .join(Conversation, Conversation.id == Message.conversation_id)
            .where(
                Message.tenant_id == self.tenant_id,
                Conversation.channel == channel,
                Message.direction == direction,
                Message.created_at >= since,
            )
        )
        return self._session.execute(statement).scalar_one()

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

    def sources_by_message(self, message_ids: Sequence[uuid.UUID]) -> dict[uuid.UUID, list[str]]:
        """The knowledge sources each reply leaned on (Feature 006, HU-05), by message id."""
        if not message_ids:
            return {}
        found: dict[uuid.UUID, list[str]] = {}
        for trace in self.list(AgentTrace.message_id.in_(list(message_ids))):
            titles = found.setdefault(trace.message_id, [])
            for entry in trace.rag_sources or []:
                if not isinstance(entry, dict):
                    continue
                for title in entry.get("fuentes") or []:
                    if isinstance(title, str) and title not in titles:
                        titles.append(title)
        return {k: v for k, v in found.items() if v}
