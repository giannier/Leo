"""Tenants, memberships and the contact-phone anti-abuse check.

Different relationships to the tenant filter, and the difference is
deliberate:

* `tenants` is the *root* of tenancy. Its key is `id`, not `tenant_id`, so
  `BaseRepository` would reject it at class-definition time - correctly.
* `tenant_users` does carry `tenant_id`, so it goes through `BaseRepository`
  and inherits the structural filter.
* `UserMembershipRepository` is scoped by `user_id` instead, and
  `TrialPhoneRepository` answers a question that is cross-tenant by
  definition. Both are deliberate exceptions; see their docstrings.
"""

from __future__ import annotations

import hashlib
import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import Select, select, text
from sqlalchemy.orm import Session

from app.models.tenant import Invitation, Tenant, TenantUser
from app.models.user import User
from app.repositories.base import BaseRepository

OWNER_ROLE = "owner"
TENANT_ADMIN_ROLE = "tenant_admin"
OPERATOR_ROLE = "operator"

ACTIVE_MEMBERSHIP = "active"
TRIAL_STATUS = "trial"
PAUSED_STATUS = "paused"

# The statuses in which a tenant still holds its owner's contact phone.
#
# Not `trial` alone. The lifecycle is `trial -> paused -> archived`, so a trial
# that simply runs out lands in `paused` - and freeing the number there would
# mean letting it go the instant the trial expires, which is a fresh trial on
# the same phone a second later, and the next one after that. The number comes
# back at `archived`, thirty days on, which is long enough that farming trials
# costs more than paying for one.
PHONE_HOLDING_STATUSES = (TRIAL_STATUS, PAUSED_STATUS)


class TenantRepository:
    """The tenant record itself."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def get(self, tenant_id: uuid.UUID) -> Tenant | None:
        return self._session.get(Tenant, tenant_id)

    def lock(self, tenant_id: uuid.UUID) -> Tenant | None:
        """Take a row lock on the tenant for the rest of the transaction.

        The mutex for anything that has to read a per-tenant total, decide
        against a plan limit and then write - the RAG character budget being
        the first case. Two uploads landing together would otherwise both
        read the same "used" figure, both find room, and both commit, putting
        the tenant over their plan.

        `SELECT ... FOR UPDATE` on the tenant row serialises them: the second
        transaction blocks until the first commits, then reads a total that
        already includes it. Contention is per tenant, so no two customers
        ever wait on each other, and the lock is released by the commit that
        makes the reservation visible - there is no window in between.
        """
        return self._session.get(Tenant, tenant_id, with_for_update=True)

    def create_trial(self, *, name: str, plan_id: str, trial_ends_at: datetime) -> Tenant:
        """Open a tenant on a trial plan.

        `trial_ends_at` is passed in rather than computed here: the trial
        length belongs to the `plans` row, and the caller is the one holding
        it. Nothing in this layer knows how long a trial is.
        """
        tenant = Tenant(
            name=name,
            status=TRIAL_STATUS,
            plan_id=plan_id,
            trial_ends_at=trial_ends_at,
        )
        self._session.add(tenant)
        self._session.flush()
        return tenant


class TenantUserRepository(BaseRepository[TenantUser]):
    """Memberships of one tenant."""

    model = TenantUser

    def add_member(self, *, user_id: uuid.UUID, role: str) -> TenantUser:
        return self.add(user_id=user_id, role=role)

    def list_members(self) -> Sequence[TenantUser]:
        return self.list()

    def membership_for(self, user_id: uuid.UUID) -> TenantUser | None:
        statement = self._scoped().where(TenantUser.user_id == user_id)
        return self._session.execute(statement).scalar_one_or_none()

    def active_membership_for(self, user_id: uuid.UUID) -> TenantUser | None:
        """The membership that currently grants access, if any.

        This is the query behind every authenticated request: the access
        token names a tenant, and this decides whether the caller may act in
        it and with what role. A disabled member is treated exactly like a
        stranger - the row is still there for the audit trail, but it grants
        nothing, so deactivating someone takes effect on their very next
        request instead of when their token happens to expire.
        """
        statement = self._scoped().where(
            TenantUser.user_id == user_id,
            TenantUser.status == ACTIVE_MEMBERSHIP,
        )
        return self._session.execute(statement).scalar_one_or_none()


@dataclass(frozen=True)
class TenantMembership:
    """One "you belong to this business, as this" row, ready to serialise."""

    tenant_id: uuid.UUID
    tenant_name: str
    role: str


class UserMembershipRepository:
    """ "Which businesses do I belong to?", answered for exactly one user.

    This is the one read of `tenant_users` that cannot be tenant-scoped: at
    login there is no tenant yet - working out which tenants the caller may
    choose from is the whole question. Answering it through
    `TenantUserRepository` would mean looping over every tenant in the
    platform and asking each one whether this user is in it, which reads far
    more than it returns.

    So the filter is not weakened, it is *changed*: `user_id` takes the place
    of `tenant_id` as the mandatory predicate, and it is bound in the
    constructor for the same reason `BaseRepository` binds the tenant - there
    must be no way to reach a query method without having supplied it. Every
    statement below starts from `_scoped()`.

    The isolation suite covers this class directly: a user must never see a
    membership that is not theirs, and the tenant they end up acting in must
    still be checked against `TenantUserRepository`, which is tenant-scoped.
    """

    def __init__(self, session: Session, user_id: uuid.UUID) -> None:
        self._session = session
        self._user_id = user_id

    @property
    def user_id(self) -> uuid.UUID:
        """The user every operation on this repository is confined to."""
        return self._user_id

    def _scoped(self) -> Select[tuple[TenantUser, Tenant]]:
        """A SELECT already narrowed to this user's memberships.

        Ordered by tenant creation so the "first" membership - what a login
        without an explicit choice activates - is stable across requests
        rather than whatever the planner returns that day. `id` breaks the
        tie for two tenants created in the same transaction.
        """
        return (
            select(TenantUser, Tenant)
            .join(Tenant, Tenant.id == TenantUser.tenant_id)
            .where(TenantUser.user_id == self._user_id)
            .order_by(Tenant.created_at, Tenant.id)
        )

    def active_memberships(self) -> Sequence[TenantMembership]:
        """Every tenant this user may currently act in."""
        statement = self._scoped().where(TenantUser.status == ACTIVE_MEMBERSHIP)
        rows = self._session.execute(statement).all()
        return [
            TenantMembership(
                tenant_id=tenant.id,
                tenant_name=tenant.name,
                role=membership.role,
            )
            for membership, tenant in rows
        ]


class InvitationRepository(BaseRepository[Invitation]):
    """Pending invitations for one tenant. Only digests are stored."""

    model = Invitation

    def issue(
        self,
        *,
        email: str,
        role: str,
        token_hash: str,
        expires_at: datetime,
        invited_by: uuid.UUID,
    ) -> Invitation:
        return self.add(
            email=email,
            role=role,
            token_hash=token_hash,
            expires_at=expires_at,
            invited_by=invited_by,
        )

    def list_pending(self, *, now: datetime) -> Sequence[Invitation]:
        """Invitations still worth showing: neither accepted, revoked nor expired."""
        return self.list(
            Invitation.accepted_at.is_(None),
            Invitation.revoked_at.is_(None),
            Invitation.expires_at > now,
        )

    def pending_count(self, *, now: datetime) -> int:
        """Live invitations, which count against the seat limit.

        An invitation that has been sent but not yet accepted is a seat
        already promised. Counting only accepted members would let an owner
        invite ten people into a two-seat plan and discover the problem only
        when the third one clicked.
        """
        return self.count(
            Invitation.accepted_at.is_(None),
            Invitation.revoked_at.is_(None),
            Invitation.expires_at > now,
        )

    def find_pending_for_email(self, *, email: str, now: datetime) -> Invitation | None:
        statement = (
            self._scoped()
            .where(
                Invitation.email == email,
                Invitation.accepted_at.is_(None),
                Invitation.revoked_at.is_(None),
                Invitation.expires_at > now,
            )
            .limit(1)
        )
        return self._session.execute(statement).scalar_one_or_none()

    def revoke(self, invitation: Invitation, *, at: datetime) -> None:
        invitation.revoked_at = at
        self._session.flush()

    def accept(self, invitation: Invitation, *, at: datetime) -> None:
        invitation.accepted_at = at
        self._session.flush()


class InvitationLookup:
    """Finds an invitation by its token digest, across every tenant.

    The one read of `invitations` that cannot be tenant-scoped: acceptance is
    a public endpoint, so at that moment there is no authenticated caller and
    therefore no tenant to scope by - working out which tenant the token
    belongs to is the whole question.

    Same shape as `UserMembershipRepository`: the filter is not weakened, it
    is *changed*. A 256-bit digest is the mandatory predicate, and it is
    unique, so this can return at most one row and an attacker cannot enumerate
    with it. Everything the caller then does goes through the tenant-scoped
    repositories above.
    """

    def __init__(self, session: Session) -> None:
        self._session = session

    def by_token_hash(self, token_hash: str) -> Invitation | None:
        statement = select(Invitation).where(Invitation.token_hash == token_hash)
        return self._session.execute(statement).scalar_one_or_none()


# Namespace for the contact-phone advisory locks. Any fixed value works; a
# constant of our own keeps this family from colliding with a future,
# unrelated use of the same PostgreSQL facility.
PHONE_LOCK_NAMESPACE = "trial-contact-phone"


def _advisory_key(phone_e164: str) -> int:
    """A stable 64-bit advisory-lock key for one phone number.

    A digest rather than `hash()`: Python salts `hash()` per process, so two
    API workers would derive different keys for the same number and would
    never serialise against each other - which is the entire point of taking
    the lock.
    """
    material = f"{PHONE_LOCK_NAMESPACE}:{phone_e164}".encode()
    return int.from_bytes(hashlib.blake2b(material, digest_size=8).digest(), "big", signed=True)


class TrialPhoneRepository:
    """Is this contact phone still held by another tenant?

    Clarification 005 forbids one verified contact phone from sitting in more
    than one tenant "en trial simultaneamente". The question is asked of live
    state - the owner's phone joined to their tenant's status - and never of a
    stored claim: a tenant stops holding its number the moment its status
    changes, so there is nothing to release, and nothing that leaks when some
    future code path forgets to.

    Read to the letter, "en trial" would free the number at `paused`. But a
    trial that expires *becomes* `paused`, so that reading hands back the
    phone at the exact moment the trial ends, and one person can run trials on
    one number forever. The rule is therefore held through `paused` and
    released at `archived` - see `PHONE_HOLDING_STATUSES`.

    Deliberately not a `BaseRepository`, and for a different reason than
    `UserMembershipRepository`: this question is cross-tenant by definition -
    it asks about *other* businesses - so binding it to one `tenant_id` would
    make it unanswerable. What keeps that safe is the return type.
    `held_by_another_trial` answers `True` or `False` and nothing else: no
    tenant id, no name, no owner, not even the row that matched. The caller
    cannot learn who holds the number, so neither can the customer reading
    the error message.
    """

    def __init__(self, session: Session) -> None:
        self._session = session

    def hold(self, phone_e164: str) -> None:
        """Serialise every claim on this number until the transaction ends.

        The check is a read followed by a write - "is anyone else on trial
        with this number?", and then the rows that make it true. Two
        registrations a millisecond apart would both read "free" and both
        commit, which is precisely the abuse Clarification 005 exists to
        stop. The table this replaced got that atomicity from a primary key;
        a query over live state cannot, because the condition spans three
        tables and PostgreSQL does not allow subqueries in index expressions.

        A transaction-scoped advisory lock keyed on the number closes the
        window instead: the second transaction waits, and by the time it runs
        its read the first is committed and visible. It is released by the
        commit or rollback that ends the transaction - unlike the lock table,
        there is nothing to free by hand and nothing to leak.

        Keyed per number, so two customers registering different phones never
        wait on each other.
        """
        self._session.execute(
            text("SELECT pg_advisory_xact_lock(:key)"),
            {"key": _advisory_key(phone_e164)},
        )

    def held_by_another_trial(self, phone_e164: str, *, excluding_tenant_id: uuid.UUID) -> bool:
        """Whether some *other* tenant still has this phone.

        "Has" means: it is the contact phone of the tenant's active owner, and
        that tenant has not been archived yet. `excluding_tenant_id` is what
        stops a tenant from being blocked by itself when it re-verifies its
        own number.
        """
        conflict = (
            select(TenantUser.id)
            .join(Tenant, Tenant.id == TenantUser.tenant_id)
            .join(User, User.id == TenantUser.user_id)
            .where(
                Tenant.status.in_(PHONE_HOLDING_STATUSES),
                Tenant.id != excluding_tenant_id,
                TenantUser.role == OWNER_ROLE,
                TenantUser.status == ACTIVE_MEMBERSHIP,
                User.phone_e164 == phone_e164,
            )
        )
        # `EXISTS`, so a boolean is all that crosses the tenant boundary.
        return bool(self._session.execute(select(conflict.exists())).scalar_one())
