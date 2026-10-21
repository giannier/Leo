"""The inbox: a person takes a conversation, writes as the business, gives it back
(Feature 007, TASK-703 and TASK-704).

* **Takeover** (`take`): an atomic `UPDATE ... WHERE takeover_user_id IS NULL`. Two
  people pressing at once: one row is updated, the other gets `AlreadyTaken` with
  the name of who has it. While it is held the turn pipeline stays quiet (gate
  `takeover`) and spends no quota (C-004); a reply the model was still writing is
  discarded before it is sent (`reply_still_wanted`, the CancelGuard).
* A conversation the assistant had **handed over** (`derivado`) leaves that queue
  when somebody takes it: the fact is cleared, the person is attending it.
* **Return** (`release`): the holder, or an owner or admin, gives it back. Nothing
  is sent; the agent answers the customer's next message and reads in the history
  what the person said (their messages are part of it, as the business's).
* **Steal** (owner or admin, C-003): takes it from whoever has it.
* **A person's message** (`send`): the person must hold the conversation; one that
  nobody holds is taken on the way. Outside Meta's 24-hour window it is refused
  (C-002): the composer is blocked, not only warned.
* **Events** of the thread (`event`): takeover, return, close, reopen and opt-out
  are stored as `msg_type='event'` items, so the thread reads in order.

Every method works inside the caller's transaction and publishes nothing: the API
publishes after it commits.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any, cast

from sqlalchemy import CursorResult, select, update
from sqlalchemy.orm import Session

from app.models.channel import Contact
from app.models.conversation import Conversation, Message
from app.models.inbox import ConversationNote, NotificationSettings
from app.models.user import User
from app.process.recompute import recompute_stage
from app.repositories.audit import AuditLogRepository
from app.repositories.channel import ContactRepository, session_window_is_open
from app.repositories.conversation import EVENT, ConversationRepository, MessageRepository
from app.services.campaigns.optins import OptinService

EVENT_TEXTS = {
    "tomada": "{nombre} tomó la conversación",
    "robada": "{nombre} tomó la conversación que tenía {anterior}",
    "devuelta": "{nombre} devolvió la conversación al asistente",
    "devuelta_por_inactividad": "La conversación volvió al asistente por inactividad",
    "cerrada": "{nombre} cerró la conversación",
    "reabierta": "{nombre} reabrió la conversación",
    "opt_out": "{nombre} marcó que el cliente no quiere recibir mensajes",
}
REASON_TAKEN = "operador:tomar"
REASON_RETURNED = "operador:devolver"


class InboxError(RuntimeError):
    """Something the person asked for cannot be done; the API maps each subclass."""


class NotFound(InboxError):
    pass


class AlreadyTaken(InboxError):
    def __init__(self, by: str | None) -> None:
        super().__init__(f"taken by {by}")
        self.by = by


class NotHolder(InboxError):
    pass


class OutsideWindow(InboxError):
    pass


class Closed(InboxError):
    pass


class AnotherOpen(InboxError):
    pass


def settings_for(session: Session, tenant_id: uuid.UUID) -> NotificationSettings:
    """The business's settings, or the defaults (not written until it saves them)."""
    row = session.get(NotificationSettings, tenant_id)
    if row is None:
        row = NotificationSettings(
            tenant_id=tenant_id, unattended_queue_minutes=10, auto_return_minutes=30
        )
    return row


class Inbox:
    def __init__(
        self,
        session: Session,
        tenant_id: uuid.UUID,
        *,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._session = session
        self._tenant_id = tenant_id
        self._clock = clock
        self._conversations = ConversationRepository(session, tenant_id)
        self._messages = MessageRepository(session, tenant_id)

    # ------------------------------------------------------------------ read

    def conversation(self, conversation_id: uuid.UUID) -> Conversation:
        row = self._conversations.get(conversation_id)
        if row is None:
            raise NotFound(str(conversation_id))
        return row

    def holder_name(self, conversation: Conversation) -> str | None:
        if conversation.takeover_user_id is None:
            return None
        user = self._session.get(User, conversation.takeover_user_id)
        return user.full_name if user is not None else None

    def contact(self, conversation: Conversation) -> Contact | None:
        if conversation.contact_id is None:
            return None
        return ContactRepository(self._session, self._tenant_id).get(conversation.contact_id)

    def window_open(self, conversation: Conversation) -> bool:
        """Whether a person may write: the simulator always, WhatsApp within 24 h."""
        if conversation.channel == "sandbox":
            return True
        contact = self.contact(conversation)
        return contact is not None and session_window_is_open(contact, now=self._clock())

    def mark_read(self, conversation: Conversation) -> None:
        conversation.unread_count = 0
        self._session.flush()

    # -------------------------------------------------------------- takeover

    def take(self, conversation_id: uuid.UUID, user: User) -> Conversation:
        conversation = self.conversation(conversation_id)
        if conversation.status == "closed":
            raise Closed(str(conversation_id))
        if conversation.takeover_user_id == user.id:
            return conversation
        now = self._clock()
        result = self._session.execute(
            update(Conversation)
            .where(
                Conversation.id == conversation_id,
                Conversation.tenant_id == self._tenant_id,
                Conversation.takeover_user_id.is_(None),
            )
            .values(takeover_user_id=user.id, takeover_started_at=now, takeover_activity_at=now)
            .execution_options(synchronize_session=False)
        )
        taken = cast(CursorResult[Any], result).rowcount
        self._session.refresh(conversation)
        if not taken:
            raise AlreadyTaken(self.holder_name(conversation))
        self._attended(conversation, now)
        self.event(conversation, "tomada", user)
        return conversation

    def steal(
        self, conversation_id: uuid.UUID, user: User
    ) -> tuple[Conversation, uuid.UUID | None]:
        """An owner or admin takes it from whoever has it (C-003). Returns the previous holder."""
        conversation = self.conversation(conversation_id)
        if conversation.status == "closed":
            raise Closed(str(conversation_id))
        previous = conversation.takeover_user_id
        if previous is None:
            return self.take(conversation_id, user), None
        if previous == user.id:
            return conversation, None
        before = self.holder_name(conversation)
        now = self._clock()
        conversation.takeover_user_id = user.id
        conversation.takeover_started_at = now
        conversation.takeover_activity_at = now
        self._session.flush()
        self.event(conversation, "robada", user, anterior=before)
        return conversation, previous

    def release(
        self, conversation_id: uuid.UUID, user: User | None, *, may_force: bool = False
    ) -> Conversation:
        """Give it back to the assistant. `user=None`: the auto-return job."""
        conversation = self.conversation(conversation_id)
        if conversation.takeover_user_id is None:
            return conversation
        if user is not None and conversation.takeover_user_id != user.id and not may_force:
            raise NotHolder(str(conversation_id))
        conversation.takeover_user_id = None
        conversation.takeover_started_at = None
        conversation.takeover_activity_at = None
        self._session.flush()
        if user is None:
            self.event(conversation, "devuelta_por_inactividad", None)
        else:
            self.event(conversation, "devuelta", user)
        recompute_stage(
            self._session,
            self._tenant_id,
            conversation,
            reason=REASON_RETURNED,
            now=self._clock(),
            force_event=True,
        )
        return conversation

    def _attended(self, conversation: Conversation, now: datetime) -> None:
        """A handed-over conversation leaves the queue once somebody has it."""
        recompute_stage(
            self._session,
            self._tenant_id,
            conversation,
            reason=REASON_TAKEN,
            now=now,
            force_event=True,
            facts_patch={"derivado": None},
        )

    # ------------------------------------------------------------- messages

    def send(self, conversation_id: uuid.UUID, user: User, text: str) -> Message:
        """A person's message, as the business. Queued for the sender on WhatsApp."""
        conversation = self.conversation(conversation_id)
        if conversation.status == "closed":
            raise Closed(str(conversation_id))
        if not self.window_open(conversation):
            raise OutsideWindow(str(conversation_id))
        if conversation.takeover_user_id is None:
            self.take(conversation_id, user)
        elif conversation.takeover_user_id != user.id:
            raise AlreadyTaken(self.holder_name(conversation))
        conversation.takeover_activity_at = self._clock()
        message = self._messages.append(
            conversation_id=conversation.id,
            direction="outbound",
            author="human_operator",
            content=text,
            delivery_status={"whatsapp": "queued", "web": "sent"}.get(conversation.channel),
            payload={"operador": user.full_name, "operador_id": str(user.id)},
        )
        conversation.unread_count = 0
        self._session.flush()
        return message

    def note(self, conversation_id: uuid.UUID, user: User, text: str) -> ConversationNote:
        conversation = self.conversation(conversation_id)
        note = ConversationNote(
            tenant_id=self._tenant_id,
            conversation_id=conversation.id,
            author_id=user.id,
            content=text,
        )
        self._session.add(note)
        self._session.flush()
        return note

    def notes(self, conversation_id: uuid.UUID) -> list[ConversationNote]:
        self.conversation(conversation_id)
        return list(
            self._session.execute(
                select(ConversationNote)
                .where(
                    ConversationNote.tenant_id == self._tenant_id,
                    ConversationNote.conversation_id == conversation_id,
                )
                .order_by(ConversationNote.created_at)
            ).scalars()
        )

    # --------------------------------------------------------------- status

    def close(self, conversation_id: uuid.UUID, user: User) -> Conversation:
        conversation = self.conversation(conversation_id)
        if conversation.status == "closed":
            return conversation
        if conversation.takeover_user_id is not None:
            conversation.takeover_user_id = None
            conversation.takeover_started_at = None
            conversation.takeover_activity_at = None
        conversation.status = "closed"
        conversation.closed_at = self._clock()
        self._session.flush()
        self.event(conversation, "cerrada", user)
        return conversation

    def reopen(self, conversation_id: uuid.UUID, user: User) -> Conversation:
        """Only if the customer has no other open WhatsApp thread (one per contact)."""
        conversation = self.conversation(conversation_id)
        if conversation.status == "open":
            return conversation
        if conversation.channel == "whatsapp" and conversation.contact_id is not None:
            other = self._conversations.open_for_contact(conversation.contact_id)
            if other is not None and other.id != conversation.id:
                raise AnotherOpen(str(other.id))
        conversation.status = "open"
        conversation.closed_at = None
        self._session.flush()
        self.event(conversation, "reabierta", user)
        return conversation

    def opt_out(self, conversation_id: uuid.UUID, user: User) -> Contact:
        """The customer asked not to be written to; marked by a person, audited (RF-007)."""
        conversation = self.conversation(conversation_id)
        contact = self.contact(conversation)
        if contact is None:
            raise NotFound("contact")
        if contact.opted_out_at is None:
            contact.opted_out_at = self._clock()
            self._session.flush()
            # No more promotions either (Feature 011, C-103).
            OptinService(self._session, self._tenant_id).revoke(contact, at=contact.opted_out_at)
            AuditLogRepository(self._session, self._tenant_id).record(
                actor_id=user.id,
                action="contact.opt_out",
                detail={"contact_id": str(contact.id), "conversation_id": str(conversation.id)},
            )
            self.event(conversation, "opt_out", user)
        return contact

    # ---------------------------------------------------------------- events

    def event(
        self, conversation: Conversation, kind: str, user: User | None, **extra: Any
    ) -> Message:
        name = user.full_name if user is not None else "El sistema"
        text = EVENT_TEXTS.get(kind, kind).format(
            nombre=name, **{k: v or "otra persona" for k, v in extra.items()}
        )
        payload: dict[str, Any] = {"evento": kind, **{k: v for k, v in extra.items() if v}}
        if user is not None:
            payload["usuario_id"] = str(user.id)
        return self._messages.append(
            conversation_id=conversation.id,
            direction="outbound",
            author="system",
            content=text,
            msg_type=EVENT,
            payload=payload,
        )


def reply_still_wanted(session: Session, conversation_id: uuid.UUID) -> bool:
    """The CancelGuard: is the agent still allowed to send in this conversation?

    Read fresh from the database right before a reply is handed to the provider: a
    person who took the conversation while the model was writing wins, and the
    reply is not sent.
    """
    taken = session.execute(
        select(Conversation.takeover_user_id)
        .where(Conversation.id == conversation_id)
        .execution_options(populate_existing=True)
    ).scalar_one_or_none()
    return taken is None


def discard_replies(session: Session, messages: list[Message]) -> None:
    """Mark replies that will never be sent because a person took the conversation."""
    for message in messages:
        message.delivery_status = "failed"
        message.payload = {**(message.payload or {}), "descartado": "takeover"}
    session.flush()
