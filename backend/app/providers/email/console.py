"""Development `EmailProvider` that writes the message to the application log.

Exists so the whole onboarding flow can be exercised locally with no external
service and no credential. Email verification is mandatory to enter the panel
(Clarification 005), so without this a developer with no Resend key cannot get
past registration - the verification token only ever exists inside the message.

It refuses to construct outside development, exactly like `ConsoleOtpSender`.
A deployment that forgets to configure a real provider must fail at startup
rather than write customers' verification links into a log aggregator.
"""

from __future__ import annotations

import logging
import re
import uuid

from app.providers.email.base import EmailConfigurationError, EmailMessage

logger = logging.getLogger(__name__)

_HREF = re.compile(r'href="([^"]+)"')


class ConsoleEmailProvider:
    """Logs the message, and the link, instead of sending. Development only."""

    def __init__(self, *, environment: str) -> None:
        if environment not in ("development", "test"):
            raise EmailConfigurationError(
                "ConsoleEmailProvider writes verification links to the log and must "
                f"never run in {environment!r}. Configure RESEND_API_KEY instead."
            )

    async def send(self, message: EmailMessage) -> str:
        links = _HREF.findall(message.html)
        logger.warning(
            "DEV ONLY - email not sent. to=%s subject=%r link=%s",
            ", ".join(message.to),
            message.subject,
            links[0] if links else "(no link in this message)",
        )
        return f"console-{uuid.uuid4()}"
