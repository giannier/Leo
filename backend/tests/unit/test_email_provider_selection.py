"""How the email provider is chosen, and what happens when there is none.

`build_email_provider` is strict - no credential, no provider. The FastAPI
dependency deliberately softens that into `None` so registration still works
on a deployment that cannot send mail yet. Both halves are pinned here
because the softening is the part that could turn into a silent failure if
someone removed the log line and the `verification_email_sent` flag.
"""

from __future__ import annotations

import pytest

from app.api.deps import get_email_provider
from app.core.config import Settings
from app.providers.email.base import EmailConfigurationError, EmailProvider
from app.providers.email.factory import build_email_provider
from app.providers.email.resend_provider import ResendEmailProvider


class TestFactory:
    def test_builds_the_resend_adapter_when_a_key_is_configured(self) -> None:
        provider = build_email_provider(
            Settings(resend_api_key="re_test_key", email_from="Leo <no-reply@leo.pe>")
        )
        assert isinstance(provider, ResendEmailProvider)
        assert isinstance(provider, EmailProvider)

    def test_raises_a_named_error_when_no_key_is_configured(self) -> None:
        with pytest.raises(EmailConfigurationError, match="RESEND_API_KEY"):
            build_email_provider(Settings(resend_api_key=None))


class TestDependency:
    def test_returns_none_and_warns_when_email_is_unconfigured(
        self, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
    ) -> None:
        monkeypatch.setattr(
            "app.api.deps.build_email_provider",
            lambda: (_ for _ in ()).throw(EmailConfigurationError("no key")),
        )

        with caplog.at_level("WARNING"):
            assert get_email_provider() is None

        assert "email provider unavailable" in caplog.text

    def test_passes_the_built_provider_through(self, monkeypatch: pytest.MonkeyPatch) -> None:
        built = ResendEmailProvider(api_key="re_test_key", sender="Leo <no-reply@leo.pe>")
        monkeypatch.setattr("app.api.deps.build_email_provider", lambda: built)

        assert get_email_provider() is built
