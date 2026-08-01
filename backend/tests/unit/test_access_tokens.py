"""Unit tests for the access token primitives.

These are the tests for the thing that decides who a request is from, so they
are mostly about *rejection*: a token that should not verify, verifying, is
the whole vulnerability.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import jwt
import pytest

from app.core.config import INSECURE_SECRET_KEY, Settings
from app.core.tokens import (
    ACCESS_TOKEN_TYPE,
    ExpiredAccessTokenError,
    InvalidAccessTokenError,
    decode_access_token,
    issue_access_token,
)


@pytest.fixture
def settings() -> Settings:
    """Explicit settings, so nothing here depends on the ambient `.env`."""
    return Settings(
        environment="test",
        secret_key="unit-test-signing-key-long-enough-for-hs256",
        access_token_expire_minutes=15,
    )


def _claims(**overrides: object) -> dict[str, object]:
    """A payload our own issuer would have produced, ready to be spoilt."""
    now = datetime.now(UTC)
    claims: dict[str, object] = {
        "sub": str(uuid.uuid4()),
        "tenant_id": None,
        "typ": ACCESS_TOKEN_TYPE,
        "jti": str(uuid.uuid4()),
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(hours=1)).timestamp()),
    }
    claims.update(overrides)
    return claims


def _sign(settings: Settings, claims: dict[str, object]) -> str:
    return jwt.encode(claims, settings.secret_key, algorithm=settings.jwt_algorithm)


def _issue(settings: Settings, **overrides: object) -> tuple[str, uuid.UUID, uuid.UUID]:
    user_id = uuid.uuid4()
    tenant_id = uuid.uuid4()
    now = datetime.now(UTC)
    token, _ = issue_access_token(
        user_id=user_id,
        tenant_id=tenant_id,
        now=now,
        settings=settings,
        **overrides,  # type: ignore[arg-type]
    )
    return token, user_id, tenant_id


class TestRoundTrip:
    def test_claims_survive_the_round_trip(self, settings: Settings) -> None:
        token, user_id, tenant_id = _issue(settings)

        claims = decode_access_token(token, settings=settings)

        assert claims.user_id == user_id
        assert claims.tenant_id == tenant_id

    def test_expiry_follows_the_configured_lifetime(self, settings: Settings) -> None:
        now = datetime.now(UTC)
        _, expires_at = issue_access_token(
            user_id=uuid.uuid4(), tenant_id=None, now=now, settings=settings
        )

        assert expires_at - now == timedelta(minutes=15)

    def test_the_configured_lifetime_is_the_contracted_fifteen_minutes(self) -> None:
        """contracts/api.md and tasks.md both say 15 minutes."""
        assert Settings(environment="test").access_token_expire_minutes == 15

    def test_a_token_without_a_tenant_is_valid(self, settings: Settings) -> None:
        """A platform admin belongs to no tenant but is still someone."""
        user_id = uuid.uuid4()
        token, _ = issue_access_token(
            user_id=user_id, tenant_id=None, now=datetime.now(UTC), settings=settings
        )

        claims = decode_access_token(token, settings=settings)

        assert claims.user_id == user_id
        assert claims.tenant_id is None

    def test_the_password_is_nowhere_near_the_token(self, settings: Settings) -> None:
        """A JWT is signed, not encrypted: its payload is world-readable."""
        token, user_id, _ = _issue(settings)
        payload = jwt.decode(token, options={"verify_signature": False})

        assert payload["sub"] == str(user_id)
        assert payload["typ"] == ACCESS_TOKEN_TYPE
        assert set(payload) == {"sub", "tenant_id", "typ", "jti", "iat", "exp"}
        # Notably absent: `role`. Authority is read from the database on
        # every request so that removing a member takes effect at once.
        assert "role" not in payload

    def test_two_tokens_minted_in_the_same_instant_still_differ(self, settings: Settings) -> None:
        """`iat` has one-second resolution, so without `jti` a refresh could
        hand back a byte-identical token and look like it did nothing."""
        user_id = uuid.uuid4()
        now = datetime.now(UTC)
        first, _ = issue_access_token(user_id=user_id, tenant_id=None, now=now, settings=settings)
        second, _ = issue_access_token(user_id=user_id, tenant_id=None, now=now, settings=settings)

        assert first != second
        assert (
            decode_access_token(first, settings=settings).token_id
            != decode_access_token(second, settings=settings).token_id
        )


class TestRejection:
    def test_an_expired_token_is_reported_as_expired(self, settings: Settings) -> None:
        long_ago = datetime.now(UTC) - timedelta(hours=2)
        token, _ = issue_access_token(
            user_id=uuid.uuid4(), tenant_id=None, now=long_ago, settings=settings
        )

        with pytest.raises(ExpiredAccessTokenError):
            decode_access_token(token, settings=settings)

    def test_a_token_signed_with_another_key_is_refused(self, settings: Settings) -> None:
        token, _, _ = _issue(settings)
        other = Settings(
            environment="test", secret_key="a-completely-different-key-of-ample-length"
        )

        with pytest.raises(InvalidAccessTokenError):
            decode_access_token(token, settings=other)

    def test_a_tampered_payload_is_refused(self, settings: Settings) -> None:
        """Editing the subject invalidates the signature over it."""
        token, _, _ = _issue(settings)
        header, _, signature = token.split(".")
        forged_payload = (
            jwt.api_jws.base64url_encode(  # type: ignore[attr-defined]
                b'{"sub":"00000000-0000-0000-0000-000000000000","tenant_id":null,'
                b'"typ":"access","jti":"11111111-1111-1111-1111-111111111111",'
                b'"iat":0,"exp":9999999999}'
            )
        ).decode()

        with pytest.raises(InvalidAccessTokenError):
            decode_access_token(f"{header}.{forged_payload}.{signature}", settings=settings)

    def test_an_unsigned_token_is_refused(self, settings: Settings) -> None:
        """The `alg: none` attack: pinning the algorithm is what stops it."""
        forged = jwt.encode(_claims(), key="", algorithm="none")

        with pytest.raises(InvalidAccessTokenError):
            decode_access_token(forged, settings=settings)

    def test_a_token_of_another_type_is_refused(self, settings: Settings) -> None:
        """Correctly signed with our key, but minted for another purpose."""
        forged = _sign(settings, _claims(typ="password_reset"))

        with pytest.raises(InvalidAccessTokenError, match="not an access token"):
            decode_access_token(forged, settings=settings)

    @pytest.mark.parametrize("missing", ["sub", "exp", "iat", "typ", "jti"])
    def test_a_token_missing_a_required_claim_is_refused(
        self, settings: Settings, missing: str
    ) -> None:
        """A missing `exp` in particular would otherwise read as "never
        expires" - the failure mode worth being blunt about."""
        claims = _claims()
        del claims[missing]

        with pytest.raises(InvalidAccessTokenError):
            decode_access_token(_sign(settings, claims), settings=settings)

    @pytest.mark.parametrize("claim", ["sub", "jti", "tenant_id"])
    def test_a_non_uuid_identifier_is_refused(self, settings: Settings, claim: str) -> None:
        forged = _sign(settings, _claims(**{claim: "administrator"}))

        with pytest.raises(InvalidAccessTokenError, match="not a UUID"):
            decode_access_token(forged, settings=settings)

    @pytest.mark.parametrize("garbage", ["", "not-a-token", "a.b.c", "Bearer x.y.z"])
    def test_garbage_is_refused(self, settings: Settings, garbage: str) -> None:
        with pytest.raises(InvalidAccessTokenError):
            decode_access_token(garbage, settings=settings)


class TestDeploymentSafety:
    @pytest.mark.parametrize(
        "placeholder",
        # The current placeholder, the one it replaced (an old `.env`
        # promoted to production must still be refused), and the two values
        # anyone reaches for when improvising.
        ["dev-secret-key-change-me-in-production", "dev-secret-key-change-me", "secret"],
    )
    def test_a_deployed_environment_refuses_a_placeholder_secret(self, placeholder: str) -> None:
        """Signing with a key that is committed to a public repository means
        anyone can mint a token for any account. Fail at startup instead."""
        with pytest.raises(ValueError, match="SECRET_KEY"):
            Settings(environment="production", secret_key=placeholder, trust_proxy_headers=False)

    def test_a_deployed_environment_refuses_a_short_secret(self) -> None:
        """RFC 7518: an HS256 key below 32 bytes weakens the MAC."""
        with pytest.raises(ValueError, match="at least 32"):
            Settings(
                environment="staging",
                secret_key="s3cr3t-but-far-too-short",
                trust_proxy_headers=False,
            )

    @pytest.mark.parametrize("environment", ["development", "test"])
    def test_a_local_environment_keeps_working_with_a_placeholder(self, environment: str) -> None:
        """A local stack that will not boot without a generated secret is a
        local stack nobody runs.

        The value is passed explicitly rather than read from the default,
        because a developer's own `.env` overrides the default and this test
        is about the rule, not about their machine.
        """
        settings = Settings(
            environment=environment,  # type: ignore[arg-type]
            secret_key=INSECURE_SECRET_KEY,
        )
        assert settings.secret_key == INSECURE_SECRET_KEY

    def test_the_placeholder_itself_clears_the_length_floor(self) -> None:
        """Otherwise every local request logs a PyJWT key-length warning."""
        assert len(INSECURE_SECRET_KEY) >= 32

    def test_a_deployed_environment_accepts_a_real_secret(self) -> None:
        settings = Settings(
            environment="production",
            secret_key="a-real-generated-secret-of-ample-length",
            trust_proxy_headers=False,
        )
        assert settings.is_deployed is True

    @pytest.mark.parametrize(
        ("environment", "deployed"),
        [
            ("development", False),
            ("test", False),
            ("staging", True),
            ("production", True),
        ],
    )
    def test_is_deployed_drives_the_secure_cookie_flag(
        self, environment: str, deployed: bool
    ) -> None:
        settings = Settings(
            environment=environment,  # type: ignore[arg-type]
            secret_key="a-real-generated-secret-of-ample-length",
            # Stated because a deployed Settings refuses to build without it.
            # This case is about the cookie flag, not about proxies.
            trust_proxy_headers=False,
        )
        assert settings.is_deployed is deployed
