"""Which provider each caller gets, and what it is configured with.

Two decisions live here and both cost money if they go wrong. The platform
profile decides whose account pays for embeddings and default chat. The tenant
override decides whether a customer's own paid key is used - and, more
importantly, what happens when that key is unusable: falling back to the
platform silently would move a customer's spend onto the platform's card while
they never learn their key is broken.

No network. Nothing here sends a request; it builds providers and inspects how
they were configured.
"""

from __future__ import annotations

import uuid

import pytest
from cryptography.fernet import Fernet

from app.core.config import Settings
from app.models.knowledge import EMBEDDING_DIMENSIONS
from app.models.llm_settings import TenantLLMSettings
from app.providers.llm.base import LLMConfigurationError
from app.providers.llm.factory import (
    platform_profile,
    platform_provider,
    provider_for_tenant,
)
from app.providers.llm.offline import OfflineLLMProvider
from app.providers.llm.openai_compatible import OpenAICompatibleProvider

# Long enough to clear the plausibility floor in `_require_key`.
NVIDIA_KEY = "nvapi-" + "n" * 60
OPENAI_KEY = "sk-proj-" + "o" * 60
TENANT_KEY = "sk-tenant-" + "t" * 60


class _Crypto:
    """Stands in for `app.core.crypto`'s settings, with a per-run key.

    Never the deployment's: a test that depended on `.env` would pass or fail
    according to a file nobody committed.
    """

    def __init__(self, key: str) -> None:
        self.credentials_encryption_key = key


@pytest.fixture
def cipher(monkeypatch: pytest.MonkeyPatch) -> Fernet:
    key = Fernet.generate_key().decode()
    monkeypatch.setattr("app.core.crypto.get_settings", lambda: _Crypto(key))
    return Fernet(key.encode())


def _settings(**overrides: object) -> Settings:
    base: dict[str, object] = {
        "environment": "test",
        # Pinned, never inherited. A constructor argument outranks the `.env`
        # file, and without this line every assertion below would depend on
        # whichever profile the developer happens to have configured - the
        # suite would pass on one machine and fail on the next.
        "llm_profile": "nvidia",
        "nvidia_api_key": NVIDIA_KEY,
        "openai_api_key": OPENAI_KEY,
    }
    base.update(overrides)
    return Settings(**base)  # type: ignore[arg-type]


def _tenant(**overrides: object) -> TenantLLMSettings:
    row = TenantLLMSettings(tenant_id=uuid.uuid4(), mode="platform")
    for name, value in overrides.items():
        setattr(row, name, value)
    return row


class TestThePlatformProfile:
    def test_the_free_tier_is_what_an_unconfigured_deployment_gets(self) -> None:
        """Nobody has to opt into not spending money.

        Read off the field default rather than off a constructed `Settings`,
        which would pick the value up from whatever `.env` is on the machine.
        """
        assert Settings.model_fields["llm_profile"].default == "nvidia"

    def test_the_free_profile_carries_nvidias_quirks(self) -> None:
        profile = platform_profile(_settings())

        assert profile.name == "nvidia"
        assert profile.send_embedding_input_type is True
        assert profile.max_tokens_field == "max_tokens"

    def test_the_paid_profile_carries_both_vendor_quirks(self) -> None:
        """OpenAI rejects `input_type` and rejects `max_tokens`. Getting
        either wrong is a 400 from an endpoint that bills per call."""
        profile = platform_profile(_settings(llm_profile="openai"))

        assert profile.name == "openai"
        assert profile.send_embedding_input_type is False
        assert profile.max_tokens_field == "max_completion_tokens"

    def test_the_paid_profile_pins_the_vector_width(self) -> None:
        """`knowledge_chunks.embedding` is one fixed width. Without this the
        paid profile would write vectors the column cannot hold, and switching
        profile would mean a migration and a full re-embedding."""
        assert platform_profile(_settings(llm_profile="openai")).embedding_dimensions == (
            EMBEDDING_DIMENSIONS
        )

    def test_a_missing_key_names_the_variable_that_is_missing(self) -> None:
        with pytest.raises(LLMConfigurationError, match="NVIDIA_API_KEY"):
            platform_profile(_settings(nvidia_api_key=None))

    def test_a_truncated_paste_is_refused_where_it_is_read(self) -> None:
        """ "Set" is the wrong question. A ten-character value passes every
        guard and fails at the embedding call, a long way from the line in
        `.env` that caused it."""
        with pytest.raises(LLMConfigurationError, match="too short"):
            platform_profile(_settings(llm_profile="openai", openai_api_key="sk-changeme"))


class TestThePlatformProvider:
    def test_the_offline_profile_talks_to_nobody(self) -> None:
        provider = platform_provider(_settings(llm_profile="offline"))

        assert isinstance(provider, OfflineLLMProvider)

    def test_any_other_profile_is_the_http_adapter(self) -> None:
        provider = platform_provider(_settings())

        assert isinstance(provider, OpenAICompatibleProvider)
        assert provider.chat_model == _settings().nvidia_chat_model


class TestTheTenantOverride:
    def test_no_row_means_the_platform_pays(self) -> None:
        provider = provider_for_tenant(None, settings=_settings())

        assert isinstance(provider, OpenAICompatibleProvider)
        assert provider.chat_model == _settings().nvidia_chat_model

    def test_the_platform_mode_means_the_platform_pays(self) -> None:
        provider = provider_for_tenant(_tenant(mode="platform"), settings=_settings())

        assert provider.chat_model == _settings().nvidia_chat_model

    def test_a_configured_tenant_gets_their_own_model(self, cipher: Fernet) -> None:
        row = _tenant(
            mode="byo_openai",
            chat_model="gpt-5.4",
            api_key_enc=cipher.encrypt(TENANT_KEY.encode()),
        )

        provider = provider_for_tenant(row, settings=_settings())

        assert isinstance(provider, OpenAICompatibleProvider)
        assert provider.chat_model == "gpt-5.4"

    def test_byo_openai_does_not_let_the_tenant_choose_a_host(self, cipher: Fernet) -> None:
        """The mode names the vendor. A base URL from the row here would let
        a tenant point their traffic - and their prompts - anywhere."""
        row = _tenant(
            mode="byo_openai",
            chat_model="gpt-5.4",
            base_url="https://exfiltrate.invalid/v1",
            api_key_enc=cipher.encrypt(TENANT_KEY.encode()),
        )

        provider = provider_for_tenant(row, settings=_settings())

        assert provider._base_url == _settings().openai_base_url.rstrip("/")  # noqa: SLF001

    def test_a_compatible_endpoint_uses_the_configured_host(self, cipher: Fernet) -> None:
        """Azure OpenAI, a gateway, a proxy - the whole point of the mode."""
        row = _tenant(
            mode="byo_openai_compatible",
            chat_model="mi-modelo",
            base_url="https://gateway.example.com/v1",
            api_key_enc=cipher.encrypt(TENANT_KEY.encode()),
        )

        provider = provider_for_tenant(row, settings=_settings())

        assert provider._base_url == "https://gateway.example.com/v1"  # noqa: SLF001

    def test_a_compatible_endpoint_without_a_host_is_refused(self, cipher: Fernet) -> None:
        row = _tenant(
            mode="byo_openai_compatible",
            chat_model="mi-modelo",
            api_key_enc=cipher.encrypt(TENANT_KEY.encode()),
        )

        with pytest.raises(LLMConfigurationError, match="base_url"):
            provider_for_tenant(row, settings=_settings())


class TestABrokenTenantCredentialNeverFallsBackQuietly:
    """Falling back to the platform would be the platform absorbing a cost the
    customer chose to take on, and they would never find out."""

    def test_a_mode_with_no_stored_key_raises(self) -> None:
        row = _tenant(mode="byo_openai", chat_model="gpt-5.4")

        with pytest.raises(LLMConfigurationError, match="no stored API key"):
            provider_for_tenant(row, settings=_settings())

    def test_a_mode_with_no_chat_model_raises(self, cipher: Fernet) -> None:
        row = _tenant(mode="byo_openai", api_key_enc=cipher.encrypt(TENANT_KEY.encode()))

        with pytest.raises(LLMConfigurationError, match="no chat model"):
            provider_for_tenant(row, settings=_settings())

    def test_a_key_that_cannot_be_decrypted_raises(self, cipher: Fernet) -> None:
        """Almost always a rotated `CREDENTIALS_ENCRYPTION_KEY`. Returning the
        platform provider here would look exactly like a tenant who never
        configured anything."""
        from app.core.crypto import CredentialDecryptionError

        row = _tenant(
            mode="byo_openai",
            chat_model="gpt-5.4",
            api_key_enc=Fernet(Fernet.generate_key()).encrypt(b"other key"),
        )

        with pytest.raises(CredentialDecryptionError):
            provider_for_tenant(row, settings=_settings())
