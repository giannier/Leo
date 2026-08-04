"""Credential validation and encryption at rest (TASK-017, RF-009)."""

from __future__ import annotations

import pytest
from cryptography.fernet import Fernet

from app.core.crypto import CredentialDecryptionError, decrypt_secret, encrypt_secret, mask_secret
from app.providers.whatsapp import (
    InvalidCredentials,
    NumberCredentials,
    WhatsAppProvider,
    YCloudProvider,
)


@pytest.fixture(autouse=True)
def encryption_key(monkeypatch: pytest.MonkeyPatch) -> str:
    """A key generated per test run.

    Never the deployment's: a test that depended on `.env` would pass or fail
    according to a file nobody committed.
    """
    key = Fernet.generate_key().decode()
    monkeypatch.setattr("app.core.crypto.get_settings", lambda: _Settings(key))
    return key


class _Settings:
    def __init__(self, key: str | None) -> None:
        self.credentials_encryption_key = key


def _valid() -> NumberCredentials:
    return NumberCredentials(
        provider="ycloud",
        values={
            "waba_id": "waba_1234567890",
            "phone_number_id": "pnid_1234567890",
            "api_key": "yc_live_abcdef123456",
        },
    )


class TestProviderContract:
    def test_the_adapter_satisfies_the_port(self) -> None:
        assert isinstance(YCloudProvider(), WhatsAppProvider)

    def test_valid_credentials_pass(self) -> None:
        YCloudProvider().validate(_valid())

    @pytest.mark.parametrize("missing", ["waba_id", "phone_number_id", "api_key"])
    def test_each_required_key_is_required(self, missing: str) -> None:
        credentials = _valid()
        del credentials.values[missing]
        with pytest.raises(InvalidCredentials, match=missing):
            YCloudProvider().validate(credentials)

    def test_a_placeholder_is_rejected(self) -> None:
        """The realistic failure is somebody tabbing through the form and
        saving "TODO", not a malformed token."""
        credentials = _valid()
        credentials.values["api_key"] = "TODO"
        with pytest.raises(InvalidCredentials, match="implausibly short"):
            YCloudProvider().validate(credentials)

    def test_the_wrong_provider_is_rejected(self) -> None:
        credentials = NumberCredentials(provider="meta", values=_valid().values)
        with pytest.raises(InvalidCredentials, match="ycloud"):
            YCloudProvider().validate(credentials)


class TestEncryptionAtRest:
    def test_a_credential_round_trips(self) -> None:
        assert decrypt_secret(encrypt_secret("yc_live_secret")) == "yc_live_secret"

    def test_the_ciphertext_does_not_contain_the_plaintext(self) -> None:
        """The point of RF-009: a database dump must not be a set of working
        WhatsApp credentials for every business on the platform."""
        ciphertext = encrypt_secret("yc_live_secret")
        assert b"yc_live_secret" not in ciphertext

    def test_the_same_value_encrypts_differently_each_time(self) -> None:
        """Fernet includes a random IV. Identical ciphertexts would let a
        reader of the table tell which businesses share a credential."""
        assert encrypt_secret("same") != encrypt_secret("same")

    def test_a_rotated_key_fails_loudly(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Silence here looks exactly like "the customer never configured a
        number", which is very hard to diagnose later."""
        ciphertext = encrypt_secret("yc_live_secret")
        monkeypatch.setattr(
            "app.core.crypto.get_settings", lambda: _Settings(Fernet.generate_key().decode())
        )
        with pytest.raises(CredentialDecryptionError, match="re-encrypted"):
            decrypt_secret(ciphertext)

    def test_a_missing_key_names_the_fix(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr("app.core.crypto.get_settings", lambda: _Settings(None))
        with pytest.raises(RuntimeError, match="CREDENTIALS_ENCRYPTION_KEY"):
            encrypt_secret("x")


class TestMasking:
    def test_only_the_tail_survives(self) -> None:
        assert mask_secret("yc_live_abcdef123456").endswith("3456")
        assert "abcdef" not in mask_secret("yc_live_abcdef123456")

    def test_a_short_value_is_masked_entirely(self) -> None:
        """Showing four of six characters would give most of it away."""
        assert set(mask_secret("abc123")) == {"*"}
