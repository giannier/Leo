"""What the business thinks of Leo: NPS, once per cycle (Feature 013, C-107).

The first cycle starts 30 days after the business signed up, then every 90 days. Owners and
admins see the card once per cycle; closing it without answering counts as an answer for that
cycle. Only the platform's operator sees the results.
"""

from __future__ import annotations

import uuid
from collections import defaultdict
from datetime import date, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.models.analytics import NpsResponse
from app.models.business import BusinessProfile
from app.models.tenant import Tenant

FIRST_AFTER = 30
EVERY = 90
ASKS = ("owner", "tenant_admin")


def cycle_for(signed_up: date, today: date) -> date | None:
    days = (today - signed_up).days
    if days < FIRST_AFTER:
        return None
    return signed_up + timedelta(days=FIRST_AFTER + EVERY * ((days - FIRST_AFTER) // EVERY))


def due(
    session: Session, tenant_id: uuid.UUID, user_id: uuid.UUID, role: str, today: date
) -> date | None:
    """The cycle to ask about, or None."""
    if role not in ASKS:
        return None
    tenant = session.get(Tenant, tenant_id)
    if tenant is None or tenant.es_prueba:
        return None
    cycle = cycle_for(tenant.created_at.date(), today)
    if cycle is None:
        return None
    seen = session.execute(
        select(NpsResponse.id).where(
            NpsResponse.tenant_id == tenant_id,
            NpsResponse.user_id == user_id,
            NpsResponse.cycle == cycle,
        )
    ).scalar_one_or_none()
    return None if seen is not None else cycle


def record(
    session: Session,
    tenant_id: uuid.UUID,
    user_id: uuid.UUID,
    cycle: date,
    *,
    score: int | None,
    comment: str | None,
) -> None:
    session.execute(
        insert(NpsResponse)
        .values(
            tenant_id=tenant_id,
            user_id=user_id,
            cycle=cycle,
            score=score,
            comment=(comment or "").strip()[:1000] or None,
            dismissed=score is None,
        )
        .on_conflict_do_nothing(constraint="uq_nps_responses_tenant_id_user_id_cycle")
    )
    session.flush()


def nps(scores: list[int]) -> int | None:
    if not scores:
        return None
    promoters = sum(1 for s in scores if s >= 9)
    detractors = sum(1 for s in scores if s <= 6)
    return round(100 * (promoters - detractors) / len(scores))


def platform_summary(session: Session, *, today: date) -> dict[str, Any]:
    rows = session.execute(
        select(
            NpsResponse, Tenant.plan_id, Tenant.created_at, Tenant.name, BusinessProfile.industry
        )
        .join(Tenant, Tenant.id == NpsResponse.tenant_id)
        .outerjoin(BusinessProfile, BusinessProfile.tenant_id == NpsResponse.tenant_id)
        .order_by(NpsResponse.created_at.desc())
    ).all()
    answered = [r for r in rows if r[0].score is not None]
    by: dict[str, dict[str, list[int]]] = {
        "plan": defaultdict(list),
        "rubro": defaultdict(list),
        "antiguedad": defaultdict(list),
    }
    for response, plan, created_at, _name, industry in answered:
        months = (today - created_at.date()).days // 30
        age = "1-3 meses" if months < 3 else "3-12 meses" if months < 12 else "más de 1 año"
        assert response.score is not None
        by["plan"][plan].append(response.score)
        by["rubro"][industry or "sin rubro"].append(response.score)
        by["antiguedad"][age].append(response.score)
    return {
        "nps": nps([r[0].score for r in answered if r[0].score is not None]),
        "respuestas": len(answered),
        "descartadas": len(rows) - len(answered),
        "segmentos": {
            key: {
                name: {"nps": nps(values), "respuestas": len(values)}
                for name, values in groups.items()
            }
            for key, groups in by.items()
        },
        "comentarios": [
            {
                "negocio": name,
                "nota": response.score,
                "comentario": response.comment,
                "fecha": response.created_at.isoformat(),
            }
            for response, _plan, _created, name, _industry in answered
            if response.comment
        ][:30],
    }
