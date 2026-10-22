"""The daily sweep of what the assistant could learn (017, phase 3).

Cross-tenant only to find which businesses turned learning on; every business is then worked on
through its own scoped repositories. One business failing never stops the others. The order is the
order of value for the ceiling of the week: the questions (free), then what a person did better,
the weekly look at lost sales (Mondays) and, last, the sales that can become examples.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select

from app.learning.questions import suggest_from_questions
from app.learning.sources import from_lost_sales, from_sales, from_takeovers
from app.models.learning import LearningSettings

logger = logging.getLogger(__name__)

SWEEP_JOB = "learning_sweep"
MONDAY = 0


async def learning_sweep(ctx: dict[str, Any], now: datetime | None = None) -> dict[str, int]:
    """ARQ entry point (daily cron)."""
    moment = now or datetime.now(UTC)
    llm = ctx["llm"]
    created = {"preguntas": 0, "toma_de_control": 0, "venta_perdida": 0, "venta_confirmada": 0}
    with ctx["session_factory"]() as session:
        tenants = list(
            session.execute(
                select(LearningSettings.tenant_id).where(LearningSettings.activo)
            ).scalars()
        )
        for tenant_id in tenants:
            try:
                created["preguntas"] += suggest_from_questions(session, tenant_id)
                created["toma_de_control"] += await from_takeovers(
                    session, tenant_id, llm, now=moment
                )
                if moment.weekday() == MONDAY:
                    created["venta_perdida"] += await from_lost_sales(
                        session, tenant_id, llm, now=moment
                    )
                created["venta_confirmada"] += await from_sales(session, tenant_id, llm, now=moment)
                session.commit()
            except Exception:  # noqa: BLE001 - one business must not stop the sweep
                session.rollback()
                logger.exception("the learning sweep failed", extra={"tenant_id": str(tenant_id)})
    return created
