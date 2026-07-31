"""Access to users and their email tokens.

These deliberately do **not** extend `BaseRepository`. Users are global: one
person joins several tenants through `tenant_users`, so `users`,
`email_tokens`, `otp_codes` and `refresh_tokens` carry no `tenant_id`.
`BaseRepository` would reject them at class-definition time, and that is the
correct behaviour - the tenant filter exists for *business* data, and forcing
a fake tenant onto an identity table would weaken the rule rather than apply
it.

Nothing here reads business data. The moment a query in this module needs to
reach a tenant-scoped table, it belongs in a `BaseRepository` instead.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from datetime import datetime
from typing import Any, cast

from sqlalchemy import CursorResult, select, update
from sqlalchemy.orm import Session

from app.models.user import EmailToken, OtpCode, RefreshToken, User

# The only value `otp_codes.purpose` accepts; the CHECK constraint enforces it.
PHONE_VERIFY_PURPOSE = "phone_verify"

VERIFY_EMAIL = "verify_email"


class UserRepository:
    """Identity records, keyed globally by id or email address."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def get(self, user_id: uuid.UUID) -> User | None:
        return self._session.get(User, user_id)

    def get_by_email(self, email: str) -> User | None:
        """Look up by address.

        `users.email` is CITEXT, so the comparison is case-insensitive in the
        database. Normalising in Python would not survive a concurrent
        insert; the unique index has to be the thing enforcing it.
        """
        statement = select(User).where(User.email == email)
        return self._session.execute(statement).scalar_one_or_none()

    def create(
        self,
        *,
        email: str,
        password_hash: str,
        full_name: str,
        phone_e164: str | None = None,
    ) -> User:
        """Insert an unverified user and flush.

        Flushing here is what makes the duplicate-address case surface as an
        `IntegrityError` from the unique index, at a point where the caller
        still knows which insert it was. Checking for the address first and
        then inserting would race: two simultaneous registrations would both
        see "available" and one would fail later, further from its cause.
        """
        user = User(
            email=email,
            password_hash=password_hash,
            full_name=full_name,
            phone_e164=phone_e164,
        )
        self._session.add(user)
        self._session.flush()
        return user

    def mark_email_verified(self, user: User, *, at: datetime) -> None:
        user.email_verified_at = at
        self._session.flush()

    def mark_phone_verified(self, user: User, *, at: datetime) -> None:
        """Record that the contact phone passed OTP verification.

        Separate from email verification because they gate different things:
        a verified email opens the panel and the sandbox, a verified phone is
        what a connection request requires (Clarification 005).
        """
        user.phone_verified_at = at
        self._session.flush()


class EmailTokenRepository:
    """Single-use email tokens. Only digests are ever stored or compared."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def issue(
        self,
        *,
        user_id: uuid.UUID,
        purpose: str,
        token_hash: str,
        expires_at: datetime,
    ) -> EmailToken:
        token = EmailToken(
            user_id=user_id,
            purpose=purpose,
            token_hash=token_hash,
            expires_at=expires_at,
        )
        self._session.add(token)
        self._session.flush()
        return token

    def find_usable(self, *, token_hash: str, purpose: str, now: datetime) -> EmailToken | None:
        """The one unconsumed, unexpired token with this digest, if any.

        Expiry and consumption are part of the lookup rather than checked
        afterwards, so there is no path where a caller forgets one of them.
        """
        statement = select(EmailToken).where(
            EmailToken.token_hash == token_hash,
            EmailToken.purpose == purpose,
            EmailToken.consumed_at.is_(None),
            EmailToken.expires_at > now,
        )
        return self._session.execute(statement).scalar_one_or_none()

    def consume(self, token: EmailToken, *, at: datetime) -> None:
        token.consumed_at = at
        self._session.flush()

    def supersede_active(self, *, user_id: uuid.UUID, purpose: str, at: datetime) -> int:
        """Retire every still-usable token of this purpose. Returns how many.

        Issuing a replacement has to invalidate the old link, otherwise a
        resend leaves two live tokens and the first email - possibly the one
        that went to the wrong address, which is why the user asked for a
        resend - keeps working.

        `consumed_at` doubles as "no longer usable" here. The data model has
        no separate `superseded_at`, and adding a column to record a
        distinction nothing acts on would be schema churn.
        """
        statement = (
            update(EmailToken)
            .where(
                EmailToken.user_id == user_id,
                EmailToken.purpose == purpose,
                EmailToken.consumed_at.is_(None),
            )
            .values(consumed_at=at)
        )
        # `Session.execute` is annotated as returning `Result`, which has no
        # `rowcount`; a DML statement always produces a `CursorResult`, which
        # does. Same shape of gap as the cast in `BaseRepository`.
        result = cast("CursorResult[Any]", self._session.execute(statement))
        self._session.flush()
        return result.rowcount


class OtpCodeRepository:
    """Phone verification codes. Only digests are stored or compared.

    Unlike email tokens, an OTP is guessable by design - six digits is about
    twenty bits. The attempt counter here is therefore a security control, not
    bookkeeping, and it has to be incremented in a way two concurrent guesses
    cannot both skip.
    """

    def __init__(self, session: Session) -> None:
        self._session = session

    def issue(
        self,
        *,
        user_id: uuid.UUID,
        code_hash: str,
        expires_at: datetime,
        purpose: str = PHONE_VERIFY_PURPOSE,
    ) -> OtpCode:
        code = OtpCode(
            user_id=user_id,
            purpose=purpose,
            code_hash=code_hash,
            expires_at=expires_at,
        )
        self._session.add(code)
        self._session.flush()
        return code

    def find_live(
        self, *, user_id: uuid.UUID, now: datetime, purpose: str = PHONE_VERIFY_PURPOSE
    ) -> OtpCode | None:
        """The newest unconsumed, unexpired code for this user, locked.

        `with_for_update` is what makes the attempt counter trustworthy: two
        guesses arriving together would otherwise both read `attempts = 4`,
        both write 5, and between them get ten tries out of a cap of five.
        The second transaction now waits for the first to commit.

        The digest is deliberately not part of the lookup. A wrong code must
        still find the row, because failing to find it is how an attempt would
        go uncounted - which is exactly what an attacker wants.
        """
        statement = (
            select(OtpCode)
            .where(
                OtpCode.user_id == user_id,
                OtpCode.purpose == purpose,
                OtpCode.consumed_at.is_(None),
                OtpCode.expires_at > now,
            )
            .order_by(OtpCode.created_at.desc())
            .limit(1)
            .with_for_update()
        )
        return self._session.execute(statement).scalar_one_or_none()

    def register_attempt(self, code: OtpCode) -> int:
        """Count one failed guess. Returns the new total."""
        code.attempts += 1
        self._session.flush()
        return code.attempts

    def consume(self, code: OtpCode, *, at: datetime) -> None:
        code.consumed_at = at
        self._session.flush()

    def supersede_active(
        self, *, user_id: uuid.UUID, at: datetime, purpose: str = PHONE_VERIFY_PURPOSE
    ) -> int:
        """Retire every live code for this user. Returns how many.

        Requesting a new code must kill the previous one. Leaving both alive
        would let an attacker widen the guessing window simply by asking for
        more codes, turning the attempt cap into five tries *per code* instead
        of five tries per verification.
        """
        statement = (
            update(OtpCode)
            .where(
                OtpCode.user_id == user_id,
                OtpCode.purpose == purpose,
                OtpCode.consumed_at.is_(None),
            )
            .values(consumed_at=at)
        )
        result = cast("CursorResult[Any]", self._session.execute(statement))
        self._session.flush()
        return result.rowcount


class RefreshTokenRepository:
    """Rotating session tokens. Only digests are ever stored or compared."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def issue(
        self,
        *,
        user_id: uuid.UUID,
        family_id: uuid.UUID,
        token_hash: str,
        expires_at: datetime,
    ) -> RefreshToken:
        token = RefreshToken(
            user_id=user_id,
            family_id=family_id,
            token_hash=token_hash,
            expires_at=expires_at,
        )
        self._session.add(token)
        self._session.flush()
        return token

    def find_by_hash(self, token_hash: str) -> RefreshToken | None:
        """The row with this digest, *whatever its state*.

        Deliberately does not filter out revoked or expired rows, unlike
        `EmailTokenRepository.find_usable`. Reuse detection is the whole point
        of rotation, and a query that hid revoked rows would report a replayed
        token as merely unknown - which is exactly the case that has to be
        treated as a theft.
        """
        statement = select(RefreshToken).where(RefreshToken.token_hash == token_hash)
        return self._session.execute(statement).scalar_one_or_none()

    def revoke(self, token: RefreshToken, *, at: datetime) -> None:
        """Retire one link. Idempotent: an earlier revocation is kept."""
        if token.revoked_at is None:
            token.revoked_at = at
        self._session.flush()

    def revoke_family(self, family_id: uuid.UUID, *, at: datetime) -> int:
        """Retire every live link of one session chain. Returns how many.

        A bulk `UPDATE`, not a loop over ORM objects: this runs while
        answering a request that has already been identified as a probable
        token theft, and the number of rows is unbounded (one per rotation
        the chain has been through).
        """
        statement = (
            update(RefreshToken)
            .where(
                RefreshToken.family_id == family_id,
                RefreshToken.revoked_at.is_(None),
            )
            .values(revoked_at=at)
        )
        result = cast("CursorResult[Any]", self._session.execute(statement))
        self._session.flush()
        return result.rowcount

    def live_tokens_for(self, user_id: uuid.UUID, *, now: datetime) -> Sequence[RefreshToken]:
        """Every still-usable token of one user. Used by tests and TASK-009."""
        statement = select(RefreshToken).where(
            RefreshToken.user_id == user_id,
            RefreshToken.revoked_at.is_(None),
            RefreshToken.expires_at > now,
        )
        return self._session.execute(statement).scalars().all()
