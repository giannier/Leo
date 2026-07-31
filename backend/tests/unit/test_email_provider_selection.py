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
from app.providers.email.console import ConsoleEmailProvider
from app.providers.email.factory import build_email_provider
from app.providers.email.resend_provider import ResendEmailProvider


class TestFactory:
    def test_builds_the_resend_adapter_when_a_key_is_configured(self) -> None:
        provider = build_email_provider(
            Settings(resend_api_key="re_test_key", email_from="Leo <no-reply@leo.pe>")
        )
        assert isinstance(provider, ResendEmailProvider)
        assert isinstance(provider, EmailProvider)

    def test_development_falls_back_to_the_console_provider(self) -> None:
        """Email verification gates the panel, so a developer with no Resend
        key would otherwise be unable to get past registration - the token
        exists only inside the message."""
        provider = build_email_provider(Settings(resend_api_key=None, environment="development"))
        assert isinstance(provider, ConsoleEmailProvider)
        assert isinstance(provider, EmailProvider)

    @pytest.mark.parametrize("environment", ["staging", "production"])
    def test_a_deployment_without_a_key_still_raises(self, environment: str) -> None:
        """The console provider logs verification links. A deployment that
        reached one would be writing customer credentials into its log
        aggregator, so the fallback must never apply there."""
        with pytest.raises(EmailConfigurationError, match="RESEND_API_KEY"):
            build_email_provider(
                Settings(
                    resend_api_key=None,
                    environment=environment,  # type: ignore[arg-type]
                    secret_key="a-real-secret-key-of-sufficient-length",
                )
            )

    def test_the_console_provider_refuses_to_construct_when_deployed(self) -> None:
        """Belt and braces: even constructed directly, it will not run."""
        with pytest.raises(EmailConfigurationError, match="never run"):
            ConsoleEmailProvider(environment="production")


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
