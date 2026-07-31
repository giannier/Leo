"""Phone verification by one-time code (Clarification 005).

A six-digit code is roughly twenty bits, so unlike the email tokens this
credential *is* guessable. Three controls contain that, and all three are
load-bearing rather than decorative:

* **Ten-minute expiry** bounds how long any single code is worth attacking.
* **Five attempts**, counted under a row lock so concurrent guesses cannot
  share a slot.
* **One live code per user** - requesting a new one retires the old, so an
  attacker cannot widen the window by asking for more codes and turning
  "five attempts" into five attempts each.

Verification succeeds only if the phone is not already holding another live
trial, which is the anti-abuse rule the whole flow exists for.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.security import generate_otp_code, hash_token, tokens_match
from app.models.user import User
from app.providers.otp.base import OtpSender
from app.repositories.tenant import (
    OWNER_ROLE,
    TrialPhoneLockRepository,
    UserMembershipRepository,
)
from app.repositories.user import OtpCodeRepository, UserRepository

MAX_ATTEMPTS = 5
CODE_TTL_MINUTES = 10


class OtpError(RuntimeError):
    """Base for every OTP failure the API turns into a response."""


class NoPhoneOnRecord(OtpError):
    """The user never supplied a contact phone, so there is nothing to verify."""


class OtpDeliveryFailed(OtpError):
    """The code was generated but could not be delivered."""


class InvalidOrExpiredOtp(OtpError):
    """Wrong code, expired code, no code, or the attempt cap is reached.

    Deliberately one error for all of them. Telling a caller *which* one it
    was hands them a free oracle: "expired" confirms the phone is registered,
    and "too many attempts" confirms they were close enough to be counted.
    The contract specifies a single `invalid_or_expired_otp`.
    """


class PhoneAlreadyInActiveTrial(OtpError):
    """Another live trial already holds this phone (Clarification 005)."""


@dataclass(frozen=True)
class OtpRequestOutcome:
    expires_at: datetime


class OtpService:
    """Issues and checks phone verification codes."""

    def __init__(
        self,
        session: Session,
        *,
        sender: OtpSender,
        ttl_minutes: int = CODE_TTL_MINUTES,
        max_attempts: int = MAX_ATTEMPTS,
    ) -> None:
        self._session = session
        self._sender = sender
        self._ttl = timedelta(minutes=ttl_minutes)
        self._max_attempts = max_attempts
        self._users = UserRepository(session)
        self._codes = OtpCodeRepository(session)

    async def request(self, user: User) -> OtpRequestOutcome:
        """Issue a fresh code and deliver it, retiring any previous one."""
        if not user.phone_e164:
            raise NoPhoneOnRecord("This account has no contact phone to verify. Add one first.")

        now = datetime.now(UTC)
        self._codes.supersede_active(user_id=user.id, at=now)

        code = generate_otp_code()
        expires_at = now + self._ttl
        self._codes.issue(user_id=user.id, code_hash=hash_token(code), expires_at=expires_at)

        # Commit before delivering. The reverse order can send a customer a
        # code that a rollback then erased, which is unverifiable and looks to
        # them like the system is broken.
        self._session.commit()

        try:
            await self._sender.send(phone_e164=user.phone_e164, code=code)
        except Exception as exc:
            raise OtpDeliveryFailed("Could not deliver the verification code.") from exc

        return OtpRequestOutcome(expires_at=expires_at)

    def verify(self, user: User, code: str) -> None:
        """Consume a code and mark the phone verified.

        Synchronous: nothing here leaves the process, and keeping it inside
        one transaction is what makes the attempt count and the phone lock
        agree with each other.
        """
        now = datetime.now(UTC)
        record = self._codes.find_live(user_id=user.id, now=now)
        if record is None:
            raise InvalidOrExpiredOtp("That code is not valid.")

        # Checked before comparing, so a caller who has already exhausted
        # their attempts gains nothing by continuing to guess.
        if record.attempts >= self._max_attempts:
            raise InvalidOrExpiredOtp("That code is not valid.")

        if not tokens_match(code, record.code_hash):
            attempts = self._codes.register_attempt(record)
            if attempts >= self._max_attempts:
                # Burn the code outright. Leaving it live would let the next
                # request start a fresh count against the same secret.
                self._codes.consume(record, at=now)
            self._session.commit()
            raise InvalidOrExpiredOtp("That code is not valid.")

        self._codes.consume(record, at=now)
        self._claim_phone(user)
        self._users.mark_phone_verified(user, at=now)
        self._session.commit()

    def _claim_phone(self, user: User) -> None:
        """Take the one-live-trial-per-phone lock for the tenant this user owns.

        Registration already claims the lock when a phone is given at signup,
        so this covers the account that added its phone afterwards. Claiming
        for the owned tenant only: a user invited into somebody else's
        business must not consume that business's phone slot.

        Inserts and lets the primary key reject a duplicate, rather than
        asking whether the phone is free and then inserting. The two-step
        version races - two verifications a millisecond apart both read free,
        which is precisely the abuse Clarification 005 exists to stop.
        """
        phone = user.phone_e164
        if phone is None:  # pragma: no cover - guarded in `request`
            return

        memberships = UserMembershipRepository(self._session, user.id).active_memberships()
        owned = [m.tenant_id for m in memberships if m.role == OWNER_ROLE]
        if not owned:
            return

        tenant_id = owned[0]
        locks = TrialPhoneLockRepository(self._session, tenant_id)
        if phone in locks.held_phones():
            return

        try:
            # A savepoint, so a rejected claim does not poison the outer
            # transaction that still has the consumed code in it.
            with self._session.begin_nested():
                locks.claim(phone)
        except IntegrityError as exc:
            raise PhoneAlreadyInActiveTrial(
                "This phone number is already verified on another trial account."
            ) from exc
