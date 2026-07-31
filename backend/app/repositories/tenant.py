"""Tenants, memberships and the trial phone lock.

Three tables, three different relationships to the tenant filter, and the
difference is deliberate:

* `tenants` is the *root* of tenancy. Its key is `id`, not `tenant_id`, so
  `BaseRepository` would reject it at class-definition time - correctly.
* `tenant_users` and `trial_phone_locks` do carry `tenant_id`, so they go
  through `BaseRepository` and inherit the structural filter.
* `UserMembershipRepository` is the one deliberate exception, and it is
  scoped by `user_id` instead. See its docstring.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import Select, select
from sqlalchemy.orm import Session

from app.models.tenant import Tenant, TenantUser, TrialPhoneLock
from app.repositories.base import BaseRepository

OWNER_ROLE = "owner"
TENANT_ADMIN_ROLE = "tenant_admin"
OPERATOR_ROLE = "operator"

ACTIVE_MEMBERSHIP = "active"
TRIAL_STATUS = "trial"


class TenantRepository:
    """The tenant record itself."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def get(self, tenant_id: uuid.UUID) -> Tenant | None:
        return self._session.get(Tenant, tenant_id)

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


class TrialPhoneLockRepository(BaseRepository[TrialPhoneLock]):
    """One live trial per contact phone (Clarification 005).

    The rule is enforced by the primary key on `phone_e164`, not by a check
    in application code: `claim()` inserts and lets the database reject the
    second attempt. A "is this phone free?" query followed by an insert would
    race - two registrations a millisecond apart would both read free and
    both proceed, which is exactly the abuse pattern the rule exists to stop.
    """

    model = TrialPhoneLock

    def get(self, entity_id: uuid.UUID) -> TrialPhoneLock | None:
        """Not available: this table is keyed by phone number, not by UUID."""
        raise NotImplementedError(
            "trial_phone_locks has no `id` column; it is keyed by phone_e164. "
            "Use claim() to take a lock and release() to give it back."
        )

    def claim(self, phone_e164: str) -> TrialPhoneLock:
        """Take the lock for this tenant, or raise `IntegrityError`."""
        return self.add(phone_e164=phone_e164)

    def release(self, phone_e164: str) -> bool:
        """Give the lock back. Scoped, so one tenant cannot free another's."""
        statement = self._scoped().where(TrialPhoneLock.phone_e164 == phone_e164)
        lock = self._session.execute(statement).scalar_one_or_none()
        if lock is None:
            return False
        self._session.delete(lock)
        self._session.flush()
        return True

    def held_phones(self) -> Sequence[str]:
        statement = select(TrialPhoneLock.phone_e164).where(self._tenant_predicate)
        return self._session.execute(statement).scalars().all()
