"""Embed the rubro FAQs and objections of every published version (017, TASK-461).

Hourly, at worker startup, and enqueued by name (`INDEX_CATALOG_JOB`) when the
platform publishes a rubro version (TASK-466). Cheap: a few dozen short texts per
version, embedded once per embedding model.
"""

from __future__ import annotations

import logging
from typing import Any

from app.services.catalog_knowledge import INDEX_CATALOG_JOB, index_published_rubros

logger = logging.getLogger(__name__)

__all__ = ["INDEX_CATALOG_JOB", "index_catalog_knowledge"]


async def index_catalog_knowledge(ctx: dict[str, Any]) -> int:
    """ARQ entry point."""
    with ctx["session_factory"]() as session:
        try:
            done = await index_published_rubros(session, ctx["llm"])
            session.commit()
        except Exception:
            session.rollback()
            logger.exception("catalog knowledge indexing failed")
            raise
    if done:
        logger.info("catalog knowledge indexed", extra={"items": done})
    return done
