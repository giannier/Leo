"""Sign in, stay signed in, sign out.

Two credentials with opposite trade-offs, which is the whole design:

* The **access token** is a stateless JWT that lives 15 minutes. It is fast -
  no database read to authenticate - and it cannot be recalled, so it is
  never allowed to live long.
* The **refresh token** is 256 bits of `secrets` entropy, stored only as a
  SHA-256 digest, valid 30 days, and **rotated on every single use**. It is
  slow (a database read and two writes) and fully revocable, which is what
  makes the short access lifetime survivable.

Rotation is not just hygiene. A refresh token is a bearer credential: whoever
holds it *is* the session, and a copy is indistinguishable from the original.
Rotation converts that undetectable situation into a detectable one. After a
theft there are two parties holding the same token; the first to refresh gets
a fresh one and revokes the presented row, and when the second one turns up
with that same now-revoked row, the duplicate use is visible.

At that point there is no way to tell the victim from the thief - both
present a legitimate token, both look identical. So the whole family is
revoked and both are sent back through the password, which only the real
owner has. That is the cost of the design: a genuine race (two browser tabs
refreshing at once) can log a user out. It is the right trade, and it is why
the window matters - rotation must be atomic, so a crash between "revoke old"
and "issue new" cannot strand a session. Both writes and the commit happen
here, in one transaction.

Everything the caller can be told apart from *these credentials are wrong* is
withheld on purpose: `login` answers the same way for an unknown address and
a wrong password, so the endpoint cannot be used to find out who has an
account.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import NoReturn

from sqlalchemy.orm import Session

from app.core.config import Settings, get_settings
from app.core.security import generate_token, hash_password, hash_token, verify_password
from app.core.tokens import issue_access_token
from app.models.user import User
from app.repositories.tenant import (
    TenantMembership,
    UserMembershipRepository,
)
from app.repositories.user import RefreshTokenRepository, UserRepository

logger = logging.getLogger(__name__)

# Argon2 hash of a value no password can be, verified against when the
# address is unknown. Without it, "no such user" returns in microseconds
# while "wrong password" takes the ~50ms Argon2 deliberately costs, and that
# difference is measurable over the network - a working account-enumeration
# oracle in an endpoint that goes to lengths to avoid being one.
_ABSENT_USER_PASSWORD_HASH = hash_password(f"absent-user-{uuid.uuid4()}")


class AuthenticationError(Exception):
    """Base class for every reason a sign-in attempt is refused."""


class InvalidCredentialsError(AuthenticationError):
    """Unknown address or wrong password. Never says which."""


class EmailNotVerifiedError(AuthenticationError):
    """The account exists but has not proved it owns the address.

    Clarification 005: email verification is what gates entry to the panel.
    """


class TenantNotAccessibleError(AuthenticationError):
    """A tenant was asked for that this user is not an active member of."""


class InvalidRefreshTokenError(Exception):
    """The presented refresh token is unknown or expired."""


class RefreshTokenReuseError(InvalidRefreshTokenError):
    """An already-revoked refresh token was presented again.

    A subclass because the client is told the same thing either way - the
    session is over, sign in again. The distinction is for us: this one means
    the family was just revoked because a token was probably stolen, and it
    is worth a log line at warning level.
    """


@dataclass(frozen=True)
class AuthenticatedSession:
    """Everything the endpoint needs to answer a login or a refresh.

    Captured as plain values before the commit: after it the ORM attributes
    are expired, and reading them would emit fresh SELECTs for data already
    in hand.
    """

    access_token: str
    access_expires_at: datetime
    expires_in_seconds: int

    refresh_token: str
    refresh_expires_at: datetime

    user_id: uuid.UUID
    full_name: str
    email: str

    memberships: tuple[TenantMembership, ...]
    active_tenant_id: uuid.UUID | None


class SessionService:
    """Login, refresh and logout for one request. Owns its transaction."""

    def __init__(self, session: Session, *, settings: Settings | None = None) -> None:
        self._session = session
        self._settings = settings or get_settings()

    # --------------------------------------------------------------- login

    def login(
        self,
        *,
        email: str,
        password: str,
        tenant_id: uuid.UUID | None = None,
    ) -> AuthenticatedSession:
        """Verify a password and open a new session chain."""
        now = datetime.now(UTC)
        user = UserRepository(self._session).get_by_email(email)

        if user is None:
            # Spend the same time as a real verification would, then fail.
            verify_password(password, _ABSENT_USER_PASSWORD_HASH)
            raise InvalidCredentialsError("no account for this address")
        if not verify_password(password, user.password_hash):
            raise InvalidCredentialsError("password does not match")
        if user.email_verified_at is None:
            raise EmailNotVerifiedError(str(user.id))

        # A fresh family: this sign-in is a new chain, unrelated to whatever
        # sessions the user already has on other devices.
        return self._open_session(
            user_id=user.id,
            full_name=user.full_name,
            email=user.email,
            family_id=uuid.uuid4(),
            requested_tenant_id=tenant_id,
            now=now,
        )

    # ------------------------------------------------------------- refresh

    def refresh(
        self,
        refresh_token: str,
        *,
        tenant_id: uuid.UUID | None = None,
    ) -> AuthenticatedSession:
        """Rotate a refresh token: revoke the presented one, issue its heir.

        Both writes and the commit happen in one transaction, so there is no
        instant in which the old token is dead and the new one does not yet
        exist.
        """
        now = datetime.now(UTC)
        tokens = RefreshTokenRepository(self._session)

        # Looked up by digest without filtering on state: a revoked row has
        # to come back, because seeing it is the point.
        record = tokens.find_by_hash(hash_token(refresh_token))
        if record is None:
            raise InvalidRefreshTokenError("unknown refresh token")

        if record.revoked_at is not None:
            self._handle_reuse(family_id=record.family_id, user_id=record.user_id, now=now)

        if record.expires_at <= now:
            raise InvalidRefreshTokenError("refresh token has expired")

        user = UserRepository(self._session).get(record.user_id)
        if user is None:  # pragma: no cover - foreign key makes this unreachable
            raise InvalidRefreshTokenError("token points at a user that no longer exists")
        if user.email_verified_at is None:
            # Unreachable through `login`, which refuses first; reachable if
            # verification is ever revoked. Refusing here keeps the rule in
            # one place: no unverified account holds a live session.
            raise EmailNotVerifiedError(str(user.id))

        tokens.revoke(record, at=now)
        return self._open_session(
            user_id=user.id,
            full_name=user.full_name,
            email=user.email,
            family_id=record.family_id,
            requested_tenant_id=tenant_id,
            now=now,
        )

    def _handle_reuse(self, *, family_id: uuid.UUID, user_id: uuid.UUID, now: datetime) -> NoReturn:
        """Kill the chain, persist that, and refuse.

        Committed before raising on purpose. The endpoint turns this into a
        401, the exception travels back out through `get_db`, and that
        dependency's safety-net rollback would otherwise undo the revocation -
        leaving the stolen family alive after the theft was detected.
        """
        revoked = RefreshTokenRepository(self._session).revoke_family(family_id, at=now)
        self._session.commit()
        logger.warning(
            "refresh token reuse detected; session family revoked",
            extra={
                "user_id": str(user_id),
                "family_id": str(family_id),
                "revoked_tokens": revoked,
            },
        )
        raise RefreshTokenReuseError(str(family_id))

    # -------------------------------------------------------------- logout

    def logout(self, refresh_token: str | None) -> bool:
        """Revoke the presented token. Returns whether anything was revoked.

        Never fails. Signing out has to work from a session that is already
        expired, already revoked, or was never there - a client that cannot
        clear its own cookie because the server objected is worse than a
        no-op. Only the presented link dies, not the family: this is one
        device signing out, not a security event.
        """
        if not refresh_token:
            return False

        now = datetime.now(UTC)
        tokens = RefreshTokenRepository(self._session)
        record = tokens.find_by_hash(hash_token(refresh_token))
        if record is None or record.revoked_at is not None:
            return False

        tokens.revoke(record, at=now)
        self._session.commit()
        return True

    # ------------------------------------------------------------ internals

    def open_for_user(
        self, user: User, *, tenant_id: uuid.UUID | None = None
    ) -> AuthenticatedSession:
        """Sign in a user the caller has already authenticated another way.

        Used by invitation acceptance, where the token in the email is the
        credential and there is no password to check - a brand-new invitee may
        have set one only moments earlier, and sending them to a login form
        after they clicked "accept" is a step with no security value.

        Deliberately does not check `email_verified_at`: acceptance verifies
        the address as a side effect, and re-checking here would reject the
        very account it just created.
        """
        return self._open_session(
            user_id=user.id,
            full_name=user.full_name,
            email=user.email,
            family_id=uuid.uuid4(),
            requested_tenant_id=tenant_id,
            now=datetime.now(UTC),
        )

    def _open_session(
        self,
        *,
        user_id: uuid.UUID,
        full_name: str,
        email: str,
        family_id: uuid.UUID,
        requested_tenant_id: uuid.UUID | None,
        now: datetime,
    ) -> AuthenticatedSession:
        """Mint both credentials, persist the refresh link, commit."""
        memberships = tuple(UserMembershipRepository(self._session, user_id).active_memberships())
        active_tenant_id = self._resolve_active_tenant(memberships, requested_tenant_id)

        access_token, access_expires_at = issue_access_token(
            user_id=user_id,
            tenant_id=active_tenant_id,
            now=now,
            settings=self._settings,
        )
        refresh_plaintext = generate_token()
        refresh_expires_at = now + timedelta(days=self._settings.refresh_token_expire_days)
        RefreshTokenRepository(self._session).issue(
            user_id=user_id,
            family_id=family_id,
            token_hash=hash_token(refresh_plaintext),
            expires_at=refresh_expires_at,
        )
        self._session.commit()

        return AuthenticatedSession(
            access_token=access_token,
            access_expires_at=access_expires_at,
            expires_in_seconds=self._settings.access_token_expire_minutes * 60,
            refresh_token=refresh_plaintext,
            refresh_expires_at=refresh_expires_at,
            user_id=user_id,
            full_name=full_name,
            email=email,
            memberships=memberships,
            active_tenant_id=active_tenant_id,
        )

    @staticmethod
    def _resolve_active_tenant(
        memberships: tuple[TenantMembership, ...],
        requested: uuid.UUID | None,
    ) -> uuid.UUID | None:
        """Decide which tenant the access token is scoped to.

        An explicit choice is honoured only if the caller is an active member
        of it - the request names a tenant, it does not grant one. With no
        explicit choice the first membership wins, and "first" is defined by
        `UserMembershipRepository` as oldest tenant, so a client that omits
        the field gets the same tenant on every refresh instead of drifting
        between them.

        `None` is a legitimate answer: a platform admin belongs to no tenant.
        The resulting token authenticates but reaches no `/tenant/*` endpoint.
        """
        if requested is None:
            return memberships[0].tenant_id if memberships else None
        for membership in memberships:
            if membership.tenant_id == requested:
                return membership.tenant_id
        raise TenantNotAccessibleError(str(requested))
