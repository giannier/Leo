"""Signed access tokens.

The access token is a short-lived JWT (HS256), and it is the *only* credential
the API accepts on an authenticated request. It is deliberately stateless: no
database round trip is needed to know who is calling, which is what keeps a
15-minute lifetime affordable.

Being stateless is also its weakness - an issued token cannot be recalled - so
two rules bound the damage:

* **It expires in minutes, not days.** Long-lived access is the refresh
  token's job, and that one *is* stateful and revocable
  (`app/services/sessions.py`).
* **It carries identity, never authority.** The claims say *who* the caller is
  and *which tenant* they selected. Whether that membership still exists,
  whether it is still active, and what role it grants are read from the
  database on every request (`app/api/deps.py`). Baking the role into the
  token would mean a member removed from a team keeps their permissions until
  the token expires.

`typ` pins the token to this purpose. Nothing else in the system is a JWT
today, but a future signed token (a download URL, an unsubscribe link) signed
with the same key must not be accepted here by accident.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import jwt

from app.core.config import Settings, get_settings

# Value of the `typ` claim on an access token.
ACCESS_TOKEN_TYPE = "access"

# Claims a token must carry to be considered well formed at all. Listed
# explicitly so a token minted without an expiry is rejected rather than
# treated as one that never expires.
_REQUIRED_CLAIMS = ["sub", "exp", "iat", "typ", "jti"]


class AccessTokenError(Exception):
    """Base class for every reason a presented access token is not usable."""


class ExpiredAccessTokenError(AccessTokenError):
    """The signature is good but the token is past its expiry.

    Kept distinct from `InvalidAccessTokenError` so the API can answer with a
    different code: the client should refresh, not send the user back to the
    login form.
    """


class InvalidAccessTokenError(AccessTokenError):
    """Malformed, wrongly signed, or not an access token."""


@dataclass(frozen=True)
class AccessTokenClaims:
    """The parsed, verified contents of an access token.

    `tenant_id` is optional: a platform admin has no membership, and a user
    whose last membership was removed still has a valid identity. Endpoints
    that need a tenant require it explicitly.
    """

    token_id: uuid.UUID
    user_id: uuid.UUID
    tenant_id: uuid.UUID | None
    issued_at: datetime
    expires_at: datetime


def issue_access_token(
    *,
    user_id: uuid.UUID,
    tenant_id: uuid.UUID | None,
    now: datetime,
    settings: Settings | None = None,
) -> tuple[str, datetime]:
    """Mint an access token. Returns the token and the moment it expires.

    `now` is a parameter rather than read from the clock here so the caller
    can stamp the whole login - access token, refresh token, database rows -
    with one consistent instant.
    """
    config = settings or get_settings()
    expires_at = now + timedelta(minutes=config.access_token_expire_minutes)
    payload = {
        "sub": str(user_id),
        "tenant_id": str(tenant_id) if tenant_id is not None else None,
        "typ": ACCESS_TOKEN_TYPE,
        # Every issuance is individually identifiable. Without it, two tokens
        # minted for the same user in the same second are byte-identical -
        # harmless in itself, but it makes a refresh look like it did nothing,
        # and it leaves nothing to correlate a request with in the logs.
        "jti": str(uuid.uuid4()),
        "iat": int(now.timestamp()),
        "exp": int(expires_at.timestamp()),
    }
    token = jwt.encode(payload, config.secret_key, algorithm=config.jwt_algorithm)
    return token, expires_at


def decode_access_token(token: str, *, settings: Settings | None = None) -> AccessTokenClaims:
    """Verify a token and return its claims, or raise.

    `algorithms` is pinned to the single configured algorithm. Passing the
    list the token itself asks for is the classic JWT vulnerability: a token
    with `alg: none`, or one signed with HMAC using the public key of an
    asymmetric pair, would verify.
    """
    config = settings or get_settings()
    try:
        payload = jwt.decode(
            token,
            config.secret_key,
            algorithms=[config.jwt_algorithm],
            options={"require": _REQUIRED_CLAIMS},
        )
    except jwt.ExpiredSignatureError as exc:
        raise ExpiredAccessTokenError("the access token has expired") from exc
    except jwt.InvalidTokenError as exc:
        raise InvalidAccessTokenError(str(exc)) from exc

    if payload.get("typ") != ACCESS_TOKEN_TYPE:
        raise InvalidAccessTokenError(f"not an access token: typ={payload.get('typ')!r}")

    try:
        token_id = uuid.UUID(str(payload["jti"]))
        user_id = uuid.UUID(str(payload["sub"]))
        raw_tenant = payload.get("tenant_id")
        tenant_id = uuid.UUID(str(raw_tenant)) if raw_tenant is not None else None
    except (TypeError, ValueError) as exc:
        raise InvalidAccessTokenError("an identifier claim is not a UUID") from exc

    return AccessTokenClaims(
        token_id=token_id,
        user_id=user_id,
        tenant_id=tenant_id,
        issued_at=datetime.fromtimestamp(int(payload["iat"]), tz=UTC),
        expires_at=datetime.fromtimestamp(int(payload["exp"]), tz=UTC),
    )
