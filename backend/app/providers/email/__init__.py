"""Transactional email port and adapters."""

from app.providers.email.base import (
    EmailAuthError,
    EmailConfigurationError,
    EmailError,
    EmailMessage,
    EmailProvider,
)
from app.providers.email.console import ConsoleEmailProvider
from app.providers.email.factory import build_email_provider
from app.providers.email.mock import MockEmailProvider
from app.providers.email.resend_provider import ResendEmailProvider

__all__ = [
    "EmailAuthError",
    "EmailConfigurationError",
    "EmailError",
    "EmailMessage",
    "EmailProvider",
    "ConsoleEmailProvider",
    "MockEmailProvider",
    "ResendEmailProvider",
    "build_email_provider",
]
