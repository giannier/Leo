"""Builds the configured `EmailProvider`.

One provider today (Resend), but callers go through the factory anyway so
that swapping in SMTP later is a change in this file and nowhere else - the
same rule the LLM adapters follow.
"""

from __future__ import annotations

from app.core.config import Settings, get_settings
from app.providers.email.base import EmailConfigurationError, EmailProvider
from app.providers.email.resend_provider import ResendEmailProvider


def build_email_provider(settings: Settings | None = None) -> EmailProvider:
    """Return the configured provider, or raise if there is no credential.

    Raising rather than returning a silent no-op is intentional: a
    deployment that cannot send email should say so. The registration
    endpoint decides what to do about it and reports the outcome to the
    client, which is not the same as swallowing it here.
    """
    settings = settings or get_settings()
    if not settings.resend_api_key:
        raise EmailConfigurationError(
            "RESEND_API_KEY is not set, so no transactional email can be sent. "
            "Set it in .env (or the deployment environment) to enable email "
            "verification."
        )
    return ResendEmailProvider(api_key=settings.resend_api_key, sender=settings.email_from)
