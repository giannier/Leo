"""In-memory `EmailProvider` for tests.

Records everything sent so a test can assert on the recipient, subject and
the link a verification email actually contains.
"""

from __future__ import annotations

import uuid

from app.providers.email.base import EmailError, EmailMessage


class MockEmailProvider:
    def __init__(self, *, fail_with: Exception | None = None) -> None:
        self.sent: list[EmailMessage] = []
        self._fail_with = fail_with

    async def send(self, message: EmailMessage) -> str:
        if self._fail_with is not None:
            raise self._fail_with
        if not message.to:
            raise EmailError("no recipients")
        self.sent.append(message)
        return f"mock-{uuid.uuid4()}"

    @property
    def last(self) -> EmailMessage:
        if not self.sent:
            raise AssertionError("no email was sent")
        return self.sent[-1]
