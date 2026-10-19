"""Funnel, products, satisfaction and surveys never cross businesses (Feature 013)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy.orm import Session

from app.models.analytics import CsatSurvey
from app.services.analytics import csat
from app.services.analytics.report import funnel, products, satisfaction
from tests.integration.campaign_fixtures import business, customer
from tests.integration.conftest import TEST_ENCRYPTION_KEY
from tests.integration.test_analytics import _journey, _sale


def test_another_business_counts_nothing(db: Session, monkeypatch: pytest.MonkeyPatch) -> None:
    from app.core.config import get_settings

    monkeypatch.setattr(get_settings(), "credentials_encryption_key", TEST_ENCRYPTION_KEY)
    mine, _ = business(db)
    theirs, _ = business(db)
    thread = _journey(db, theirs, reach="ganado", product="Torta")
    _sale(db, theirs, thread, "Torta", "99.00")
    contact = customer(db, theirs)
    db.add(
        CsatSurvey(
            tenant_id=theirs.id,
            conversation_id=thread.id,
            contact_id=contact.id,
            trigger="ganado",
            status="sent",
            sent_at=datetime.now(UTC),
        )
    )
    db.flush()
    today = datetime.now(UTC).date()
    assert funnel(db, mine.id, today - timedelta(days=1), today)["actual"]["conversaciones"] == 0
    assert products(db, mine.id, today, today, money=True)["vendidos"] == []
    assert satisfaction(db, mine.id, today, today, by_person=True)["enviadas"] == 0
    assert csat.pending_for(db, mine.id, contact.id, now=datetime.now(UTC)) is None
    assert csat.pending_for(db, theirs.id, contact.id, now=datetime.now(UTC)) is not None
