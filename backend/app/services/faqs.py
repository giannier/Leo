"""The business's FAQs as knowledge: `consultar_conocimiento` answers from them (017, 018)."""

from __future__ import annotations

import contextlib
import uuid

from sqlalchemy.orm import Session

from app.models.sales_config import Faq
from app.services.knowledge import KnowledgeService, SourceNotFoundError


async def index_faq(session: Session, tenant_id: uuid.UUID, queue: object, faq: Faq) -> None:
    """Index the FAQ as a knowledge source and remember which one (commits)."""
    service = KnowledgeService(session, tenant_id, queue=queue)  # type: ignore[arg-type]
    result = await service.ingest_text(
        source_type="faq", title=faq.pregunta, content=f"{faq.pregunta}\n{faq.respuesta}"
    )
    faq.knowledge_source_id = result.id
    session.commit()


def unindex_faq(session: Session, tenant_id: uuid.UUID, faq: Faq) -> None:
    if faq.knowledge_source_id is None:
        return
    with contextlib.suppress(SourceNotFoundError):
        KnowledgeService(session, tenant_id).delete(faq.knowledge_source_id)
    faq.knowledge_source_id = None
