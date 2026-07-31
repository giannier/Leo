"""WhatsApp numbers and connection requests."""

from __future__ import annotations

from collections.abc import Sequence

from app.models.whatsapp import ConnectionRequest, WhatsAppNumber
from app.repositories.base import BaseRepository

CONNECTED_STATUS = "connected"


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
