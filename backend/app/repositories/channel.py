"""Contacts, raw webhook deliveries and delivery history.

Two of these three are deliberately *not* `BaseRepository` subclasses, and the
reason is worth stating because the Constitution's rule - every business query
filters by `tenant_id` at the repository layer - looks violated otherwise.

`webhook_events` has no tenant. Which tenant a delivery belongs to is a
conclusion drawn from its payload, and drawing it is the very step that can
fail; a malformed event that cannot be attributed to anybody must still be
stored or the parser bug that caused it destroys a customer's message. It is
platform infrastructure, like `audit_logs`, not tenant data.

`message_status_events` has no tenant column either, but it certainly is
tenant data: it hangs off a message. So its repository takes a `tenant_id`
anyway and reaches the rows through a join on `messages`. The filter is still
structural - there is no method here that can be called without one.
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import Sequence
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.channel import (
    MAX_WEBHOOK_ATTEMPTS,
    SESSION_WINDOW_HOURS,
    Contact,
    MessageStatusEvent,
    WebhookEvent,
)
from app.models.conversation import Message
from app.repositories.base import BaseRepository, TenantScopeError

logger = logging.getLogger(__name__)


class ContactRepository(BaseRepository[Contact]):
    """The people who have written to one tenant's number."""

    model = Contact

    def by_phone(self, phone_e164: str) -> Contact | None:
        statement = self._scoped().where(Contact.phone_e164 == phone_e164)
        return self._session.execute(statement).scalar_one_or_none()

    def by_external_user_id(self, external_user_id: str) -> Contact | None:
        """The contact Meta identifies with this BSUID."""
        statement = self._scoped().where(Contact.external_user_id == external_user_id)
        return self._session.execute(statement).scalar_one_or_none()

    def upsert(
        self,
        phone_e164: str | None = None,
        *,
        external_user_id: str | None = None,
        profile_name: str | None = None,
    ) -> Contact:
        """Find or create the contact by BSUID or phone, whichever arrived.

        A message carries a phone number, a BSUID, or both. The BSUID is the
        stable identity (a person can change number), so it is looked up first.
        When one identifier is missing from the stored contact and the other
        arrives, it is learned: a contact that started with a phone gains its
        BSUID the first time Meta sends it, and the other way round. A stored
        identifier is never overwritten.

        If the phone and the BSUID each match a *different* contact, they are
        two rows of the same person that this method does not merge (merging
        would move conversations and orders). The BSUID contact wins and the
        phone is not attached to it, which would violate the phone's unique
        index. Each row is marked with the other's id (`posible_duplicado_de`)
        so the operator sees it (Milestone B, decision 12).

        The profile name is only overwritten when the provider actually sent
        one. WhatsApp omits the field for people who have not set a name, and
        a blank in one webhook must not erase a name learned from an earlier
        one.
        """
        if phone_e164 is None and external_user_id is None:
            raise ValueError("a contact needs a phone number or a BSUID")

        by_user = self.by_external_user_id(external_user_id) if external_user_id else None
        by_phone = self.by_phone(phone_e164) if phone_e164 else None
        contact = by_user or by_phone
        if contact is None:
            return self.add(
                phone_e164=phone_e164,
                external_user_id=external_user_id,
                profile_name=profile_name,
            )

        changed = False
        if by_user is not None and by_phone is not None and by_user.id != by_phone.id:
            changed = _mark_duplicates(by_user, by_phone) or changed
        if external_user_id and contact.external_user_id is None:
            contact.external_user_id = external_user_id
            changed = True
        if phone_e164 and contact.phone_e164 is None and by_phone is None:
            contact.phone_e164 = phone_e164
            changed = True
        if profile_name:
            contact.profile_name = profile_name
            changed = True
        if changed:
            self._session.flush()
        return contact

    def merge_custom_data(self, contact: Contact, values: dict[str, Any]) -> Contact:
        """Add or replace some of the customer's data, keeping the rest.

        A new dict is assigned rather than the old one mutated, so SQLAlchemy
        sees the column change. Validation is the caller's job (TASK-427).
        """
        self._require_own(contact)
        contact.custom_data = {**contact.custom_data, **values}
        self._session.flush()
        return contact

    def record_inbound(self, contact: Contact, *, at: datetime) -> Contact:
        """Stamp the message that reopens the 24-hour session window.

        Never moved backwards. Providers redeliver, and out-of-order arrival
        is normal, so an older event must not shorten a window that a newer
        message already extended.
        """
        self._require_own(contact)
        if contact.last_inbound_at is None or contact.last_inbound_at < at:
            contact.last_inbound_at = at
            self._session.flush()
        return contact

    def set_opted_out(self, contact: Contact, *, at: datetime | None) -> Contact:
        """Record - or clear - a request not to be contacted.

        A timestamp rather than a flag, because "when did they opt out" is a
        question that gets asked and a boolean cannot answer it. Clearing it
        is what a new inbound message from that person means: they started the
        conversation again.
        """
        self._require_own(contact)
        contact.opted_out_at = at
        self._session.flush()
        return contact

    def _require_own(self, contact: Contact) -> None:
        """Refuse to mutate a contact belonging to somebody else.

        These methods take an ORM object rather than an id, so the scoped
        read that would normally enforce the boundary has already happened -
        or has been skipped. This is the check that makes skipping it fail.
        """
        if contact.tenant_id != self.tenant_id:
            raise TenantScopeError(
                f"Contact {contact.id!s} belongs to tenant {contact.tenant_id!s}, "
                f"not to {self.tenant_id!s}"
            )


def session_window_is_open(contact: Contact, *, now: datetime) -> bool:
    """Whether Meta still allows a free-form reply to this person.

    Computed, never stored. A `window_open` column would need a job to expire
    it and would be wrong for exactly as long as that job was late - and being
    wrong here means either a silent violation of Meta's policy or a customer
    who never gets an answer.
    """
    if contact.last_inbound_at is None:
        return False
    return now - contact.last_inbound_at < timedelta(hours=SESSION_WINDOW_HOURS)


class WebhookEventRepository:
    """Raw provider deliveries. Platform-wide, not tenant-scoped."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def record(
        self, *, provider: str, provider_event_id: str, payload: dict[str, Any]
    ) -> WebhookEvent | None:
        """Store a delivery, or return None if it has already been stored.

        The uniqueness of `provider_event_id` is what makes this idempotent,
        and the check below is only the fast path. The transaction that
        actually loses a race gets an `IntegrityError` from the database,
        which the caller treats the same way: the event is already known and
        this delivery is a duplicate.
        """
        existing = self.by_provider_event_id(provider_event_id)
        if existing is not None:
            return None
        event = WebhookEvent(
            provider=provider,
            provider_event_id=provider_event_id,
            payload=payload,
        )
        self._session.add(event)
        self._session.flush()
        return event

    def by_provider_event_id(self, provider_event_id: str) -> WebhookEvent | None:
        statement = select(WebhookEvent).where(WebhookEvent.provider_event_id == provider_event_id)
        return self._session.execute(statement).scalar_one_or_none()

    def get(self, event_id: uuid.UUID) -> WebhookEvent | None:
        return self._session.get(WebhookEvent, event_id)

    def mark_processed(self, event: WebhookEvent, *, at: datetime) -> WebhookEvent:
        event.status = "processed"
        event.processed_at = at
        event.error_msg = None
        self._session.flush()
        return event

    def mark_failed(self, event: WebhookEvent, *, error: str) -> WebhookEvent:
        """Count the attempt, and give up loudly once there have been enough.

        `dead_letter` is not a discard. It is a row an admin can see and
        replay, which is the Constitution's rule against silent failure: the
        message still exists, it just stopped retrying on its own.
        """
        event.attempts += 1
        # Truncated: a provider error can carry a whole HTML page, and the
        # useful part is always at the front.
        event.error_msg = error[:1000]
        event.status = "dead_letter" if event.attempts >= MAX_WEBHOOK_ATTEMPTS else "failed"
        self._session.flush()
        return event

    def list_by_status(
        self, status: str, *, limit: int = 50, offset: int = 0
    ) -> Sequence[WebhookEvent]:
        statement = (
            select(WebhookEvent)
            .where(WebhookEvent.status == status)
            .order_by(WebhookEvent.created_at.desc())
            .offset(offset)
            .limit(limit)
        )
        return self._session.execute(statement).scalars().all()

    def count_by_status(self, status: str) -> int:
        statement = (
            select(func.count()).select_from(WebhookEvent).where(WebhookEvent.status == status)
        )
        return self._session.execute(statement).scalar_one()


class MessageStatusEventRepository:
    """Delivery history, reached through the message that owns it."""

    def __init__(self, session: Session, tenant_id: uuid.UUID) -> None:
        self._session = session
        self._tenant_id = tenant_id

    @property
    def tenant_id(self) -> uuid.UUID:
        return self._tenant_id

    def append(
        self, message_id: uuid.UUID, *, status: str, provider_ts: datetime | None = None
    ) -> MessageStatusEvent:
        """Record one step, refusing to write against another tenant's message."""
        owned = self._session.execute(
            select(Message.id).where(Message.id == message_id, Message.tenant_id == self._tenant_id)
        ).scalar_one_or_none()
        if owned is None:
            raise TenantScopeError(
                f"Message {message_id!s} does not belong to tenant {self._tenant_id!s}"
            )
        event = MessageStatusEvent(message_id=message_id, status=status, provider_ts=provider_ts)
        self._session.add(event)
        self._session.flush()
        return event

    def history(self, message_id: uuid.UUID) -> Sequence[MessageStatusEvent]:
        """Every step for one message, in the provider's order.

        Ordered by `provider_ts` and not by `created_at`: `delivered` and
        `read` webhooks routinely arrive out of order, and a history in the
        order we happened to receive them is worthless.
        """
        statement = (
            select(MessageStatusEvent)
            .join(Message, Message.id == MessageStatusEvent.message_id)
            .where(
                MessageStatusEvent.message_id == message_id,
                Message.tenant_id == self._tenant_id,
            )
            .order_by(MessageStatusEvent.provider_ts, MessageStatusEvent.created_at)
        )
        return self._session.execute(statement).scalars().all()


def _mark_duplicates(one: Contact, other: Contact) -> bool:
    """Point two rows of one person at each other; True if anything changed.

    A mark already set is kept: it names the first duplicate found, which is
    enough for the operator to look.
    """
    changed = False
    if one.posible_duplicado_de is None:
        one.posible_duplicado_de = other.id
        changed = True
    if other.posible_duplicado_de is None:
        other.posible_duplicado_de = one.id
        changed = True
    if changed:
        logger.warning(
            "a BSUID and a phone matched two contacts; marked, not merged",
            extra={"tenant_id": str(one.tenant_id)},
        )
    return changed
