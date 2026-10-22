"""What the assistant did besides answering, in a period (019, phase G: results by capability).

One block per thing the agent can do: appointments booked (and how they went), quotes asked for
and sent, customers qualified. Counted live from their own tables over the business's days. A block
that stayed at zero is left out, so a business that does not book appointments never sees "0 citas".
"""

from __future__ import annotations

import uuid
from datetime import date
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.crm import Lead
from app.models.scheduling import Appointment
from app.repositories.aftersale import ReturnRepository
from app.services.metrics.rollup import bounds, zone_of


def what_it_did(session: Session, tenant_id: uuid.UUID, start: date, end: date) -> dict[str, Any]:
    zone = zone_of(session, tenant_id)
    since, _ = bounds(start, zone)
    _, until = bounds(end, zone)
    out: dict[str, Any] = {}

    booked = session.execute(
        select(
            func.count(),
            func.count().filter(Appointment.created_by == "agent"),
        ).where(
            Appointment.tenant_id == tenant_id,
            Appointment.created_at >= since,
            Appointment.created_at < until,
        )
    ).one()
    happened: dict[str, int] = {
        str(status): int(n)
        for status, n in session.execute(
            select(Appointment.status, func.count())
            .where(
                Appointment.tenant_id == tenant_id,
                Appointment.starts_at >= since,
                Appointment.starts_at < until,
                Appointment.status.in_(("completada", "no_asistio")),
            )
            .group_by(Appointment.status)
        ).all()
    }
    if booked[0] or happened:
        out["citas"] = {
            "agendadas": int(booked[0]),
            "por_el_asistente": int(booked[1]),
            "completadas": happened.get("completada", 0),
            "faltas": happened.get("no_asistio", 0),
        }

    asked = session.execute(
        select(func.count()).where(
            Lead.tenant_id == tenant_id,
            Lead.quote_requested_at >= since,
            Lead.quote_requested_at < until,
        )
    ).scalar_one()
    sent = session.execute(
        select(func.count()).where(
            Lead.tenant_id == tenant_id, Lead.quote_sent_at >= since, Lead.quote_sent_at < until
        )
    ).scalar_one()
    if asked or sent:
        out["cotizaciones"] = {"pedidas": int(asked), "enviadas": int(sent)}

    scored = session.execute(
        select(Lead.score, func.count())
        .where(Lead.tenant_id == tenant_id, Lead.scored_at >= since, Lead.scored_at < until)
        .group_by(Lead.score)
    ).all()
    if scored:
        by_score = {str(score): int(n) for score, n in scored}
        out["calificados"] = {
            "total": sum(by_score.values()),
            "calientes": by_score.get("caliente", 0),
            "tibios": by_score.get("tibio", 0),
            "frios": by_score.get("frio", 0),
        }

    returns = ReturnRepository(session, tenant_id).counts_between(since, until)
    if returns:
        out["devoluciones"] = {
            "solicitadas": sum(returns.values()),
            "aprobadas": returns.get("aprobada", 0) + returns.get("resuelta", 0),
            "rechazadas": returns.get("rechazada", 0),
            "pendientes": returns.get("solicitada", 0),
        }
    return out
