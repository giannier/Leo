"""Authentication endpoints: registration, email verification and sessions.

Implements the `/auth` section of
`leo-sdd/features/001-onboarding/contracts/api.md` that TASK-006 and TASK-007
cover. Phone OTP arrives with TASK-008.

Request and response models live here rather than in a `schemas/` package,
matching `health.py` and the repository layout the project documents.

The refresh token never appears in a response body. It is set as an httpOnly
cookie so that a cross-site scripting bug in the panel cannot read it - the
whole point of pairing a short-lived access token the frontend does hold in
memory with a long-lived refresh token it cannot touch. See
`_set_refresh_cookie` for the attributes and why each one is what it is.
"""

from __future__ import annotations

import uuid
from typing import Annotated, Literal

from fastapi import APIRouter, Cookie, Depends, Response, status
from pydantic import BaseModel, Field, StringConstraints
from sqlalchemy.orm import Session

from app.api.deps import enforce_auth_rate_limit, get_db, get_email_provider
from app.api.errors import ApiError, error_responses
from app.core.config import Settings, get_settings
from app.providers.email.base import EmailProvider
from app.services.auth import (
    AuthService,
    EmailAlreadyRegisteredError,
    InvalidEmailTokenError,
    PhoneInActiveTrialError,
    TrialPlanUnavailableError,
)
from app.services.sessions import (
    AuthenticatedSession,
    EmailNotVerifiedError,
    InvalidCredentialsError,
    InvalidRefreshTokenError,
    SessionService,
    TenantNotAccessibleError,
)

# The rate limit guards the whole router, not just `login`: an attacker
# blocked at the login endpoint would otherwise simply move to
# `resend-verification`, or to the OTP endpoints TASK-008 adds here.
router = APIRouter(
    prefix="/auth",
    tags=["auth"],
    dependencies=[Depends(enforce_auth_rate_limit)],
)

REFRESH_COOKIE_NAME = "refresh_token"

# Deliberately permissive: it rejects obvious nonsense ("no @", spaces, no
# dot in the domain) and nothing else. A full RFC 5322 check would need the
# `email-validator` package, which is not an approved dependency - and it
# would add little, because the address is proved by the verification email
# regardless of what the syntax check says.
EMAIL_PATTERN = r"^[^@\s]+@[^@\s.]+(?:\.[^@\s.]+)+$"

# E.164: leading '+', no leading zero, 8 to 15 digits in total.
E164_PATTERN = r"^\+[1-9]\d{7,14}$"

# ISO 3166-1 alpha-2, uppercase.
COUNTRY_PATTERN = r"^[A-Z]{2}$"

MIN_PASSWORD_LENGTH = 10
# Argon2 has no short input ceiling the way bcrypt does; the cap is only
# there so a multi-megabyte "password" cannot be used to burn CPU.
MAX_PASSWORD_LENGTH = 256

EmailAddress = Annotated[
    str,
    StringConstraints(strip_whitespace=True, max_length=254, pattern=EMAIL_PATTERN),
]
ShortText = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=120)]
PhoneE164 = Annotated[str, StringConstraints(strip_whitespace=True, pattern=E164_PATTERN)]
CountryCode = Annotated[str, StringConstraints(strip_whitespace=True, pattern=COUNTRY_PATTERN)]


class RegisterRequest(BaseModel):
    """Payload of `POST /auth/register`."""

    full_name: ShortText
    email: EmailAddress
    password: str = Field(min_length=MIN_PASSWORD_LENGTH, max_length=MAX_PASSWORD_LENGTH)
    business_name: ShortText
    # Optional: the contact phone is only *required* before a number
    # connection request (Clarification 005, TASK-008). Supplying it here
    # takes the trial lock straight away.
    phone_e164: PhoneE164 | None = None
    # Accepted and validated, but not persisted: no table in data-model.md
    # has a country column. The wizard collects locale settings
    # (`business_profiles.currency` / `timezone`) in TASK-010. Kept in the
    # contract so the frontend does not have to change when it gains a home.
    country: CountryCode = "PE"


class RegisterResponse(BaseModel):
    """`201` body of `POST /auth/register`."""

    user_id: uuid.UUID
    tenant_id: uuid.UUID
    verification_email_sent: bool


class VerifyEmailRequest(BaseModel):
    token: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=512)]


class ResendVerificationRequest(BaseModel):
    email: EmailAddress


class LoginRequest(BaseModel):
    """Payload of `POST /auth/login`."""

    email: EmailAddress
    password: str = Field(max_length=MAX_PASSWORD_LENGTH)
    # Which membership to activate. Optional, and only meaningful once a
    # person belongs to more than one business - which happens from TASK-009,
    # when an existing user accepts an invitation. Omitted, the oldest
    # membership is activated, deterministically, so a client that never
    # sends this field always lands in the same tenant.
    tenant_id: uuid.UUID | None = None


class RefreshRequest(BaseModel):
    """Optional payload of `POST /auth/refresh`.

    The credential itself is the cookie; this body only carries the tenant
    the caller wants the *new* access token scoped to. Sending it is how a
    client switches business without re-entering a password, and how it keeps
    the active tenant stable across a refresh - the server cannot infer which
    of several tenants the panel is currently showing.
    """

    tenant_id: uuid.UUID | None = None


class UserSummary(BaseModel):
    """The signed-in identity, as the contract shapes it."""

    id: uuid.UUID
    full_name: str
    email: str


class MembershipSummary(BaseModel):
    """One business the caller belongs to, and their role in it."""

    tenant_id: uuid.UUID
    tenant_name: str
    role: str


class SessionResponse(BaseModel):
    """`200` body of both `POST /auth/login` and `POST /auth/refresh`.

    Refresh returns the same shape as login, not just the new access token.
    It costs nothing - the data is already loaded to build the token - and it
    is what lets the panel restore a session on a page reload: one call, and
    it knows who is signed in and which businesses they can switch to.
    """

    access_token: str
    token_type: Literal["bearer"] = "bearer"
    # Seconds, so the client can schedule its refresh instead of waiting for
    # a 401 and retrying every request.
    expires_in: int
    user: UserSummary
    memberships: list[MembershipSummary]
    # Which membership the returned access token is actually scoped to. Null
    # for a platform admin, who belongs to no tenant.
    active_tenant_id: uuid.UUID | None


SessionDep = Annotated[Session, Depends(get_db)]
EmailProviderDep = Annotated[EmailProvider | None, Depends(get_email_provider)]
RefreshCookieDep = Annotated[str | None, Cookie(alias=REFRESH_COOKIE_NAME)]


def _refresh_cookie_path(settings: Settings) -> str:
    """Scope the cookie to `/api/v1/auth`.

    A cookie sent on every request to the API is a cookie exposed on every
    request to the API. Only `refresh` and `logout` ever read it, and both
    live under this prefix, so nothing else needs to carry it.
    """
    return f"{settings.api_v1_prefix.rstrip('/')}/auth"


def _set_refresh_cookie(response: Response, session: AuthenticatedSession) -> None:
    """Attach the rotated refresh token to the response.

    * `httponly` - `document.cookie` cannot read it, so a script injected
      into the panel cannot steal the long-lived half of the session.
    * `secure` in staging and production - the token would otherwise travel
      in clear text. Off in development, where localhost is plain HTTP and
      the flag would simply stop the cookie being stored at all.
    * `samesite="lax"` - the browser will not attach this cookie to a
      cross-site POST, which is the entire CSRF defence for `/auth/refresh`
      and `/auth/logout`. `strict` would add nothing here (Lax already
      excludes every method except top-level GET navigation) and would break
      the panel if it is ever served from a different registrable domain
      than the API.
    * `max_age` matching the token's own expiry, so a browser does not keep
      presenting a cookie the database will refuse.
    * no `domain` - a host-only cookie is not shared with subdomains.
    """
    settings = get_settings()
    response.set_cookie(
        key=REFRESH_COOKIE_NAME,
        value=session.refresh_token,
        max_age=settings.refresh_token_expire_days * 24 * 60 * 60,
        path=_refresh_cookie_path(settings),
        httponly=True,
        secure=settings.is_deployed,
        samesite="lax",
    )


def _clear_refresh_cookie(response: Response) -> None:
    """Remove the cookie. Every attribute must match how it was set."""
    settings = get_settings()
    response.delete_cookie(
        key=REFRESH_COOKIE_NAME,
        path=_refresh_cookie_path(settings),
        httponly=True,
        secure=settings.is_deployed,
        samesite="lax",
    )


def _clear_refresh_cookie_header() -> dict[str, str]:
    """The same removal, as a header to hang on an error response.

    Needed because the two paths out of an endpoint are not symmetric:
    FastAPI merges the injected `Response`'s headers into a *returned* body,
    but an exception goes straight to the error handler and never sees them.
    Clearing the cookie on the way out of a failed refresh therefore has to
    travel with the `ApiError` itself.

    Built by asking a throwaway `Response` to delete the cookie rather than
    by formatting the header by hand, so it cannot drift from
    `_set_refresh_cookie`.
    """
    probe = Response()
    _clear_refresh_cookie(probe)
    return {"set-cookie": probe.headers["set-cookie"]}


def _as_response(session: AuthenticatedSession) -> SessionResponse:
    return SessionResponse(
        access_token=session.access_token,
        expires_in=session.expires_in_seconds,
        user=UserSummary(
            id=session.user_id,
            full_name=session.full_name,
            email=session.email,
        ),
        memberships=[
            MembershipSummary(
                tenant_id=membership.tenant_id,
                tenant_name=membership.tenant_name,
                role=membership.role,
            )
            for membership in session.memberships
        ],
        active_tenant_id=session.active_tenant_id,
    )


def _invalid_refresh() -> ApiError:
    """One answer for every refresh failure.

    Unknown, expired, revoked and replayed all come back identically. The
    client's move is the same in all four cases - sign in again - and telling
    them apart would tell an attacker holding a stolen token whether it was
    ever valid.

    The dead cookie is cleared on the way out. Left in place, the browser
    would keep presenting it on every refresh for the rest of its 30-day
    `Max-Age`, turning one expired session into a stream of 401s.
    """
    return ApiError(
        status_code=status.HTTP_401_UNAUTHORIZED,
        error="invalid_refresh",
        message="The session has ended. Please sign in again.",
        headers=_clear_refresh_cookie_header(),
    )


@router.post(
    "/register",
    response_model=RegisterResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Register an owner and open their trial tenant",
    responses=error_responses(
        (400, "`validation_error`"),
        (409, "`email_taken` or `phone_in_active_trial`"),
        (503, "`registration_unavailable` - the trial plan is misconfigured"),
    ),
)
async def register(
    payload: RegisterRequest,
    session: SessionDep,
    email_provider: EmailProviderDep,
) -> RegisterResponse:
    """Create the account, its trial tenant and the owner membership."""
    service = AuthService(session, email_provider=email_provider)
    try:
        result = await service.register(
            full_name=payload.full_name,
            email=payload.email,
            password=payload.password,
            business_name=payload.business_name,
            phone_e164=payload.phone_e164,
        )
    except EmailAlreadyRegisteredError as exc:
        raise ApiError(
            status_code=status.HTTP_409_CONFLICT,
            error="email_taken",
            message="An account already exists for this email address.",
            fields={"email": "already registered"},
        ) from exc
    except PhoneInActiveTrialError as exc:
        raise ApiError(
            status_code=status.HTTP_409_CONFLICT,
            error="phone_in_active_trial",
            message="This phone number already has a trial account in progress.",
            fields={"phone_e164": "already used by a live trial"},
        ) from exc
    except TrialPlanUnavailableError as exc:
        # A misconfigured deployment, not a bad request: answering 4xx here
        # would send the customer off to fix something on their side.
        raise ApiError(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            error="registration_unavailable",
            message="Registration is temporarily unavailable. Please try again later.",
        ) from exc

    return RegisterResponse(
        user_id=result.user_id,
        tenant_id=result.tenant_id,
        verification_email_sent=result.verification_email_sent,
    )


@router.post(
    "/verify-email",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Consume an email verification token",
    responses=error_responses(
        (400, "`validation_error` or `invalid_or_expired_token`"),
    ),
)
async def verify_email(payload: VerifyEmailRequest, session: SessionDep) -> None:
    """Mark the address verified. The token is single use."""
    service = AuthService(session, email_provider=None)
    try:
        service.verify_email(payload.token)
    except InvalidEmailTokenError as exc:
        raise ApiError(
            status_code=status.HTTP_400_BAD_REQUEST,
            error="invalid_or_expired_token",
            message="This verification link is invalid, expired or already used.",
        ) from exc


@router.post(
    "/resend-verification",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Send a fresh verification link",
    responses=error_responses((400, "`validation_error`")),
)
async def resend_verification(
    payload: ResendVerificationRequest,
    session: SessionDep,
    email_provider: EmailProviderDep,
) -> None:
    """Always answers 204.

    Whether the address is registered, already verified, or unknown is not
    reflected in the response: a different answer per case would let anyone
    enumerate which businesses have accounts.
    """
    service = AuthService(session, email_provider=email_provider)
    await service.resend_verification(email=payload.email)


@router.post(
    "/login",
    response_model=SessionResponse,
    status_code=status.HTTP_200_OK,
    summary="Sign in and open a session",
    responses=error_responses(
        (400, "`validation_error`"),
        (401, "`invalid_credentials`"),
        (403, "`email_not_verified` or `tenant_access_denied`"),
        (429, "`rate_limited` - more than 5 requests per minute from this IP"),
    ),
)
async def login(
    payload: LoginRequest,
    session: SessionDep,
    response: Response,
) -> SessionResponse:
    """Exchange a password for an access token and a refresh cookie.

    The response lists every business the caller belongs to, so the panel can
    offer a choice; the access token is scoped to whichever one is active.
    """
    service = SessionService(session)
    try:
        result = service.login(
            email=payload.email,
            password=payload.password,
            tenant_id=payload.tenant_id,
        )
    except InvalidCredentialsError as exc:
        # One answer for "no such address" and "wrong password": two would
        # turn this endpoint into a way to discover who has an account.
        raise ApiError(
            status_code=status.HTTP_401_UNAUTHORIZED,
            error="invalid_credentials",
            message="Email or password is incorrect.",
        ) from exc
    except EmailNotVerifiedError as exc:
        raise ApiError(
            status_code=status.HTTP_403_FORBIDDEN,
            error="email_not_verified",
            message="Verify your email address before signing in.",
        ) from exc
    except TenantNotAccessibleError as exc:
        raise ApiError(
            status_code=status.HTTP_403_FORBIDDEN,
            error="tenant_access_denied",
            message="You are not an active member of this tenant.",
        ) from exc

    _set_refresh_cookie(response, result)
    return _as_response(result)


@router.post(
    "/refresh",
    response_model=SessionResponse,
    status_code=status.HTTP_200_OK,
    summary="Rotate the refresh token and mint a new access token",
    responses=error_responses(
        (400, "`validation_error`"),
        (401, "`invalid_refresh` - unknown, expired, revoked or replayed"),
        (403, "`email_not_verified` or `tenant_access_denied`"),
        (429, "`rate_limited` - more than 5 requests per minute from this IP"),
    ),
)
async def refresh(
    session: SessionDep,
    response: Response,
    refresh_token: RefreshCookieDep = None,
    payload: RefreshRequest | None = None,
) -> SessionResponse:
    """Trade the refresh cookie for a fresh pair.

    The presented token is revoked and its replacement issued in the same
    transaction. Presenting one that was already revoked is treated as a
    stolen credential: the entire session chain is revoked and the answer is
    the same 401 as any other failure.
    """
    service = SessionService(session)
    try:
        result = service.refresh(
            refresh_token or "",
            tenant_id=payload.tenant_id if payload else None,
        )
    except InvalidRefreshTokenError as exc:
        # Covers reuse as well: `RefreshTokenReuseError` is a subclass, and
        # the family has already been revoked by the time it is raised. The
        # error carries the cookie-clearing header itself.
        raise _invalid_refresh() from exc
    except EmailNotVerifiedError as exc:
        raise ApiError(
            status_code=status.HTTP_403_FORBIDDEN,
            error="email_not_verified",
            message="Verify your email address before using this session.",
        ) from exc
    except TenantNotAccessibleError as exc:
        raise ApiError(
            status_code=status.HTTP_403_FORBIDDEN,
            error="tenant_access_denied",
            message="You are not an active member of this tenant.",
        ) from exc

    _set_refresh_cookie(response, result)
    return _as_response(result)


@router.post(
    "/logout",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="End the current session",
    responses=error_responses(
        (429, "`rate_limited` - more than 5 requests per minute from this IP"),
    ),
)
async def logout(
    session: SessionDep,
    response: Response,
    refresh_token: RefreshCookieDep = None,
) -> None:
    """Revoke the presented refresh token and clear the cookie.

    Always answers 204. Signing out of a session that has already expired,
    already been revoked, or was never there is not an error the client can
    act on - and a client left holding a cookie because the server objected
    to clearing it would be strictly worse off. Only this token is revoked,
    not the family: other devices stay signed in.
    """
    SessionService(session).logout(refresh_token)
    _clear_refresh_cookie(response)
