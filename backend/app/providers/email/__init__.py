"""Transactional email port and adapters."""

from app.providers.email.base import (
    EmailAuthError,
    EmailError,
    EmailMessage,
    EmailProvider,
)
from app.providers.email.mock import MockEmailProvider
from app.providers.email.resend_provider import ResendEmailProvider

__all__ = [
    "EmailAuthError",
    "EmailError",
    "EmailMessage",
    "EmailProvider",
    "MockEmailProvider",
    "ResendEmailProvider",
]
