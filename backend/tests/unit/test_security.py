"""Password hashing and token primitives.

These assert the properties the rest of the auth code depends on - a salted
Argon2id hash, a digest that never reveals its token - rather than restating
the implementation.
"""

from __future__ import annotations

from app.core.security import (
    TOKEN_ENTROPY_BYTES,
    generate_token,
    hash_password,
    hash_token,
    tokens_match,
    verify_password,
)


class TestPasswordHashing:
    def test_hash_is_argon2_and_not_the_password(self) -> None:
        digest = hash_password("correct horse battery staple")
        assert digest.startswith("$argon2")
        assert "correct horse battery staple" not in digest

    def test_correct_password_verifies(self) -> None:
        digest = hash_password("a-very-long-password")
        assert verify_password("a-very-long-password", digest) is True

    def test_wrong_password_is_rejected(self) -> None:
        digest = hash_password("a-very-long-password")
        assert verify_password("a-very-long-passworD", digest) is False

    def test_same_password_hashes_differently_each_time(self) -> None:
        """Salted: two accounts with the same password must not collide."""
        first = hash_password("shared-password-123")
        second = hash_password("shared-password-123")
        assert first != second
        assert verify_password("shared-password-123", first)
        assert verify_password("shared-password-123", second)


class TestTokens:
    def test_tokens_are_unique_and_url_safe(self) -> None:
        tokens = {generate_token() for _ in range(50)}
        assert len(tokens) == 50
        allowed = set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_")
        assert all(set(token) <= allowed for token in tokens)

    def test_token_carries_the_intended_entropy(self) -> None:
        # base64url of N bytes is ceil(4N/3) characters, unpadded.
        assert len(generate_token()) >= (TOKEN_ENTROPY_BYTES * 4) // 3

    def test_digest_is_stable_and_hides_the_token(self) -> None:
        token = generate_token()
        digest = hash_token(token)
        assert digest == hash_token(token)
        assert token not in digest
        assert len(digest) == 64

    def test_comparison_accepts_only_the_original(self) -> None:
        token = generate_token()
        digest = hash_token(token)
        assert tokens_match(token, digest) is True
        assert tokens_match(generate_token(), digest) is False
