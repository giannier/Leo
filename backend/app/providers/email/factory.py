"""Builds the configured `EmailProvider`.

One provider today (Resend), but callers go through the factory anyway so
that swapping in SMTP later is a change in this file and nowhere else - the
same rule the LLM adapters follow.
"""

from __future__ import annotations

from app.core.config import Settings, get_settings
from app.providers.email.base import EmailConfigurationError, EmailProvider
from app.providers.email.console import ConsoleEmailProvider
from app.providers.email.resend_provider import ResendEmailProvider


def build_email_provider(settings: Settings | None = None) -> EmailProvider:
    """Return the configured provider, or raise if there is no credential.

    Raising rather than returning a silent no-op is intentional: a
    deployment that cannot send email should say so. The registration
    endpoint decides what to do about it and reports the outcome to the
    client, which is not the same as swallowing it here.
    """
    settings = settings or get_settings()

    # An explicit choice always wins over inference.
    if settings.email_provider == "console":
        return ConsoleEmailProvider(
            environment=settings.environment, outbox_path=settings.email_outbox_path
        )
    if settings.email_provider == "resend":
        if not settings.resend_api_key:
            raise EmailConfigurationError("EMAIL_PROVIDER=resend but RESEND_API_KEY is not set.")
        return ResendEmailProvider(
            api_key=settings.resend_api_key,
            sender=settings.email_from,
            reply_to=settings.email_reply_to,
        )

    if settings.resend_api_key:
        return ResendEmailProvider(
            api_key=settings.resend_api_key,
            sender=settings.email_from,
            reply_to=settings.email_reply_to,
        )

    # No credential. In development that is the normal case - the console
    # provider logs the verification link so the flow stays walkable with no
    # external service. Anywhere else it is a misconfiguration, and
    # `ConsoleEmailProvider` refuses to construct, so this still raises.
    if not settings.is_deployed:
        return ConsoleEmailProvider(
            environment=settings.environment, outbox_path=settings.email_outbox_path
        )

    raise EmailConfigurationError(
        "RESEND_API_KEY is not set, so no transactional email can be sent. "
        "Set it in the deployment environment to enable email verification."
    )
