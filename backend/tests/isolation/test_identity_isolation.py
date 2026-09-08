"""Isolation suite for the tables TASK-006 added repositories for.

`tenant_users` carries `tenant_id`, so it goes through `BaseRepository` and
must obey the same rule as the catalog: one tenant can neither read nor
unmake the other's rows. A leak there is a leak of who has access to a
business account.

`TrialPhoneRepository` is the opposite shape and gets its own class below.
Its question - "is another business, in trial today, using this number?" -
is cross-tenant on purpose, so it cannot be bound to one `tenant_id`. What
is tested instead is that the answer is a bare boolean: knowing that a
number is taken must never become knowing who has it.

Rows are created inline rather than in the shared fixture so that the
existing meta-tests, which assert exact row counts, keep meaning what they
say.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import TenantUser, User
from app.repositories import (
    TenantScopeError,
    TenantUserRepository,
    TrialPhoneRepository,
)
from app.repositories.base import BaseRepository
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


def _owns(db: Session, tenant_id: uuid.UUID, label: str, phone: str) -> User:
    """Give a tenant an active owner whose contact phone is `phone`."""
    user = _user(db, label)
    user.phone_e164 = phone
    db.flush()
    TenantUserRepository(db, tenant_id).add_member(user_id=user.id, role="owner")
    return user


class TestTheContactPhoneCheckLeaksNothing:
    """Clarification 005 asks about other tenants, so it must answer sparely.

    The rule cannot be tenant-scoped - it exists to find a *different*
    business - so the boundary is held by what comes back rather than by
    what is read. These tests fail if the answer ever grows richer than
    `True` or `False`.
    """

    def test_a_number_held_by_another_live_trial_is_reported_taken(
        self, db: Session, tenant_a: TenantFixture, tenant_b: TenantFixture
    ) -> None:
        phone = _phone()
        _owns(db, tenant_b.tenant_id, "b", phone)

        assert (
            TrialPhoneRepository(db).held_by_another_trial(
                phone, excluding_tenant_id=tenant_a.tenant_id
            )
            is True
        )

    def test_the_answer_is_a_bare_boolean_and_never_the_other_tenant(
        self, db: Session, tenant_a: TenantFixture, tenant_b: TenantFixture
    ) -> None:
        """The identity of the business holding a number is not ours to hand out."""
        phone = _phone()
        _owns(db, tenant_b.tenant_id, "b", phone)

        answer = TrialPhoneRepository(db).held_by_another_trial(
            phone, excluding_tenant_id=tenant_a.tenant_id
        )

        assert type(answer) is bool
        assert str(tenant_b.tenant_id) not in repr(answer)

    def test_a_tenant_is_never_blocked_by_its_own_number(
        self, db: Session, tenant_a: TenantFixture
    ) -> None:
        phone = _phone()
        _owns(db, tenant_a.tenant_id, "a", phone)

        assert (
            TrialPhoneRepository(db).held_by_another_trial(
                phone, excluding_tenant_id=tenant_a.tenant_id
            )
            is False
        )

    def test_a_number_nobody_uses_is_free(self, db: Session, tenant_a: TenantFixture) -> None:
        assert (
            TrialPhoneRepository(db).held_by_another_trial(
                _phone(), excluding_tenant_id=tenant_a.tenant_id
            )
            is False
        )

    def test_only_the_owner_of_a_tenant_holds_a_number(
        self, db: Session, tenant_a: TenantFixture, tenant_b: TenantFixture
    ) -> None:
        """An operator is not the business's contact, so their phone is not it."""
        phone = _phone()
        operator = _user(db, "b")
        operator.phone_e164 = phone
        db.flush()
        TenantUserRepository(db, tenant_b.tenant_id).add_member(
            user_id=operator.id, role="operator"
        )

        assert (
            TrialPhoneRepository(db).held_by_another_trial(
                phone, excluding_tenant_id=tenant_a.tenant_id
            )
            is False
        )

    def test_the_check_is_deliberately_not_a_tenant_repository(self) -> None:
        """Binding it to one tenant would make its question unanswerable."""
        assert not issubclass(TrialPhoneRepository, BaseRepository)


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
    with pytest.raises(TypeError):
        TenantUserRepository(db)  # type: ignore[call-arg]


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
