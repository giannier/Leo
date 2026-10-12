"""What the sending tools need from the turn's outbound side (Feature 004, TASK-420).

`enviar_recurso` and `mostrar_opciones` do not talk to WhatsApp: they hand a
finished message to an adapter and learn whether it went. Who sits behind the
adapter is the turn pipeline's business (TASK-421): in production it resolves the
file with `MediaDelivery`, calls the provider and records the outbound message;
in the simulator it records the message and sends nothing (C-19). Keeping the
tools on this narrow interface is what lets both share them.

Every method raises `OutboundFailed` when the message did not go, and nothing
else: the tools only need to know that it failed, not why.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Protocol

from app.models.media import MediaAsset
from app.providers.whatsapp.base import Button, ListSection


class OutboundFailed(RuntimeError):
    """The message could not be handed to the customer."""


class TurnOutbound(Protocol):
    async def send_media(self, asset: MediaAsset) -> None:
        """Send a library file, with the caption the tenant gave it."""
        ...

    async def send_buttons(self, body: str, buttons: Sequence[Button]) -> None:
        """Send 1 to 3 reply buttons under `body`."""
        ...

    async def send_list(self, body: str, button_text: str, sections: Sequence[ListSection]) -> None:
        """Send a list of up to 10 rows under `body`."""
        ...
