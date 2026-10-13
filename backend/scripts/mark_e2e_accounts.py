"""Mark the businesses the end-to-end suite created as test accounts (TASK-455).

    make front-e2e        # runs this after Playwright, pass or fail
    make mark-e2e         # or on its own

A business is the suite's when its owner's address is `e2e-<stamp>@example.com`
(`frontend/e2e/outbox.ts`). Marked tenants are hidden from the platform panel.
Nothing is deleted. Development only.
"""

from __future__ import annotations

import sys

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.db import get_session_factory
from app.models.tenant import Tenant, TenantUser
from app.models.user import User

# What `frontend/e2e/outbox.ts` builds.
E2E_EMAIL_PATTERN = "e2e-%@example.com"


def mark(session: Session) -> int:
    """Mark every suite-created tenant; returns how many changed."""
    owned_by_suite = (
        select(TenantUser.tenant_id)
        .join(User, User.id == TenantUser.user_id)
        .where(TenantUser.role == "owner", User.email.like(E2E_EMAIL_PATTERN))
    )
    result = session.execute(
        update(Tenant)
        .where(Tenant.id.in_(owned_by_suite), Tenant.es_prueba.is_(False))
        .values(es_prueba=True)
    )
    return int(result.rowcount or 0)


def main() -> int:
    if get_settings().environment not in ("development", "test"):
        print("Solo para desarrollo.")
        return 1
    with get_session_factory()() as session:
        changed = mark(session)
        session.commit()
    print(f"cuentas de e2e marcadas como prueba: {changed}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
