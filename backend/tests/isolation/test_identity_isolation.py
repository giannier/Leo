"""Isolation suite for the tables TASK-006 added repositories for.

`tenant_users` and `trial_phone_locks` both carry `tenant_id`, so they go
through `BaseRepository` and must obey the same rule as the catalog: one
tenant can neither read nor unmake the other's rows. Memberships are the
higher-value target of the two - a leak there is a leak of who has access to
a business account.

Rows are created inline rather than in the shared fixture so that the
existing meta-tests, which assert exact row counts, keep meaning what they
say.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models import TenantUser, TrialPhoneLock, User
from app.repositories import (
    TenantScopeError,
    TenantUserRepository,
    TrialPhoneLockRepository,
)
from tests.isolation.conftest import TenantFixture


def _user(db: Session, label: str) -> User:
    user = User(
        email=f"{label}-{uuid.uuid4().hex[:8]}@example.com",
        password_hash="not-a-real-hash",
        full_name=f"Owner {label}",
    )
    db.add(user)
    db.flush()
    return user


def _phone() -> str:
    return f"+519{uuid.uuid4().int % 10**8:08d}"


class TestMembershipsAreScoped:
    def test_listing_members_never_shows_another_tenants_team(
        self, db: Session, tenant_a: TenantFixture, tenant_b: TenantFixture
    ) -> None:
        owner_a, owner_b = _user(db, "a"), _user(db, "b")
        TenantUserRepository(db, tenant_a.tenant_id).add_member(user_id=owner_a.id, role="owner")
        TenantUserRepository(db, tenant_b.tenant_id).add_member(user_id=owner_b.id, role="owner")

        members = TenantUserRepository(db, tenant_a.tenant_id).list_members()

        assert {member.user_id for member in members} == {owner_a.id}

    def test_membership_lookup_of_a_foreign_user_returns_none(
        self, db: Session, tenant_a: TenantFixture, tenant_b: TenantFixture
    ) -> None:
        """Knowing a user id must not reveal that they belong somewhere else."""
        owner_b = _user(db, "b")
        TenantUserRepository(db, tenant_b.tenant_id).add_member(user_id=owner_b.id, role="owner")

        assert TenantUserRepository(db, tenant_a.tenant_id).membership_for(owner_b.id) is None

    def test_seat_count_only_counts_own_members(
        self, db: Session, tenant_a: TenantFixture, tenant_b: TenantFixture
    ) -> None:
        """Feature 009 enforces `plans.max_seats` off this count."""
        TenantUserRepository(db, tenant_a.tenant_id).add_member(
            user_id=_user(db, "a").id, role="owner"
        )
        for label in ("b1", "b2"):
            TenantUserRepository(db, tenant_b.tenant_id).add_member(
                user_id=_user(db, label).id, role="operator"
            )

        assert TenantUserRepository(db, tenant_a.tenant_id).count() == 1

    def test_a_membership_cannot_be_planted_in_another_tenant(
        self, db: Session, tenant_a: TenantFixture, tenant_b: TenantFixture
    ) -> None:
        repo = TenantUserRepository(db, tenant_a.tenant_id)
        with pytest.raises(TenantScopeError):
            repo.add(user_id=_user(db, "a").id, role="owner", tenant_id=tenant_b.tenant_id)

    def test_a_foreign_membership_cannot_be_deleted(
        self, db: Session, tenant_a: TenantFixture, tenant_b: TenantFixture
    ) -> None:
        """Deleting a rival's owner row would lock them out of their account."""
        victim = TenantUserRepository(db, tenant_b.tenant_id).add_member(
            user_id=_user(db, "b").id, role="owner"
        )

        assert TenantUserRepository(db, tenant_a.tenant_id).delete(victim.id) is False
        assert db.get(TenantUser, victim.id) is not None


class TestTrialPhoneLocksAreScoped:
    def test_held_phones_are_listed_per_tenant(
        self, db: Session, tenant_a: TenantFixture, tenant_b: TenantFixture
    ) -> None:
        phone_a, phone_b = _phone(), _phone()
        TrialPhoneLockRepository(db, tenant_a.tenant_id).claim(phone_a)
        TrialPhoneLockRepository(db, tenant_b.tenant_id).claim(phone_b)

        assert list(TrialPhoneLockRepository(db, tenant_a.tenant_id).held_phones()) == [phone_a]

    def test_releasing_another_tenants_lock_is_refused(
        self, db: Session, tenant_a: TenantFixture, tenant_b: TenantFixture
    ) -> None:
        """Otherwise anyone could free a phone and re-register a fresh trial."""
        phone_b = _phone()
        TrialPhoneLockRepository(db, tenant_b.tenant_id).claim(phone_b)

        assert TrialPhoneLockRepository(db, tenant_a.tenant_id).release(phone_b) is False
        assert db.get(TrialPhoneLock, phone_b) is not None

    def test_a_tenant_can_release_its_own_lock(self, db: Session, tenant_a: TenantFixture) -> None:
        phone = _phone()
        repo = TrialPhoneLockRepository(db, tenant_a.tenant_id)
        repo.claim(phone)

        assert repo.release(phone) is True
        assert db.get(TrialPhoneLock, phone) is None

    def test_the_lock_is_stamped_with_the_bound_tenant(
        self, db: Session, tenant_a: TenantFixture
    ) -> None:
        lock = TrialPhoneLockRepository(db, tenant_a.tenant_id).claim(_phone())
        assert lock.tenant_id == tenant_a.tenant_id

    def test_lookup_by_uuid_is_refused_rather_than_silently_wrong(
        self, db: Session, tenant_a: TenantFixture
    ) -> None:
        """The table is keyed by phone number; there is no `id` column."""
        repo = TrialPhoneLockRepository(db, tenant_a.tenant_id)
        with pytest.raises(NotImplementedError, match="phone_e164"):
            repo.get(uuid.uuid4())

    def test_a_second_tenant_cannot_take_a_held_phone(
        self, db: Session, tenant_a: TenantFixture, tenant_b: TenantFixture
    ) -> None:
        """Clarification 005, enforced by the primary key and nothing else.

        Left as the last statement in the test on purpose: the failed insert
        aborts the transaction, and the fixture rolls it back.
        """
        phone = _phone()
        TrialPhoneLockRepository(db, tenant_a.tenant_id).claim(phone)

        with pytest.raises(IntegrityError):
            TrialPhoneLockRepository(db, tenant_b.tenant_id).claim(phone)


class TestIdentityTablesStayOutOfBaseRepository:
    def test_email_tokens_cannot_be_given_a_tenant_repository(self) -> None:
        """`email_tokens` has no tenant_id, so the guard must reject it."""
        from app.models import EmailToken
        from app.repositories.base import BaseRepository

        with pytest.raises(TypeError, match="tenant_id"):

            class EmailTokenRepository(BaseRepository[EmailToken]):  # type: ignore[type-var]
                model = EmailToken

    def test_the_real_identity_repositories_are_not_tenant_repositories(self) -> None:
        from app.repositories import EmailTokenRepository, UserRepository
        from app.repositories.base import BaseRepository

        assert not issubclass(UserRepository, BaseRepository)
        assert not issubclass(EmailTokenRepository, BaseRepository)

    def test_the_tenant_table_repository_is_not_a_tenant_repository(self) -> None:
        """`tenants` is the root of tenancy: its key is `id`, not `tenant_id`."""
        from app.repositories import TenantRepository
        from app.repositories.base import BaseRepository

        assert not issubclass(TenantRepository, BaseRepository)


def test_the_new_repositories_are_bound_to_a_tenant_at_construction(
    tenant_a: TenantFixture, db: Session
) -> None:
    """No query method is reachable without having supplied a tenant."""
    for repository in (TenantUserRepository, TrialPhoneLockRepository):
        with pytest.raises(TypeError):
            repository(db)  # type: ignore[call-arg]


def test_membership_rows_are_really_written(db: Session, tenant_a: TenantFixture) -> None:
    """An empty table would satisfy every scoping assertion above."""
    owner = _user(db, "a")
    TenantUserRepository(db, tenant_a.tenant_id).add_member(user_id=owner.id, role="owner")

    rows = (
        db.execute(select(TenantUser).where(TenantUser.tenant_id == tenant_a.tenant_id))
        .scalars()
        .all()
    )
    assert len(rows) == 1
