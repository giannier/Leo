"""Run the evals of a catalogue draft (017, TASK-466). Enqueued by the platform panel."""

from __future__ import annotations

import logging
import uuid
from typing import Any

from app.models.catalog import CatalogVersion
from app.services.catalog_evals import EVALS_JOB, run_evals

logger = logging.getLogger(__name__)

__all__ = ["EVALS_JOB", "run_catalog_evals"]


async def run_catalog_evals(ctx: dict[str, Any], version_id: str) -> bool:
    """ARQ entry point. Stores the result on the version; returns whether it passed."""
    with ctx["session_factory"]() as session:
        version = session.get(CatalogVersion, uuid.UUID(version_id))
        if version is None or version.estado != "borrador":
            return False
        run = await run_evals(session, version, ctx.get("llm"))
        session.commit()
    logger.info("catalog evals", extra={"version_id": version_id, "ok": run.ok})
    return run.ok
