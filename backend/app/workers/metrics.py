"""`metrics.rollup` and `metrics.purge` (Feature 010, TASK-1002 and TASK-1005), nightly."""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from typing import Any, cast

from sqlalchemy import CursorResult, delete
from sqlalchemy.orm import Session

from app.models.conversation import AgentTrace
from app.models.metrics import MetricsDaily
from app.services.metrics.rollup import rollup

logger = logging.getLogger(__name__)

ROLLUP_JOB = "metrics.rollup"
PURGE_JOB = "metrics.purge"
# C-003: daily metrics are kept 24 months, raw traces 6.
METRICS_RETENTION_DAYS = 730
TRACES_RETENTION_DAYS = 183


def purge(session: Session, *, now: datetime) -> tuple[int, int]:
    """Drop what is past its retention. Returns (metric rows, traces) removed."""
    metrics = cast(
        CursorResult[Any],
        session.execute(
            delete(MetricsDaily).where(
                MetricsDaily.day < (now - timedelta(days=METRICS_RETENTION_DAYS)).date()
            )
        ),
    ).rowcount
    traces = cast(
        CursorResult[Any],
        session.execute(
            delete(AgentTrace).where(
                AgentTrace.created_at < now - timedelta(days=TRACES_RETENTION_DAYS)
            )
        ),
    ).rowcount
    return int(metrics or 0), int(traces or 0)


async def metrics_rollup(ctx: dict[str, Any]) -> int:
    with ctx["session_factory"]() as session:
        written = rollup(session, now=datetime.now(UTC))
        session.commit()
    return written


async def metrics_purge(ctx: dict[str, Any]) -> str:
    with ctx["session_factory"]() as session:
        metrics, traces = purge(session, now=datetime.now(UTC))
        session.commit()
    logger.info("metrics purge", extra={"metrics": metrics, "traces": traces})
    return f"{metrics} métricas, {traces} trazas"
