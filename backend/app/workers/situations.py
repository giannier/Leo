"""Embed the "cuándo aplica" of situations (017, TASK-462).

Enqueued by the API after a save (`EMBED_JOB`, with the tenant), and swept every
hour for whatever a lost job left behind. The sweep is the one cross-tenant
read here: it only finds which tenants have pending rows, and each tenant is
then embedded through its own scoped repository.
"""

from __future__ import annotations

import logging
import uuid
from typing import Any

from sqlalchemy import distinct, or_, select

from app.models.learning import ExampleConversation
from app.models.situation import Situation
from app.services.situations import EMBED_JOB, embed_pending

logger = logging.getLogger(__name__)

__all__ = ["EMBED_JOB", "embed_situations"]


async def embed_situations(ctx: dict[str, Any], tenant_id: str | None = None) -> int:
    """ARQ entry point. With no tenant, sweeps every tenant with pending situations."""
    llm = ctx["llm"]
    with ctx["session_factory"]() as session:
        if tenant_id is not None:
            tenants = [uuid.UUID(tenant_id)]
        else:
            lacking_situation = select(distinct(Situation.tenant_id)).where(
                or_(
                    Situation.embedding.is_(None),
                    Situation.embedding_model != llm.embedding_model,
                )
            )
            lacking_example = select(distinct(ExampleConversation.tenant_id)).where(
                or_(
                    ExampleConversation.embedding.is_(None),
                    ExampleConversation.embedding_model != llm.embedding_model,
                )
            )
            tenants = list(session.execute(lacking_situation.union(lacking_example)).scalars())
        done = 0
        for tenant in tenants:
            try:
                done += await embed_pending(session, tenant, llm)
                session.commit()
            except Exception:  # noqa: BLE001 - one tenant must not stop the sweep
                session.rollback()
                logger.exception("could not embed situations", extra={"tenant_id": str(tenant)})
    return done
