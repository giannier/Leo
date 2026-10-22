"""The daily sweep of what the assistant could learn (017, phase 3).

Cross-tenant only to find which businesses turned learning on; every business is then worked on
through its own scoped repositories. One business failing never stops the others.
"""

from __future__ import annotations

import logging
from typing import Any

from sqlalchemy import select

from app.learning.questions import suggest_from_questions
from app.models.learning import LearningSettings

logger = logging.getLogger(__name__)

SWEEP_JOB = "learning_sweep"


async def learning_sweep(ctx: dict[str, Any]) -> dict[str, int]:
    """ARQ entry point (daily cron)."""
    created = {"preguntas": 0}
    with ctx["session_factory"]() as session:
        tenants = list(
            session.execute(
                select(LearningSettings.tenant_id).where(LearningSettings.activo)
            ).scalars()
        )
        for tenant_id in tenants:
            try:
                created["preguntas"] += suggest_from_questions(session, tenant_id)
                session.commit()
            except Exception:  # noqa: BLE001 - one business must not stop the sweep
                session.rollback()
                logger.exception("the learning sweep failed", extra={"tenant_id": str(tenant_id)})
    return created
