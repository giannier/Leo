"""A business never sees another's notices (Feature 004, TASK-431)."""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy.orm import Session

from app.models.tenant import HANDOFF, LLM_DOWN
from app.repositories.notification import TenantNotificationRepository
from app.services.tenant_alerts import TenantAlerts
from tests.isolation.conftest import TenantFixture


class TestNotices:
    def test_each_tenant_reads_only_its_own(
        self, db: Session, tenant_a: TenantFixture, tenant_b: TenantFixture
    ) -> None:
        TenantAlerts(db, tenant_b.tenant_id).handoff(
            tenant_b.conversation_id, motivo="queja", resumen="datos de B"
        )

        assert TenantNotificationRepository(db, tenant_a.tenant_id).recent() == []
        [theirs] = TenantNotificationRepository(db, tenant_b.tenant_id).recent(HANDOFF)
        assert theirs.payload["resumen"] == "datos de B"

    def test_the_hourly_email_limit_is_per_tenant(
        self, db: Session, tenant_a: TenantFixture, tenant_b: TenantFixture
    ) -> None:
        theirs = TenantAlerts(db, tenant_b.tenant_id)
        theirs.llm_down()

        mine = TenantAlerts(db, tenant_a.tenant_id)
        mine.llm_down()

        assert len(TenantNotificationRepository(db, tenant_a.tenant_id).recent(LLM_DOWN)) == 1
        assert TenantNotificationRepository(db, tenant_a.tenant_id).emailed_since(
            LLM_DOWN, since=datetime(2000, 1, 1, tzinfo=UTC)
        )
