"""Registration and email verification.

Three flows, one invariant: an account and the trial tenant that belongs to
it are created together or not at all.

Two design points worth stating, because both look like mistakes until you
know why:

**Email uniqueness is enforced by the database, never by a prior read.**
Checking "is this email free?" and then inserting is two statements with a
gap in between; two registrations landing in that gap both see "free". So
the service inserts and translates the resulting `IntegrityError`. The unique
index on `users.email` is the actual rule; this module only names the
failure. The contact-phone rule cannot be an index - its condition spans
three tables - so `TrialPhoneRepository` closes the same gap with an
advisory lock instead.

**Email is sent after the commit, never inside it.** Sending first would
mean a rolled-back transaction still produced a verification link for an
account that does not exist. Committing first means the worst case is an
account with no email yet - recoverable with `resend-verification`, and
reported to the caller as `verification_email_sent: false`.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from urllib.parse import quote

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.config import Settings, get_settings
from app.core.security import generate_token, hash_password, hash_token
from app.providers.email.base import EmailError, EmailProvider
from app.repositories.plan import TRIAL_PLAN_ID, PlanRepository
from app.repositories.tenant import (
    OWNER_ROLE,
    TenantRepository,
    TenantUserRepository,
    TrialPhoneRepository,
)
from app.repositories.user import VERIFY_EMAIL, EmailTokenRepository, UserRepository
from app.services.emails import build_verification_email
from app.services.trial import billing_enforced

logger = logging.getLogger(__name__)

# Path on the tenant panel that consumes the token. The frontend route added
# in TASK-020 has to match this constant.
VERIFICATION_PATH = "/verify-email"


class RegistrationError(Exception):
    """Base class for everything that can stop a registration."""


class EmailAlreadyRegisteredError(RegistrationError):
    """The address already has an account (`409 email_taken`)."""


class PhoneInActiveTrialError(RegistrationError):
    """Another tenant currently in trial has this phone (`409`).

    Carries the phone the caller sent, never anything about the tenant that
    already has it. Which business holds a number is not the registrant's to
    know, and the endpoint's message says only that the number is taken.
    """


class TrialPlanUnavailableError(RegistrationError):
    """The `trial` plan row is missing or has no `trial_days`.

    A deployment problem, not a client problem: the seed in migration 0001
    did not run, or someone edited the row. Distinguished from the other
    errors so the endpoint can answer 503 instead of blaming the caller.
    """


class InvalidEmailTokenError(Exception):
    """The verification token is unknown, expired, or already used."""


@dataclass(frozen=True)
class RegistrationResult:
    """What the endpoint needs to answer, captured before the commit."""

    user_id: uuid.UUID
    tenant_id: uuid.UUID
    verification_email_sent: bool


class AuthService:
    """Registration and email verification for one request.

    Owns its transaction: each public method commits the unit of work it
    completes, or leaves the session rolled back. `email_provider` is
    optional so that a deployment with no credential still registers
    accounts - it reports that the message did not go out rather than
    failing the request or, worse, silently dropping it.
    """

    def __init__(
        self,
        session: Session,
        *,
        email_provider: EmailProvider | None,
        settings: Settings | None = None,
    ) -> None:
        self._session = session
        self._email = email_provider
        self._settings = settings or get_settings()

    # ------------------------------------------------------------ register

    async def register(
        self,
        *,
        full_name: str,
        email: str,
        password: str,
        business_name: str,
        phone_e164: str | None = None,
        country: str = "PE",
    ) -> RegistrationResult:
        """Create the user, the trial tenant and the ownership."""
        now = datetime.now(UTC)
        trial_ends_at = now + timedelta(days=self._trial_days())

        users = UserRepository(self._session)
        try:
            user = users.create(
                email=email,
                password_hash=hash_password(password),
                full_name=full_name,
                phone_e164=phone_e164,
            )
        except IntegrityError as exc:
            # `users` has exactly one unique constraint, on the address.
            self._session.rollback()
            raise EmailAlreadyRegisteredError(email) from exc

        tenant = TenantRepository(self._session).create_trial(
            name=business_name,
            plan_id=TRIAL_PLAN_ID,
            trial_ends_at=trial_ends_at,
            pais=country.upper(),
        )
        TenantUserRepository(self._session, tenant.id).add_member(user_id=user.id, role=OWNER_ROLE)

        if phone_e164 is not None and billing_enforced():
            # Clarification 005, read literally: the number may not sit in two
            # tenants that are in trial *at the same time*. So the question is
            # asked of the tenants that are in trial now, and a business whose
            # trial has ended - paused, archived, or converted to a paid plan -
            # is simply not among them. Nothing has to be released for its
            # owner to register again with the same number.
            phones = TrialPhoneRepository(self._session)
            phones.hold(phone_e164)
            if phones.held_by_another_trial(phone_e164, excluding_tenant_id=tenant.id):
                self._session.rollback()
                raise PhoneInActiveTrialError(phone_e164)

        token = self._issue_verification_token(user.id, now=now)

        # Read before commit: after it, these are expired attributes and
        # touching them would emit another SELECT for values we already have.
        user_id, tenant_id = user.id, tenant.id
        self._session.commit()

        sent = await self._send_verification(
            user_id=user_id, address=email, full_name=full_name, token=token
        )
        return RegistrationResult(
            user_id=user_id, tenant_id=tenant_id, verification_email_sent=sent
        )

    # -------------------------------------------------------- verification

    def verify_email(self, token: str) -> uuid.UUID:
        """Consume a verification token and mark the address verified.

        Returns the user id. Single use: a second call with the same token
        fails, which also means a leaked link stops working the moment the
        legitimate owner uses it.
        """
        now = datetime.now(UTC)
        tokens = EmailTokenRepository(self._session)

        record = tokens.find_usable(token_hash=hash_token(token), purpose=VERIFY_EMAIL, now=now)
        if record is None:
            raise InvalidEmailTokenError("unknown, expired or already used token")

        user = UserRepository(self._session).get(record.user_id)
        if user is None:  # pragma: no cover - foreign key makes this unreachable
            raise InvalidEmailTokenError("token points at a user that no longer exists")

        tokens.consume(record, at=now)
        if user.email_verified_at is None:
            UserRepository(self._session).mark_email_verified(user, at=now)

        user_id = user.id
        self._session.commit()
        return user_id

    async def resend_verification(self, *, email: str) -> bool:
        """Issue a fresh link, retiring any previous one.

        Returns whether an email actually went out. The endpoint answers 204
        either way: telling an anonymous caller whether an address is
        registered turns this into an account-enumeration oracle.
        """
        now = datetime.now(UTC)
        user = UserRepository(self._session).get_by_email(email)
        if user is None or user.email_verified_at is not None:
            return False

        token = self._issue_verification_token(user.id, now=now)
        user_id, full_name, address = user.id, user.full_name, user.email
        self._session.commit()

        return await self._send_verification(
            user_id=user_id, address=address, full_name=full_name, token=token
        )

    # ------------------------------------------------------------ internals

    def _trial_days(self) -> int:
        """Trial length, from the `plans` row - never a literal in code."""
        plan = PlanRepository(self._session).get(TRIAL_PLAN_ID)
        if plan is None:
            raise TrialPlanUnavailableError(f"no {TRIAL_PLAN_ID!r} row in plans")
        if plan.trial_days is None:
            raise TrialPlanUnavailableError(f"plan {TRIAL_PLAN_ID!r} has no trial_days configured")
        return plan.trial_days

    def _issue_verification_token(self, user_id: uuid.UUID, *, now: datetime) -> str:
        """Retire the user's live tokens and mint one replacement."""
        tokens = EmailTokenRepository(self._session)
        tokens.supersede_active(user_id=user_id, purpose=VERIFY_EMAIL, at=now)

        plaintext = generate_token()
        tokens.issue(
            user_id=user_id,
            purpose=VERIFY_EMAIL,
            token_hash=hash_token(plaintext),
            expires_at=now + timedelta(hours=self._token_ttl_hours),
        )
        return plaintext

    @property
    def _token_ttl_hours(self) -> int:
        return self._settings.email_verification_token_ttl_hours

    def verification_url(self, token: str) -> str:
        """The link that goes in the email."""
        base = self._settings.frontend_base_url.rstrip("/")
        return f"{base}{VERIFICATION_PATH}?token={quote(token, safe='')}"

    async def _send_verification(
        self, *, user_id: uuid.UUID, address: str, full_name: str, token: str
    ) -> bool:
        """Hand the message to the provider. Never raises.

        Log lines carry the user id, not the address: the log pipeline is not
        a place for personal data (Ley 29733, and the Constitution's logging
        standard).
        """
        if self._email is None:
            logger.warning(
                "verification email not sent: no email provider is configured",
                extra={"user_id": str(user_id)},
            )
            return False

        message = build_verification_email(
            to=address,
            full_name=full_name,
            verification_url=self.verification_url(token),
            expires_in_hours=self._token_ttl_hours,
        )
        try:
            await self._email.send(message)
        except EmailError:
            logger.exception(
                "verification email could not be delivered to the provider",
                extra={"user_id": str(user_id)},
            )
            return False
        return True
