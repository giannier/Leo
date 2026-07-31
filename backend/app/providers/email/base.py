"""The `EmailProvider` port."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable


class EmailError(RuntimeError):
    """The message could not be handed to the provider."""


class EmailAuthError(EmailError):
    """The provider rejected the credential. Retrying will not help."""


@dataclass(frozen=True)
class EmailMessage:
    to: tuple[str, ...]
    subject: str
    html: str
    text: str | None = None
    reply_to: str | None = None


@runtime_checkable
class EmailProvider(Protocol):
    async def send(self, message: EmailMessage) -> str:
        """Send one message and return the provider's message id."""
        ...
