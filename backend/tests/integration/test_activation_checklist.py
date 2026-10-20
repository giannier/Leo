"""The activation checklist truth table (TASK-013, Clarification 009).

The Verification asks for every combination. The interesting ones are not the
all-on and all-off rows: they are the conditional payment item, and the gap
between `sandbox_ready` and `activation_ready`, which is what decides whether
a customer can try the product before we have done any manual work for them.

These exercise `build_checklist` against real rows rather than a mocked
repository, because the thing being tested is what the function concludes from
the database - a mock would only prove it reads the attributes I told it to.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from decimal import Decimal

import pytest
from sqlalchemy.orm import Session

from app.models.business import AgentConfig, BusinessProfile, PaymentMethodBusiness, Product
from app.models.tenant import Tenant
from app.models.user import User
from app.models.whatsapp import WhatsAppNumber
from app.services.activation import SANDBOX_ITEMS, build_checklist


@pytest.fixture
def tenant(db: Session) -> Tenant:
    row = Tenant(name="Checklist tenant")
    db.add(row)
    db.flush()
    return row


@pytest.fixture
def user(db: Session) -> User:
    row = User(
        email=f"check-{uuid.uuid4().hex[:10]}@example.com",
        password_hash="x",
        full_name="Checklist User",
    )
    db.add(row)
    db.flush()
    return row


def _complete_everything(
    db: Session, tenant: Tenant, user: User, *, objective: str = "ambos"
) -> None:
    """Every item satisfied, so each test can switch exactly one off."""
    user.email_verified_at = datetime.now(UTC)
    user.phone_verified_at = datetime.now(UTC)
    db.add_all(
        [
            BusinessProfile(tenant_id=tenant.id, business_name="Panaderia", industry="restaurante"),
            AgentConfig(tenant_id=tenant.id, agent_name="Leo", tone="cercano", objective=objective),
            Product(tenant_id=tenant.id, name="Pan", price=Decimal("1.00"), is_active=True),
            PaymentMethodBusiness(
                tenant_id=tenant.id, method="yape", instructions="Yape al 999", is_active=True
            ),
        ]
    )
    db.flush()


def _connect_number(db: Session, tenant: Tenant) -> None:
    db.add(
        WhatsAppNumber(
            tenant_id=tenant.id,
            phone_e164=f"+519{uuid.uuid4().int % 10**8:08d}",
            display_name="Panaderia",
            status="connected",
        )
    )
    db.flush()


class TestEverythingOff:
    def test_a_fresh_tenant_is_ready_for_nothing(
        self, db: Session, tenant: Tenant, user: User
    ) -> None:
        checklist = build_checklist(db, tenant_id=tenant.id, user=user)
        assert checklist.sandbox_ready is False
        assert checklist.activation_ready is False
        assert checklist.blocker == "checklist_incomplete"

    def test_all_five_items_are_always_reported(
        self, db: Session, tenant: Tenant, user: User
    ) -> None:
        """The list never changes length, so the panel is not re-rendered
        into a different shape as the customer edits."""
        checklist = build_checklist(db, tenant_id=tenant.id, user=user)
        assert len(checklist.items) == 5


class TestSandboxThreshold:
    def test_items_one_to_five_open_the_sandbox(
        self, db: Session, tenant: Tenant, user: User
    ) -> None:
        """The contact phone plays no part in trying the agent."""
        _complete_everything(db, tenant, user)
        user.phone_verified_at = None
        db.flush()

        checklist = build_checklist(db, tenant_id=tenant.id, user=user)
        assert checklist.sandbox_ready is True
        assert checklist.activation_ready is False

    @pytest.mark.parametrize("missing", SANDBOX_ITEMS)
    def test_any_missing_item_closes_the_sandbox(
        self, db: Session, tenant: Tenant, user: User, missing: str
    ) -> None:
        """One row per sandbox item: each on its own must be able to block."""
        _complete_everything(db, tenant, user)

        if missing == "email_verified":
            user.email_verified_at = None
        elif missing == "business_profile_complete":
            db.query(BusinessProfile).filter_by(tenant_id=tenant.id).delete()
        elif missing == "agent_config_complete":
            db.query(AgentConfig).filter_by(tenant_id=tenant.id).delete()
        elif missing == "has_active_product":
            db.query(Product).filter_by(tenant_id=tenant.id).update({"is_active": False})
        elif missing == "has_payment_method":
            db.query(PaymentMethodBusiness).filter_by(tenant_id=tenant.id).delete()
        db.flush()

        checklist = build_checklist(db, tenant_id=tenant.id, user=user)
        assert checklist.item(missing).done is False  # type: ignore[arg-type]
        assert checklist.sandbox_ready is False


class TestConditionalPaymentMethod:
    """Clarification 009: a payment method is required only if the objective
    includes selling. An agent that only answers questions has nothing to
    charge for."""

    @pytest.mark.parametrize("objective", ["vender", "ambos"])
    def test_selling_objectives_require_a_method(
        self, db: Session, tenant: Tenant, user: User, objective: str
    ) -> None:
        _complete_everything(db, tenant, user, objective=objective)
        db.query(PaymentMethodBusiness).filter_by(tenant_id=tenant.id).delete()
        db.flush()

        checklist = build_checklist(db, tenant_id=tenant.id, user=user)
        assert checklist.item("has_payment_method").required is True
        assert checklist.item("has_payment_method").done is False
        assert checklist.sandbox_ready is False

    def test_answering_only_does_not_require_a_method(
        self, db: Session, tenant: Tenant, user: User
    ) -> None:
        _complete_everything(db, tenant, user, objective="atender")
        db.query(PaymentMethodBusiness).filter_by(tenant_id=tenant.id).delete()
        db.flush()

        checklist = build_checklist(db, tenant_id=tenant.id, user=user)
        assert checklist.item("has_payment_method").required is False
        # Reported done so it neither blocks nor shows as an outstanding task
        # the customer cannot resolve.
        assert checklist.item("has_payment_method").done is True
        assert checklist.sandbox_ready is True

    def test_an_inactive_method_does_not_count(
        self, db: Session, tenant: Tenant, user: User
    ) -> None:
        """A deactivated method is one the business stopped accepting. Counting
        it would let the agent quote a payment route nobody honours."""
        _complete_everything(db, tenant, user, objective="vender")
        db.query(PaymentMethodBusiness).filter_by(tenant_id=tenant.id).update({"is_active": False})
        db.flush()

        assert build_checklist(db, tenant_id=tenant.id, user=user).sandbox_ready is False


class TestActivationThreshold:
    def test_everything_plus_a_connected_number(
        self, db: Session, tenant: Tenant, user: User
    ) -> None:
        _complete_everything(db, tenant, user)
        _connect_number(db, tenant)

        checklist = build_checklist(db, tenant_id=tenant.id, user=user)
        assert checklist.activation_ready is True
        assert checklist.blocker is None

    def test_a_complete_checklist_without_a_number_is_blocked_on_the_number(
        self, db: Session, tenant: Tenant, user: User
    ) -> None:
        _complete_everything(db, tenant, user)
        checklist = build_checklist(db, tenant_id=tenant.id, user=user)
        assert checklist.activation_ready is False
        assert checklist.blocker == "number_not_connected"

    def test_a_disconnected_number_does_not_count(
        self, db: Session, tenant: Tenant, user: User
    ) -> None:
        _complete_everything(db, tenant, user)
        db.add(
            WhatsAppNumber(
                tenant_id=tenant.id,
                phone_e164=f"+519{uuid.uuid4().int % 10**8:08d}",
                display_name="Panaderia",
                status="disconnected",
            )
        )
        db.flush()
        assert build_checklist(db, tenant_id=tenant.id, user=user).blocker == "number_not_connected"

    def test_an_incomplete_checklist_is_reported_before_the_number(
        self, db: Session, tenant: Tenant, user: User
    ) -> None:
        """The customer should be sent to fix what they can fix themselves;
        connecting a number needs us."""
        checklist = build_checklist(db, tenant_id=tenant.id, user=user)
        assert checklist.blocker == "checklist_incomplete"

    def test_an_unverified_contact_phone_does_not_block_activation(
        self, db: Session, tenant: Tenant, user: User
    ) -> None:
        """Owning the number is proved by its YCloud API key, so the contact phone
        is not on the checklist."""
        _complete_everything(db, tenant, user)
        _connect_number(db, tenant)
        user.phone_verified_at = None
        db.flush()

        checklist = build_checklist(db, tenant_id=tenant.id, user=user)
        assert checklist.sandbox_ready is True
        assert checklist.activation_ready is True


class TestProfileAndAgentCompleteness:
    def test_a_profile_without_industry_is_incomplete(
        self, db: Session, tenant: Tenant, user: User
    ) -> None:
        _complete_everything(db, tenant, user)
        db.query(BusinessProfile).filter_by(tenant_id=tenant.id).update({"industry": None})
        db.flush()
        assert build_checklist(db, tenant_id=tenant.id, user=user).sandbox_ready is False

    def test_a_description_is_not_required(self, db: Session, tenant: Tenant, user: User) -> None:
        """The field customers leave for later. Blocking the sandbox on prose
        would stop them reaching the moment that sells the product."""
        _complete_everything(db, tenant, user)
        db.query(BusinessProfile).filter_by(tenant_id=tenant.id).update({"description": None})
        db.flush()
        assert build_checklist(db, tenant_id=tenant.id, user=user).sandbox_ready is True

    def test_a_soft_deleted_product_does_not_count(
        self, db: Session, tenant: Tenant, user: User
    ) -> None:
        _complete_everything(db, tenant, user)
        db.query(Product).filter_by(tenant_id=tenant.id).update({"deleted_at": datetime.now(UTC)})
        db.flush()
        assert build_checklist(db, tenant_id=tenant.id, user=user).sandbox_ready is False


class TestPurity:
    def test_building_the_checklist_writes_nothing(
        self, db: Session, tenant: Tenant, user: User
    ) -> None:
        """Pure by contract (Clarification 009). The same function serves the
        endpoint and the activation guard; a side effect in it would fire on
        every poll of a screen the customer leaves open."""
        _complete_everything(db, tenant, user)
        db.flush()

        build_checklist(db, tenant_id=tenant.id, user=user)
        # Compared by length: SQLAlchemy returns an `IdentitySet`, which is
        # never equal to a plain `set` even when both are empty.
        assert len(db.new) == 0
        assert len(db.deleted) == 0
        assert len(db.dirty) == 0
