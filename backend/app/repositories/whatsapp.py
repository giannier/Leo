"""WhatsApp numbers and connection requests."""

from __future__ import annotations

from collections.abc import Sequence

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.whatsapp import ConnectionRequest, WhatsAppNumber
from app.repositories.base import BaseRepository

CONNECTED_STATUS = "connected"


def number_by_phone(session: Session, phone_e164: str) -> WhatsAppNumber | None:
    """Which business owns this number, platform-wide.

    The one lookup here that cannot be tenant-scoped, and the reason is not a
    shortcut: this *is* how the tenant is discovered. A webhook arrives
    addressed to a phone number and nothing else, so the tenant is the answer
    being computed rather than an input that could be supplied.

    Safe by construction rather than by care: `phone_e164` is globally unique
    on this table (Clarification 007), so there is exactly one possible
    answer and no filter could narrow it further. Everything downstream is
    built on the tenant this returns and is scoped normally.
    """
    statement = select(WhatsAppNumber).where(WhatsAppNumber.phone_e164 == phone_e164)
    return session.execute(statement).scalar_one_or_none()


def number_by_webhook_token(session: Session, token: str) -> WhatsAppNumber | None:
    """The number a webhook URL belongs to.

    Unscoped for the same reason as `number_by_phone`: the token is how the
    tenant is discovered. It is 256 random bits and unique, so there is one
    possible answer and it cannot be guessed.
    """
    statement = select(WhatsAppNumber).where(WhatsAppNumber.webhook_token == token)
    return session.execute(statement).scalar_one_or_none()


class WhatsAppNumberRepository(BaseRepository[WhatsAppNumber]):
    """Numbers bound to one tenant."""

    model = WhatsAppNumber

    def list_connected(self) -> Sequence[WhatsAppNumber]:
        return self.list(WhatsAppNumber.status == CONNECTED_STATUS)

    def has_connected(self) -> bool:
        """Whether the agent can reach real customers.

        The line between "you can try this privately" and "this is live", so
        the activation guard turns on it.
        """
        return self.count(WhatsAppNumber.status == CONNECTED_STATUS) > 0


class ConnectionRequestRepository(BaseRepository[ConnectionRequest]):
    """Requests for an admin to connect a number on the tenant's behalf."""

    model = ConnectionRequest

    def list_open(self) -> Sequence[ConnectionRequest]:
        return self.list(ConnectionRequest.status.in_(("requested", "in_progress")))
