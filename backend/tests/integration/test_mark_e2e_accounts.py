"""TASK-455: the end-to-end suite's businesses are marked and hidden from the platform panel."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

from scripts.mark_e2e_accounts import mark
from sqlalchemy.orm import Session

from app.models.tenant import Tenant
from app.models.user import User
from app.repositories.tenant import TenantRepository, TenantUserRepository
from app.services.admin import PlatformAdminService


def _business(db: Session, email: str) -> Tenant:
    user = User(email=email, password_hash="x", full_name="Prueba")
    db.add(user)
    db.flush()
    tenant = TenantRepository(db).create_trial(
        name=f"Negocio {uuid.uuid4().hex[:8]}",
        plan_id="trial",
        trial_ends_at=datetime.now(UTC) + timedelta(days=14),
    )
    TenantUserRepository(db, tenant.id).add_member(user_id=user.id, role="owner")
    db.flush()
    return tenant


def test_only_the_suites_businesses_are_marked_and_hidden(db: Session) -> None:
    suite = _business(db, f"e2e-{uuid.uuid4().hex[:10]}@example.com")
    real = _business(db, f"ana-{uuid.uuid4().hex[:10]}@example.com")
    admin = db.query(User).filter(User.is_platform_admin.is_(True)).first() or User(
        email="x@y.z", password_hash="x", full_name="Admin", is_platform_admin=True
    )

    assert mark(db) >= 1
    db.expire_all()

    assert db.get(Tenant, suite.id).es_prueba is True  # type: ignore[union-attr]
    assert db.get(Tenant, real.id).es_prueba is False  # type: ignore[union-attr]
    service = PlatformAdminService(db, actor=admin)
    listed = {t.id for t in service.list_tenants(limit=200)}
    everything = {t.id for t in service.list_tenants(limit=200, include_test=True)}
    assert suite.id not in listed and real.id in listed
    assert suite.id in everything
    # A second run finds nothing left to mark.
    assert mark(db) == 0
