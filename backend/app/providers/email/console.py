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

import json
import logging
import re
import uuid
from datetime import UTC, datetime
from pathlib import Path

from app.providers.email.base import EmailConfigurationError, EmailMessage

logger = logging.getLogger(__name__)

_HREF = re.compile(r'href="([^"]+)"')


class ConsoleEmailProvider:
    """Logs the message, and the link, instead of sending. Development only."""

    def __init__(self, *, environment: str, outbox_path: str | None = None) -> None:
        if environment not in ("development", "test"):
            raise EmailConfigurationError(
                "ConsoleEmailProvider writes verification links to the log and must "
                f"never run in {environment!r}. Configure RESEND_API_KEY instead."
            )
        # An append-only JSONL file of everything "sent", when a path is
        # configured. The log line is fine for a person reading a terminal;
        # the end-to-end suite needs to *read* the verification link, and it
        # cannot - the token is stored hashed, so the database will never give
        # it back and the message is the only place the plaintext exists.
        #
        # Safe by construction: this class refuses to exist outside
        # development, so there is no configuration that turns a production
        # deployment into one writing customers' links to disk.
        self._outbox = Path(outbox_path) if outbox_path else None

    async def send(self, message: EmailMessage) -> str:
        links = _HREF.findall(message.html)
        message_id = f"console-{uuid.uuid4()}"

        logger.warning(
            "DEV ONLY - email not sent. to=%s subject=%r link=%s",
            ", ".join(message.to),
            message.subject,
            links[0] if links else "(no link in this message)",
        )

        if self._outbox is not None:
            self._outbox.parent.mkdir(parents=True, exist_ok=True)
            record = {
                "id": message_id,
                "at": datetime.now(UTC).isoformat(),
                "to": list(message.to),
                "subject": message.subject,
                "links": links,
                "text": message.text,
            }
            with self._outbox.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(record, ensure_ascii=False) + "\n")

        return message_id
