"""The inbox's scheduled jobs are thin: a session, the service, the emails (Feature 007)."""

from __future__ import annotations

from contextlib import contextmanager
from typing import Any

import pytest

from app.workers import inbox


@contextmanager
def _session() -> Any:
    yield object()


class _Mail:
    def __init__(self, fail_first: bool = False) -> None:
        self.sent: list[str] = []
        self.fail_first = fail_first

    async def send(self, message: str) -> None:
        if self.fail_first and not self.sent and message == "a":
            self.fail_first = False
            raise RuntimeError("smtp down")
        self.sent.append(message)


async def test_auto_return_runs_the_service(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(inbox, "return_idle_takeovers", lambda session: 3)

    assert await inbox.auto_return_takeovers({"session_factory": _session}) == 3


async def test_one_failed_email_does_not_stop_the_others(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(inbox, "notify_unattended", lambda session: ["a", "b"])
    mail = _Mail(fail_first=True)

    sent = await inbox.email_unattended_handovers({"session_factory": _session, "email": mail})

    assert sent == 2 and mail.sent == ["b"]


async def test_without_an_email_provider_the_alert_still_counts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(inbox, "notify_unattended", lambda session: ["a"])

    assert await inbox.email_unattended_handovers({"session_factory": _session}) == 1
