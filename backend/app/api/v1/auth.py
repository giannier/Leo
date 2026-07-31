"""Authentication endpoints: registration and email verification.

Implements the `/auth` section of
`leo-sdd/features/001-onboarding/contracts/api.md` that TASK-006 covers.
Login, refresh, logout and phone OTP arrive with TASK-007 and TASK-008.

Request and response models live here rather than in a `schemas/` package,
matching `health.py` and the repository layout the project documents.
"""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, status
from pydantic import BaseModel, Field, StringConstraints
from sqlalchemy.orm import Session

from app.api.deps import get_db, get_email_provider
from app.api.errors import ApiError, error_responses
from app.providers.email.base import EmailProvider
from app.services.auth import (
    AuthService,
    EmailAlreadyRegisteredError,
    InvalidEmailTokenError,
    PhoneInActiveTrialError,
    TrialPlanUnavailableError,
)

router = APIRouter(prefix="/auth", tags=["auth"])

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


SessionDep = Annotated[Session, Depends(get_db)]
EmailProviderDep = Annotated[EmailProvider | None, Depends(get_email_provider)]


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
