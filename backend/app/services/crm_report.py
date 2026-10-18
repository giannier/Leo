"""What the CRM says in numbers, and its CSV (Feature 012, HU-05 and RF-007).

Live queries over `leads` and `lead_events`: the board is a working tool, so its numbers are
read when asked, not rolled up. Money is Decimal and per currency, and is left out entirely for
an operator (C-106).
"""

from __future__ import annotations

import csv
import io
import uuid
from collections import Counter, defaultdict
from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.channel import Contact
from app.models.crm import Lead, LeadEvent, LostReason, PipelineStage


def contact_name(contact: Contact) -> str | None:
    data = contact.custom_data if isinstance(contact.custom_data, dict) else {}
    return data.get("nombres") or contact.profile_name


def pipeline_metrics(
    session: Session, tenant_id: uuid.UUID, start: datetime, end: datetime, *, money: bool
) -> dict[str, Any]:
    stages = list(
        session.execute(
            select(PipelineStage)
            .where(PipelineStage.tenant_id == tenant_id)
            .order_by(PipelineStage.position)
        ).scalars()
    )
    by_id = {s.id: s for s in stages}
    active = list(
        session.execute(
            select(Lead).where(Lead.tenant_id == tenant_id, Lead.status == "active")
        ).scalars()
    )
    created = list(
        session.execute(
            select(Lead).where(
                Lead.tenant_id == tenant_id,
                Lead.created_at >= start,
                Lead.created_at < end,
                Lead.status != "discarded",
            )
        ).scalars()
    )
    closed = list(
        session.execute(
            select(Lead).where(
                Lead.tenant_id == tenant_id,
                Lead.closed_at >= start,
                Lead.closed_at < end,
                Lead.status.in_(["won", "lost"]),
            )
        ).scalars()
    )
    reasons = {
        r.key: r.label
        for r in session.execute(
            select(LostReason).where(LostReason.tenant_id == tenant_id)
        ).scalars()
    }

    per_stage = Counter(by_id[lead.stage_id].key for lead in active if lead.stage_id in by_id)
    won = [lead for lead in closed if lead.status == "won"]
    lost = [lead for lead in closed if lead.status == "lost"]
    seen: dict[str, set[uuid.UUID]] = defaultdict(set)
    if created:
        for lead_id, to_stage in session.execute(
            select(LeadEvent.lead_id, LeadEvent.to_stage).where(
                LeadEvent.tenant_id == tenant_id,
                LeadEvent.lead_id.in_([lead.id for lead in created]),
                LeadEvent.to_stage.is_not(None),
            )
        ).all():
            seen[str(to_stage)].add(lead_id)
    days_to_win = [
        (lead.closed_at - lead.created_at).total_seconds() / 86400
        for lead in won
        if lead.closed_at is not None
    ]

    report: dict[str, Any] = {
        "activos_por_etapa": {s.key: per_stage.get(s.key, 0) for s in stages if s.is_active},
        "nuevos": len(created),
        "ganados": len(won),
        "perdidos": len(lost),
        "perdidos_por_motivo": dict(
            Counter(
                reasons.get(lead.lost_reason or "", lead.lost_reason or "otro") for lead in lost
            )
        ),
        # Of the leads created in the range, how many reached each stage.
        "llegaron_a": {s.key: len(seen.get(s.key, set())) for s in stages},
        "conversion": round(
            len([lead for lead in created if lead.status == "won"]) / len(created), 4
        )
        if created
        else None,
        "dias_promedio_para_ganar": round(sum(days_to_win) / len(days_to_win), 1)
        if days_to_win
        else None,
    }
    if money:
        pipeline: dict[str, Decimal] = defaultdict(Decimal)
        for lead in active:
            stage = by_id.get(lead.stage_id)
            if (
                lead.amount_estimated is not None
                and stage is not None
                and stage.semantic in ("interesado", "por_pagar", None)
            ):
                pipeline[lead.moneda or "PEN"] += lead.amount_estimated
        sold: dict[str, Decimal] = defaultdict(Decimal)
        for lead in won:
            if lead.amount_real is not None:
                sold[lead.moneda or "PEN"] += lead.amount_real
        report["valor_pipeline"] = {k: str(v) for k, v in pipeline.items()}
        report["vendido"] = {k: str(v) for k, v in sold.items()}
    return report


def export_csv(
    rows: list[tuple[Lead, Contact]],
    stages: dict[uuid.UUID, PipelineStage],
    reasons: dict[str, str],
    *,
    money: bool,
) -> str:
    out = io.StringIO()
    writer = csv.writer(out)
    header = ["nombre", "telefono", "etapa", "estado", "producto", "origen", "motivo_perdida"]
    if money:
        header += ["moneda", "monto_estimado", "monto_real"]
    header += ["creado", "ultima_actividad", "cerrado"]
    writer.writerow(header)
    for lead, contact in rows:
        stage = stages.get(lead.stage_id)
        line = [
            _safe(contact_name(contact)),
            _safe(contact.phone_e164),
            stage.label if stage else "",
            lead.status,
            _safe(lead.product_interest),
            _safe(lead.origen),
            reasons.get(lead.lost_reason or "", lead.lost_reason or ""),
        ]
        if money:
            line += [
                lead.moneda or "",
                str(lead.amount_estimated or ""),
                str(lead.amount_real or ""),
            ]
        line += [
            lead.created_at.isoformat(),
            lead.last_activity_at.isoformat(),
            lead.closed_at.isoformat() if lead.closed_at else "",
        ]
        writer.writerow(line)
    return out.getvalue()


def _safe(value: str | None) -> str:
    """A cell a spreadsheet will not run as a formula."""
    text = value or ""
    return f"'{text}" if text[:1] in ("=", "+", "-", "@") else text
