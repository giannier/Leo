"""Tenants, memberships and the trial phone lock.

Three tables, three different relationships to the tenant filter, and the
difference is deliberate:

* `tenants` is the *root* of tenancy. Its key is `id`, not `tenant_id`, so
  `BaseRepository` would reject it at class-definition time - correctly.
* `tenant_users` and `trial_phone_locks` do carry `tenant_id`, so they go
  through `BaseRepository` and inherit the structural filter.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.tenant import Tenant, TenantUser, TrialPhoneLock
from app.repositories.base import BaseRepository

OWNER_ROLE = "owner"
TRIAL_STATUS = "trial"


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
