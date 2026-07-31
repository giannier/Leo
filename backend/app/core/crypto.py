"""Application-level encryption for credentials stored in the database.

Fernet (AES-128-CBC + HMAC) with the key held in the environment, never in
the database. An attacker who dumps Postgres gets ciphertext; they need the
process environment as well.

Chosen over pgcrypto because there the key travels inside the SQL statement
and ends up in query logs, and over a secrets manager because that is
infrastructure this project does not have yet. See plan.md.
"""

from __future__ import annotations

from cryptography.fernet import Fernet, InvalidToken

from app.core.config import get_settings


class CredentialDecryptionError(RuntimeError):
    """Ciphertext could not be decrypted with the configured key.

    Almost always means the key was rotated without re-encrypting stored
    credentials. Surfaced explicitly rather than returning an empty string,
    because a silent failure here looks like "the customer never configured
    a key" and is very hard to diagnose later.
    """


def _cipher() -> Fernet:
    key = get_settings().credentials_encryption_key
    if not key:
        raise RuntimeError(
            "CREDENTIALS_ENCRYPTION_KEY is not set. Generate one with "
            '`python -c "from cryptography.fernet import Fernet; '
            'print(Fernet.generate_key().decode())"` and put it in .env.'
        )
    return Fernet(key.encode() if isinstance(key, str) else key)


def encrypt_secret(plaintext: str) -> bytes:
    """Encrypt a credential for storage in a BYTEA column."""
    return _cipher().encrypt(plaintext.encode("utf-8"))


def decrypt_secret(ciphertext: bytes) -> str:
    """Decrypt a credential read from a BYTEA column."""
    try:
        return _cipher().decrypt(ciphertext).decode("utf-8")
    except InvalidToken as exc:
        raise CredentialDecryptionError(
            "Stored credential could not be decrypted with the current "
            "CREDENTIALS_ENCRYPTION_KEY. If the key was rotated, stored "
            "credentials must be re-encrypted or cleared."
        ) from exc


def mask_secret(plaintext: str, *, visible: int = 4) -> str:
    """Render a credential for display: last few characters only.

    Used wherever a key reaches a UI or a log line. Short values are masked
    entirely rather than partially revealed.
    """
    if len(plaintext) <= visible * 2:
        return "*" * len(plaintext)
    return f"{'*' * 8}{plaintext[-visible:]}"
