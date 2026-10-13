"""Team membership: invitations, roles and deactivation (TASK-009).

Two rules here are not obvious from the endpoint list, and both are the kind
that only bite in production:

**Seats are counted under a lock.** Read-then-insert lets two accepts a
millisecond apart both see a free seat and both take it, putting a two-seat
tenant on three. `TenantRepository.lock()` takes `SELECT ... FOR UPDATE` on
the tenant row first, so the second transaction reads a count that already
includes the first.

**A pending invitation occupies a seat.** Counting only accepted members
would let an owner send ten invitations into a two-seat plan and discover the
problem when the third person clicked - by which time the promise has already
been made.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy.orm import Session

from app.core.security import generate_token, hash_password, hash_token
from app.models.tenant import Invitation, TenantUser
from app.models.user import User
from app.repositories.plan import PlanRepository
from app.repositories.tenant import (
    ACTIVE_MEMBERSHIP,
    OWNER_ROLE,
    InvitationLookup,
    InvitationRepository,
    TenantRepository,
    TenantUserRepository,
)
from app.repositories.user import RefreshTokenRepository, UserRepository
from app.services.trial import billing_enforced

INVITATION_TTL_DAYS = 7
DISABLED_MEMBERSHIP = "disabled"


class TeamError(RuntimeError):
    """Base for every team failure the API turns into a response."""


class SeatLimitReached(TeamError):
    """The plan has no free seat for another member or invitation."""


class AlreadyMember(TeamError):
    """That address already belongs to this tenant."""


class InvitationNotFound(TeamError):
    """No such invitation for this tenant."""


class InvitationExpired(TeamError):
    """The invitation exists but is past its expiry, revoked or used."""


class MemberNotFound(TeamError):
    """No such member in this tenant."""


class CannotDemoteLastOwner(TeamError):
    """Every tenant must keep at least one owner.

    Without this a tenant can be left with nobody able to delete the account
    or manage billing, and no self-service way back - it becomes a support
    ticket that requires database access to fix.
    """


class PasswordRequired(TeamError):
    """A brand-new user accepting an invitation must set a password."""


@dataclass(frozen=True)
class SeatUsage:
    used: int
    limit: int

    @property
    def free(self) -> int:
        return max(self.limit - self.used, 0)


@dataclass(frozen=True)
class AcceptedInvitation:
    user: User
    tenant_id: uuid.UUID
    role: str
    created_user: bool


class TeamService:
    """Membership and invitations for one tenant."""

    def __init__(self, session: Session, tenant_id: uuid.UUID) -> None:
        self._session = session
        self._tenant_id = tenant_id
        self._tenants = TenantRepository(session)
        self._members = TenantUserRepository(session, tenant_id)
        self._invitations = InvitationRepository(session, tenant_id)
        self._plans = PlanRepository(session)
        self._users = UserRepository(session)

    # ------------------------------------------------------------------ read

    def list_members(self) -> list[TenantUser]:
        return list(self._members.list_members())

    def list_pending_invitations(self) -> list[Invitation]:
        return list(self._invitations.list_pending(now=datetime.now(UTC)))

    def seat_usage(self) -> SeatUsage:
        """Seats taken, counting live invitations, against the plan's ceiling."""
        now = datetime.now(UTC)
        active = self._members.count(TenantUser.status == ACTIVE_MEMBERSHIP)
        pending = self._invitations.pending_count(now=now)
        return SeatUsage(used=active + pending, limit=self._seat_limit())

    def _seat_limit(self) -> int:
        tenant = self._tenants.get(self._tenant_id)
        if tenant is None:  # pragma: no cover - the caller is authenticated
            raise MemberNotFound("Tenant not found.")
        plan = self._plans.get(tenant.plan_id)
        if plan is None:  # pragma: no cover - FK guarantees it
            raise MemberNotFound("Plan not found.")
        # Never hardcoded: Clarification 003 puts the number in `plans` so a
        # tier change is a data change, not a deploy.
        return plan.max_seats

    # ---------------------------------------------------------- invitations

    def invite(self, *, email: str, role: str, invited_by: uuid.UUID) -> tuple[Invitation, str]:
        """Create an invitation. Returns it plus the plaintext token, once.

        The plaintext exists only long enough to be put in the email; the
        database keeps a digest, so a dump yields nothing presentable.
        """
        now = datetime.now(UTC)

        # Lock first, then count. Everything between here and the commit sees
        # a seat count no concurrent accept or invite can change underneath it.
        self._tenants.lock(self._tenant_id)

        existing = self._users.get_by_email(email)
        if existing is not None and self._members.membership_for(existing.id) is not None:
            raise AlreadyMember("That person is already on this team.")

        if self._invitations.find_pending_for_email(email=email, now=now) is not None:
            raise AlreadyMember("That address already has a pending invitation.")

        usage = self.seat_usage()
        if billing_enforced() and usage.free <= 0:
            raise SeatLimitReached(
                f"This plan allows {usage.limit} seats and {usage.used} are taken."
            )

        token = generate_token()
        invitation = self._invitations.issue(
            email=email,
            role=role,
            token_hash=hash_token(token),
            expires_at=now + timedelta(days=INVITATION_TTL_DAYS),
            invited_by=invited_by,
        )
        self._session.commit()
        return invitation, token

    def reissue(self, invitation_id: uuid.UUID) -> tuple[Invitation, str]:
        """Replace a pending invitation's token and extend its window.

        The old token stops working. A resend usually means the first message
        went astray, and leaving both live would mean a link that may have
        reached the wrong inbox still grants access.
        """
        now = datetime.now(UTC)
        invitation = self._invitations.get(invitation_id)
        if invitation is None:
            raise InvitationNotFound("No such invitation.")
        if invitation.accepted_at is not None or invitation.revoked_at is not None:
            raise InvitationExpired("That invitation is no longer pending.")

        token = generate_token()
        invitation.token_hash = hash_token(token)
        invitation.expires_at = now + timedelta(days=INVITATION_TTL_DAYS)
        self._session.commit()
        return invitation, token

    def revoke_invitation(self, invitation_id: uuid.UUID) -> None:
        invitation = self._invitations.get(invitation_id)
        if invitation is None:
            raise InvitationNotFound("No such invitation.")
        self._invitations.revoke(invitation, at=datetime.now(UTC))
        self._session.commit()

    # -------------------------------------------------------------- members

    def update_member(
        self,
        membership_id: uuid.UUID,
        *,
        role: str | None = None,
        status: str | None = None,
    ) -> TenantUser:
        """Change a member's role or status.

        Deactivating revokes their refresh tokens in the same transaction.
        Their access token keeps its signature until it expires, but every
        request re-reads the membership - TASK-007 deliberately kept the role
        out of the token - so it stops working on the very next call.
        """
        membership = self._members.get(membership_id)
        if membership is None:
            raise MemberNotFound("No such member on this team.")

        if role is not None and role != membership.role:
            self._guard_last_owner(membership, new_role=role)
            membership.role = role

        if status is not None and status != membership.status:
            if status == DISABLED_MEMBERSHIP:
                self._guard_last_owner(membership, new_role=None)
            membership.status = status
            if status == DISABLED_MEMBERSHIP:
                self._revoke_sessions(membership.user_id)

        self._session.flush()
        self._session.commit()
        return membership

    def _guard_last_owner(self, membership: TenantUser, *, new_role: str | None) -> None:
        """Refuse to leave the tenant without an active owner."""
        if membership.role != OWNER_ROLE:
            return
        if new_role == OWNER_ROLE:
            return
        remaining = self._members.count(
            TenantUser.role == OWNER_ROLE,
            TenantUser.status == ACTIVE_MEMBERSHIP,
            TenantUser.id != membership.id,
        )
        if remaining == 0:
            raise CannotDemoteLastOwner("This is the only owner. Promote somebody else first.")

    def _revoke_sessions(self, user_id: uuid.UUID) -> None:
        tokens = RefreshTokenRepository(self._session)
        now = datetime.now(UTC)
        for token in tokens.live_tokens_for(user_id, now=now):
            tokens.revoke(token, at=now)


def accept_invitation(
    session: Session,
    *,
    token: str,
    full_name: str | None,
    password: str | None,
) -> AcceptedInvitation:
    """Join a tenant with an invitation token.

    A module-level function, not a `TeamService` method, because at this point
    there is no authenticated caller and therefore no tenant to construct the
    service with - the token is what identifies the tenant.

    Works for someone who already has an account and for someone who does not.
    A brand-new user is created verified: they proved control of the address
    by receiving the invitation, so asking them to confirm it again adds a
    step without adding evidence.
    """
    now = datetime.now(UTC)
    invitation = InvitationLookup(session).by_token_hash(hash_token(token))
    if invitation is None:
        raise InvitationExpired("This invitation link is not valid.")
    if (
        invitation.accepted_at is not None
        or invitation.revoked_at is not None
        or invitation.expires_at <= now
    ):
        raise InvitationExpired("This invitation has expired or already been used.")

    tenants = TenantRepository(session)
    # Same lock as `invite`: the seat that was reserved when the invitation
    # was sent must still be there, and two people accepting together must
    # not both take the last one.
    tenants.lock(invitation.tenant_id)

    users = UserRepository(session)
    members = TenantUserRepository(session, invitation.tenant_id)
    invitations = InvitationRepository(session, invitation.tenant_id)

    user = users.get_by_email(invitation.email)
    created_user = False
    if user is None:
        if not password:
            raise PasswordRequired("Choose a password to finish creating your account.")
        user = users.create(
            email=invitation.email,
            password_hash=hash_password(password),
            full_name=full_name or invitation.email,
        )
        # Verified on creation: receiving the invitation at that address is
        # the proof. Sending a second confirmation would add a step without
        # adding evidence, and would leave them locked out of the team they
        # were just invited to until they found it.
        users.mark_email_verified(user, at=now)
        created_user = True

    if members.membership_for(user.id) is None:
        active = members.count(TenantUser.status == ACTIVE_MEMBERSHIP)
        tenant = tenants.get(invitation.tenant_id)
        assert tenant is not None
        plan = PlanRepository(session).get(tenant.plan_id)
        assert plan is not None
        # The invitation itself no longer counts once it is being accepted,
        # so only active members are compared against the ceiling here.
        if billing_enforced() and active >= plan.max_seats:
            raise SeatLimitReached("This team has no free seats left.")
        members.add_member(user_id=user.id, role=invitation.role)

    invitations.accept(invitation, at=now)
    session.commit()

    return AcceptedInvitation(
        user=user,
        tenant_id=invitation.tenant_id,
        role=invitation.role,
        created_user=created_user,
    )
