"""Shared FastAPI dependencies.

Two groups live here.

The **infrastructure** dependencies (`get_db`, `get_email_provider`,
`get_rate_limiter`, `get_storage_provider`, `get_job_queue`) exist to be
overridden in tests: a session already inside a rolled-back transaction, a
mail provider that records instead of sending, a limiter that counts nothing,
an object store held in memory, a queue that records instead of enqueueing.

The **authentication** chain is the one every protected endpoint from
TASK-009 onward is built on, and it is deliberately a chain rather than one
function, because each link answers a different question and endpoints need
different depths of it:

    bearer credentials      -> is there a token at all?
    get_access_claims       -> is it ours, unexpired, an access token?
    get_current_user        -> does that user still exist, and is the address verified?
    get_current_principal   -> is the tenant in the token one they may still act in, as what?
    require_roles(...)      -> is that role enough for this endpoint?

Two decisions are worth stating, because both cost a database read that could
have been avoided by trusting the token:

**Authority is never read from the token.** The claims carry identity - who,
and which tenant they selected - and nothing else. The membership and its
role are read fresh on every request. The alternative, a `role` claim, would
mean an operator promoted to admin waits fifteen minutes for it, and far
worse, a member removed from a team keeps their access for the same fifteen.
TASK-009 has to revoke access on deactivation; this is what makes that
possible without a token denylist.

**Email verification is checked live, not stamped into the token.** Same
reason in reverse: it is one read, and it keeps Clarification 005's rule -
no unverified account gets into the panel - in a single place that no future
endpoint can forget.
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import AsyncIterator, Callable, Iterator
from dataclasses import dataclass
from functools import lru_cache
from typing import Annotated

from fastapi import Depends, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session

from app.api.errors import ApiError
from app.core.config import Settings, get_settings
from app.core.db import get_session_factory
from app.core.queue import JobQueue, create_job_queue
from app.core.rate_limit import RateLimiter, RedisRateLimiter
from app.core.redis import get_redis_client
from app.core.tokens import (
    AccessTokenClaims,
    ExpiredAccessTokenError,
    InvalidAccessTokenError,
    decode_access_token,
)
from app.models.user import User
from app.providers.email.base import EmailConfigurationError, EmailProvider
from app.providers.email.factory import build_email_provider
from app.providers.storage.base import StorageProvider
from app.providers.storage.s3_provider import S3StorageProvider
from app.repositories.tenant import (
    OPERATOR_ROLE,
    OWNER_ROLE,
    TENANT_ADMIN_ROLE,
    TenantUserRepository,
)
from app.repositories.user import UserRepository

logger = logging.getLogger(__name__)

# Role sets, straight from Clarification 008. Named rather than spelled out at
# each call site so the permission model is readable in one place and a change
# to it is one edit.
#
#   owner         everything
#   tenant_admin  everything except deleting the account and billing
#   operator      the inbox only
ANY_MEMBER: tuple[str, ...] = (OWNER_ROLE, TENANT_ADMIN_ROLE, OPERATOR_ROLE)
MANAGES_CONFIGURATION: tuple[str, ...] = (OWNER_ROLE, TENANT_ADMIN_ROLE)
OWNS_THE_ACCOUNT: tuple[str, ...] = (OWNER_ROLE,)

# `auto_error=False` so a missing or malformed header comes back as `None`
# and this module answers with the documented `{error, message}` envelope,
# instead of FastAPI's own 403 with a `detail` body. Declaring the scheme is
# still worth it: it is what puts `bearerAuth` in the OpenAPI document, which
# is where TASK-020's generated client gets its auth handling from.
_bearer_scheme = HTTPBearer(
    scheme_name="bearerAuth",
    description="Access token from `POST /auth/login`.",
    auto_error=False,
)

_UNAUTHENTICATED_HEADERS = {"WWW-Authenticate": "Bearer"}


def get_db() -> Iterator[Session]:
    """A session for the duration of one request.

    Owns the session, not the transaction: services commit their own units
    of work. The rollback here is the safety net for an exception that
    escaped a service mid-transaction, so a half-written unit of work is
    never left for the connection pool to hand to the next request.
    """
    session = get_session_factory()()
    try:
        yield session
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def get_email_provider() -> EmailProvider | None:
    """The configured email provider, or `None` when there is no credential.

    Returning `None` rather than raising keeps registration working on a
    deployment that cannot send mail yet: the account is still created and
    the response says the message did not go out. The warning is logged once
    per attempt so the gap is visible rather than assumed.
    """
    try:
        return build_email_provider()
    except EmailConfigurationError as exc:
        logger.warning("email provider unavailable: %s", exc)
        return None


def get_storage_provider() -> StorageProvider:
    """The configured object store, for PDF uploads."""
    settings = get_settings()
    return S3StorageProvider(
        bucket=settings.s3_bucket,
        endpoint_url=settings.s3_endpoint_url,
        access_key=settings.s3_access_key,
        secret_key=settings.s3_secret_key,
        region=settings.s3_region,
    )


async def get_job_queue() -> AsyncIterator[JobQueue]:
    """A Redis pool for the duration of one request.

    Opened per request rather than shared, so a broken connection cannot
    outlive the request that found it. Enqueueing happens once per upload, so
    the extra handshake is not on any hot path.
    """
    queue = await create_job_queue()
    try:
        yield queue
    finally:
        await queue.close()


# --------------------------------------------------------------- rate limit


@lru_cache
def _auth_rate_limiter() -> RedisRateLimiter:
    """Built once per process: the limit is shared, not per request."""
    settings = get_settings()
    return RedisRateLimiter(
        get_redis_client(),
        limit=settings.auth_rate_limit_requests,
        window_seconds=settings.auth_rate_limit_window_seconds,
        namespace="ratelimit:auth",
    )


def get_rate_limiter() -> RateLimiter:
    """The limiter guarding `/auth/*`. Overridden in tests."""
    return _auth_rate_limiter()


def client_identifier(request: Request, settings: Settings) -> str:
    """Who to count this request against.

    Behind a reverse proxy every request arrives from the proxy's own
    address, so a per-IP limit would be a global one - the whole platform
    sharing five logins a minute. `X-Forwarded-For` fixes that, but only
    where it can be trusted: a client can send the header itself, and a
    limiter keyed on an attacker-chosen string is no limiter at all. Hence
    the explicit `trust_proxy_headers` switch, off unless a deployment says
    otherwise.

    The **last** entry is used, not the first. Each hop appends, so the tail
    is what the proxy we trust observed; the head is whatever the client
    claimed before the request ever reached us.
    """
    if settings.trust_proxy_headers:
        forwarded = request.headers.get("x-forwarded-for")
        if forwarded:
            hops = [hop.strip() for hop in forwarded.split(",") if hop.strip()]
            if hops:
                return hops[-1]
    return request.client.host if request.client else "unknown"


async def enforce_auth_rate_limit(
    request: Request,
    limiter: Annotated[RateLimiter, Depends(get_rate_limiter)],
) -> None:
    """Throttle `/auth/*` per client IP (contracts/api.md: 5/min/IP).

    Applied to the whole router rather than to `login` alone, so password
    guessing cannot simply move to `resend-verification` or, from TASK-008,
    to the OTP endpoints.
    """
    decision = await limiter.hit(client_identifier(request, get_settings()))
    if decision.allowed:
        return
    raise ApiError(
        status_code=status.HTTP_429_TOO_MANY_REQUESTS,
        error="rate_limited",
        message="Too many authentication requests. Please wait and try again.",
        headers={"Retry-After": str(decision.retry_after_seconds)},
    )


# ------------------------------------------------------------ authentication


@dataclass(frozen=True)
class Principal:
    """A caller acting in one tenant, with the role they hold there *now*."""

    user: User
    tenant_id: uuid.UUID
    role: str

    @property
    def user_id(self) -> uuid.UUID:
        return self.user.id


SessionDep = Annotated[Session, Depends(get_db)]
BearerDep = Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer_scheme)]


def get_access_claims(credentials: BearerDep) -> AccessTokenClaims:
    """Verify the bearer token and return its claims."""
    if credentials is None or not credentials.credentials:
        raise ApiError(
            status_code=status.HTTP_401_UNAUTHORIZED,
            error="not_authenticated",
            message="This endpoint requires an access token.",
            headers=_UNAUTHENTICATED_HEADERS,
        )
    try:
        return decode_access_token(credentials.credentials)
    except ExpiredAccessTokenError as exc:
        # Distinct from `invalid_token` so the client refreshes instead of
        # dropping the user back on the login form every fifteen minutes.
        raise ApiError(
            status_code=status.HTTP_401_UNAUTHORIZED,
            error="token_expired",
            message="The access token has expired. Refresh it and retry.",
            headers=_UNAUTHENTICATED_HEADERS,
        ) from exc
    except InvalidAccessTokenError as exc:
        raise ApiError(
            status_code=status.HTTP_401_UNAUTHORIZED,
            error="invalid_token",
            message="The access token is not valid.",
            headers=_UNAUTHENTICATED_HEADERS,
        ) from exc


ClaimsDep = Annotated[AccessTokenClaims, Depends(get_access_claims)]


def get_current_user(claims: ClaimsDep, session: SessionDep) -> User:
    """The signed-in user, refused if the address is not verified.

    Clarification 005: a verified email is what admits someone to the panel -
    and, on its own, what unlocks the sandbox. `login` already refuses an
    unverified account, so reaching this check means either a token minted
    before verification was somehow withdrawn, or an endpoint being called
    with a hand-made token. Both should fail.
    """
    user = UserRepository(session).get(claims.user_id)
    if user is None:
        # The signature was valid, so this is a deleted account rather than a
        # forgery - but the client is told the same thing either way.
        raise ApiError(
            status_code=status.HTTP_401_UNAUTHORIZED,
            error="invalid_token",
            message="The access token is not valid.",
            headers=_UNAUTHENTICATED_HEADERS,
        )
    if user.email_verified_at is None:
        raise ApiError(
            status_code=status.HTTP_403_FORBIDDEN,
            error="email_not_verified",
            message="Verify your email address before using this endpoint.",
        )
    return user


CurrentUserDep = Annotated[User, Depends(get_current_user)]


def get_current_principal(
    user: CurrentUserDep,
    claims: ClaimsDep,
    session: SessionDep,
) -> Principal:
    """Resolve the tenant named in the token into a live membership.

    The claim selects a tenant; it does not grant one. The membership is
    re-read here on every request, through the tenant-scoped repository, so
    that a token naming somebody else's tenant finds nothing - and so that
    removing or disabling a member takes effect immediately rather than when
    their access token happens to run out.
    """
    if claims.tenant_id is None:
        raise ApiError(
            status_code=status.HTTP_403_FORBIDDEN,
            error="no_active_tenant",
            message="This access token is not scoped to any tenant.",
        )

    membership = TenantUserRepository(session, claims.tenant_id).active_membership_for(user.id)
    if membership is None:
        # Indistinguishable from "that tenant does not exist", on purpose:
        # confirming the id is real would leak the existence of another
        # business account.
        raise ApiError(
            status_code=status.HTTP_403_FORBIDDEN,
            error="tenant_access_denied",
            message="You are not an active member of this tenant.",
        )
    return Principal(user=user, tenant_id=claims.tenant_id, role=membership.role)


CurrentPrincipalDep = Annotated[Principal, Depends(get_current_principal)]


def require_roles(*roles: str) -> Callable[[Principal], Principal]:
    """Build a dependency that admits only the listed roles.

    Returns the principal, so an endpoint can depend on the guard alone and
    still get the caller rather than declaring the same thing twice.

    Prefer the ready-made aliases below to calling this at a route: a
    `Depends(...)` in an argument default is evaluated once at import and is
    the pattern ruff's B008 exists to catch.
    """
    allowed = frozenset(roles)
    if not allowed:
        raise ValueError("require_roles() needs at least one role")

    def guard(principal: CurrentPrincipalDep) -> Principal:
        if principal.role not in allowed:
            raise ApiError(
                status_code=status.HTTP_403_FORBIDDEN,
                error="insufficient_role",
                message="Your role does not allow this action.",
            )
        return principal

    return guard


# The three levels of authority from Clarification 008, ready to annotate an
# endpoint parameter with. Anything narrower is a one-off `require_roles(...)`.
#
# `MemberDep` is `CurrentPrincipalDep` rather than `require_roles(*ANY_MEMBER)`
# because resolving an active membership *is* the check at that level - a
# second pass over a set containing every role would only be ceremony.
MemberDep = CurrentPrincipalDep
ConfigurationManagerDep = Annotated[Principal, Depends(require_roles(*MANAGES_CONFIGURATION))]
AccountOwnerDep = Annotated[Principal, Depends(require_roles(*OWNS_THE_ACCOUNT))]
