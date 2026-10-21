"""Where the leads of a period came from (feature 019, phase F: attribution of origin).

A lead takes the origin of its customer (the link of the web chat, what they said, the ad of a
WhatsApp click): "Instagram", "TikTok", "Anuncio: ..."; without one it is "Directo". Counted by the
day the lead was born in the business's own time, with how many were won, so a network can be
judged by what it sold and not only by how many wrote.
"""

from __future__ import annotations

import uuid
from datetime import date
from typing import Any

from sqlalchemy import case, func, select
from sqlalchemy.orm import Session

from app.models.crm import Lead
from app.services.metrics.rollup import bounds, zone_of

DIRECT = "Directo"
MAX_ORIGINS = 8


def lead_origins(
    session: Session, tenant_id: uuid.UUID, start: date, end: date
) -> list[dict[str, Any]]:
    zone = zone_of(session, tenant_id)
    since, _ = bounds(start, zone)
    _, until = bounds(end, zone)
    origin = func.coalesce(func.nullif(func.btrim(Lead.origen), ""), DIRECT)
    rows = session.execute(
        select(
            origin.label("origen"),
            func.count().label("leads"),
            func.sum(case((Lead.status == "won", 1), else_=0)).label("ganados"),
        )
        .where(Lead.tenant_id == tenant_id, Lead.created_at >= since, Lead.created_at < until)
        .group_by(origin)
        .order_by(func.count().desc(), origin)
        .limit(MAX_ORIGINS)
    ).all()
    return [{"origen": str(o), "leads": int(n), "ganados": int(w or 0)} for o, n, w in rows]
