"""Password hashing and single-use token primitives.

Two different one-way functions, for two different threat models:

* **Passwords** are low-entropy and chosen by humans, so they need a slow,
  memory-hard KDF. Argon2id (via passlib) is the current OWASP first choice
  and is what the project standardised on - not bcrypt.
* **Tokens** (email verification, and later invitations and refresh tokens)
  are 256 bits of `secrets` entropy. Brute force is not a threat, so a plain
  SHA-256 digest is enough and keeps lookups a single indexed comparison.

Only the digest ever reaches the database. A dump of `email_tokens` yields
nothing a reader can present back to the API.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets

from passlib.context import CryptContext

# `deprecated="auto"` means a hash produced by an older scheme would be
# reported as needing a rehash rather than silently accepted forever, if a
# second scheme is ever added.
_password_context = CryptContext(schemes=["argon2"], deprecated="auto")

# 32 bytes -> 43 URL-safe characters, comfortably above the 128 bits that
# make guessing pointless while still fitting in a query string.
TOKEN_ENTROPY_BYTES = 32


def hash_password(password: str) -> str:
    """Derive the storable Argon2id hash of a plaintext password."""
    # passlib ships no type information, so its return value is `Any`; the
    # annotation is what pins it back down to `str` for the type checker.
    digest: str = _password_context.hash(password)
    return digest


def verify_password(password: str, password_hash: str) -> bool:
    """Check a plaintext password against a stored Argon2id hash."""
    matches: bool = _password_context.verify(password, password_hash)
    return matches


def generate_token() -> str:
    """Return a fresh, URL-safe, single-use token in plaintext.

    This is the only moment the plaintext exists on our side: it goes into
    the email and is then discarded. Everything stored is the digest.
    """
    return secrets.token_urlsafe(TOKEN_ENTROPY_BYTES)


def hash_token(token: str) -> str:
    """Digest a token for storage and for lookup."""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def tokens_match(token: str, token_hash: str) -> bool:
    """Constant-time comparison of a presented token against a stored digest.

    Lookups go through the unique index on `token_hash`, which is not
    constant time, but any comparison done in Python should be.
    """
    return hmac.compare_digest(hash_token(token), token_hash)
