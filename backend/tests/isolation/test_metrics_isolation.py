"""Metrics never cross businesses (Feature 010)."""

from __future__ import annotations

from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.metrics import MetricsDaily
from app.services.metrics.report import tenant_report
from app.services.metrics.rollup import compute_day, rollup, zone_of
from tests.integration.test_metrics import DAY, _a_busy_day, _at, _tenant


class TestOneBusinessNeverCountsAnothers:
    def test_the_day_the_rows_and_the_report_are_its_own(self, db: Session) -> None:
        busy = _tenant(db)
        quiet = _tenant(db)
        _a_busy_day(db, busy)
        now = _at(DAY + timedelta(days=2), 12)

        rollup(db, now=now)
        day = compute_day(db, quiet.id, DAY, zone_of(db, quiet.id))
        rows = db.execute(select(MetricsDaily).where(MetricsDaily.tenant_id == quiet.id)).scalars()
        report = tenant_report(db, quiet.id, DAY, DAY, now=now, money=True)

        assert (day.conversations, day.messages_in, day.orders_confirmed, day.tokens_in) == (
            0,
            0,
            0,
            0,
        )
        assert all(r.conversations == 0 and r.sales == {} for r in rows)
        assert report["total"]["conversations"] == 0 and report["mas_vendidos"] == []
