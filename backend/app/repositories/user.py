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
from datetime import datetime
from typing import Any, cast

from sqlalchemy import CursorResult, select, update
from sqlalchemy.orm import Session

from app.models.user import EmailToken, User

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
