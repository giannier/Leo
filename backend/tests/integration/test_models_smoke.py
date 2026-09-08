"""Smoke tests for the migration 0001 schema and the ORM models.

These assert behaviour the migration is responsible for - seeded rows, CHECK
constraints, money precision, vector storage - rather than restating the
model definitions, which would pass even if the migration never ran.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.orm import Session

from app.models import (
    KnowledgeChunk,
    KnowledgeSource,
    Plan,
    Product,
    Tenant,
    TenantUser,
    User,
)
from app.models.knowledge import EMBEDDING_DIMENSIONS


def _make_user(db: Session, email: str) -> User:
    user = User(email=email, password_hash="not-a-real-hash", full_name="Test User")
    db.add(user)
    db.flush()
    return user


def _make_tenant(db: Session, name: str = "Test Tenant") -> Tenant:
    tenant = Tenant(name=name)
    db.add(tenant)
    db.flush()
    return tenant


class TestExtensions:
    def test_required_extensions_are_installed(self, db: Session) -> None:
        installed = set(
            db.execute(
                text("SELECT extname FROM pg_extension WHERE extname IN ('citext', 'vector')")
            )
            .scalars()
            .all()
        )
        assert installed == {"citext", "vector"}


class TestPlanSeed:
    def test_all_four_plans_are_seeded(self, db: Session) -> None:
        ids = set(db.execute(select(Plan.id)).scalars().all())
        assert ids == {"trial", "emprende", "crece", "pro"}

    def test_trial_plan_matches_the_documented_limits(self, db: Session) -> None:
        """Clarifications 001, 003 and 011 pin every one of these values."""
        trial = db.get(Plan, "trial")
        assert trial is not None
        assert trial.trial_days == 14
        assert trial.max_ai_messages == 250
        assert trial.max_seats == 2
        assert trial.max_rag_chars == 500_000
        assert trial.max_knowledge_files == 20

    def test_paid_plan_seat_and_rag_limits_match_the_clarifications(self, db: Session) -> None:
        seats = {p.id: p.max_seats for p in db.execute(select(Plan)).scalars()}
        rag = {p.id: p.max_rag_chars for p in db.execute(select(Plan)).scalars()}
        assert (seats["emprende"], seats["crece"], seats["pro"]) == (2, 5, 10)
        assert (rag["emprende"], rag["crece"], rag["pro"]) == (1_000_000, 4_000_000, 8_000_000)


class TestTenantDefaults:
    def test_new_tenant_starts_on_the_trial_plan(self, db: Session) -> None:
        tenant = _make_tenant(db)
        db.refresh(tenant)
        assert tenant.status == "trial"
        assert tenant.plan_id == "trial"
        assert tenant.ai_messages_used == 0

    def test_timestamps_are_timezone_aware(self, db: Session) -> None:
        """TIMESTAMPTZ, never a naive timestamp."""
        tenant = _make_tenant(db)
        db.refresh(tenant)
        assert tenant.created_at.tzinfo is not None


class TestCheckConstraints:
    def test_invalid_tenant_status_is_rejected(self, db: Session) -> None:
        db.add(Tenant(name="Bad Status", status="deleted"))
        with pytest.raises(IntegrityError):
            db.flush()

    def test_invalid_membership_role_is_rejected(self, db: Session) -> None:
        tenant = _make_tenant(db)
        user = _make_user(db, "role@example.com")
        db.add(TenantUser(tenant_id=tenant.id, user_id=user.id, role="superuser"))
        with pytest.raises(IntegrityError):
            db.flush()

    def test_negative_product_price_is_rejected(self, db: Session) -> None:
        tenant = _make_tenant(db)
        db.add(Product(tenant_id=tenant.id, name="Broken", price=Decimal("-1.00")))
        with pytest.raises(IntegrityError):
            db.flush()


class TestUniqueness:
    def test_email_uniqueness_is_case_insensitive(self, db: Session) -> None:
        """CITEXT, so 'A@b.com' and 'a@b.com' are the same account."""
        _make_user(db, "Duplicate@Example.com")
        db.add(
            User(
                email="duplicate@example.com",
                password_hash="x",
                full_name="Impostor",
            )
        )
        with pytest.raises(IntegrityError):
            db.flush()

    # Clarification 005 - one live trial per contact phone - is deliberately
    # absent from this file. It is no longer a schema constraint: the rule
    # depends on `tenants.status` at the moment it is asked, so it lives in
    # `TrialPhoneRepository` and is covered where behaviour is, in
    # `test_auth_registration.py` and `test_auth_phone_otp.py`.


class TestMoneyPrecision:
    def test_price_round_trips_as_exact_decimal(self, db: Session) -> None:
        """Money is Decimal, never float (Constitution)."""
        tenant = _make_tenant(db)
        product = Product(tenant_id=tenant.id, name="Menu", price=Decimal("19.99"))
        db.add(product)
        db.flush()
        db.refresh(product)
        assert isinstance(product.price, Decimal)
        assert product.price == Decimal("19.99")

    def test_scale_is_two_decimal_places(self, db: Session) -> None:
        tenant = _make_tenant(db)
        product = Product(tenant_id=tenant.id, name="Rounding", price=Decimal("10.005"))
        db.add(product)
        db.flush()
        db.refresh(product)
        # NUMERIC(12, 2) rounds half up at the database, it does not truncate.
        assert product.price == Decimal("10.01")


class TestVectorColumn:
    def test_embedding_round_trips_at_the_configured_dimension(self, db: Session) -> None:
        tenant = _make_tenant(db)
        source = KnowledgeSource(tenant_id=tenant.id, type="faq", title="Opening hours")
        db.add(source)
        db.flush()

        chunk = KnowledgeChunk(
            tenant_id=tenant.id,
            source_id=source.id,
            content="We open at 9am.",
            embedding=[0.1] * EMBEDDING_DIMENSIONS,
        )
        db.add(chunk)
        db.flush()
        db.refresh(chunk)

        assert chunk.embedding is not None
        assert len(chunk.embedding) == EMBEDDING_DIMENSIONS

    def test_wrong_embedding_dimension_is_rejected(self, db: Session) -> None:
        tenant = _make_tenant(db)
        source = KnowledgeSource(tenant_id=tenant.id, type="faq", title="Wrong width")
        db.add(source)
        db.flush()

        db.add(
            KnowledgeChunk(
                tenant_id=tenant.id,
                source_id=source.id,
                content="too short",
                embedding=[0.1] * 8,
            )
        )
        with pytest.raises(DBAPIError):
            db.flush()


class TestCascades:
    def test_deleting_a_source_deletes_its_chunks(self, db: Session) -> None:
        """Orphaned embeddings would keep answering for a deleted document."""
        tenant = _make_tenant(db)
        source = KnowledgeSource(tenant_id=tenant.id, type="text", title="Doomed")
        db.add(source)
        db.flush()
        db.add(
            KnowledgeChunk(
                tenant_id=tenant.id,
                source_id=source.id,
                content="chunk",
                embedding=[0.0] * EMBEDDING_DIMENSIONS,
            )
        )
        db.flush()

        db.delete(source)
        db.flush()

        remaining = db.execute(
            select(func.count())
            .select_from(KnowledgeChunk)
            .where(KnowledgeChunk.source_id == source.id)
        ).scalar_one()
        assert remaining == 0


class TestInvitationWindow:
    def test_expiry_is_stored_with_its_timezone(self, db: Session) -> None:
        from app.models import Invitation

        tenant = _make_tenant(db)
        inviter = _make_user(db, "inviter@example.com")
        expires = datetime.now(UTC) + timedelta(days=7)
        invitation = Invitation(
            tenant_id=tenant.id,
            email="guest@example.com",
            role="operator",
            token_hash="hashed-token",
            expires_at=expires,
            invited_by=inviter.id,
        )
        db.add(invitation)
        db.flush()
        db.refresh(invitation)
        assert invitation.expires_at.tzinfo is not None
        assert invitation.accepted_at is None
