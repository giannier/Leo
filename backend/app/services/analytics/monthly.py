"""The monthly email to the owner (Feature 013, HU-05, C-108).

On the 1st, for the month that ended: conversations, sales, hours saved, satisfaction, the
best sellers and what WhatsApp cost, with a link to the panel. Once per month per business: the
month is claimed with a conditional UPDATE before the email goes (`reporte_ultimo_mes`), so a
second worker or a retry finds it taken. Businesses that turned it off, test accounts and
archived ones get nothing.
"""

from __future__ import annotations

import logging
import uuid
from datetime import UTC, date, datetime, timedelta
from html import escape
from typing import Any

from sqlalchemy import or_, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.models.metrics import DashboardSettings
from app.models.tenant import Tenant
from app.providers.email.base import EmailMessage
from app.services.activation import owner_of
from app.services.analytics.report import products, satisfaction
from app.services.campaigns.spend import whatsapp_spend
from app.services.metrics.report import tenant_report

logger = logging.getLogger(__name__)

MONTHS = (
    "enero",
    "febrero",
    "marzo",
    "abril",
    "mayo",
    "junio",
    "julio",
    "agosto",
    "septiembre",
    "octubre",
    "noviembre",
    "diciembre",
)


def previous_month(today: date) -> tuple[date, date]:
    last = today.replace(day=1) - timedelta(days=1)
    return last.replace(day=1), last


def build(session: Session, tenant_id: uuid.UUID, first: date, last: date) -> dict[str, Any]:
    now = datetime.now(UTC)
    report = tenant_report(session, tenant_id, first, last, now=now, money=True)
    total = report["total"]
    happy = satisfaction(session, tenant_id, first, last, by_person=False)
    sold = products(session, tenant_id, first, last, money=True)
    spend = whatsapp_spend(
        session,
        tenant_id,
        start=datetime(first.year, first.month, first.day, tzinfo=UTC),
        end=datetime(last.year, last.month, last.day, tzinfo=UTC) + timedelta(days=1),
    )
    return {
        "mes": f"{MONTHS[first.month - 1]} {first.year}",
        "conversaciones": total.get("conversations", 0),
        "pedidos": total.get("orders_confirmed", 0),
        "ventas": total.get("ventas", {}),
        "horas_ahorradas": total.get("horas_ahorradas", 0),
        "satisfaccion": happy["promedio"],
        "encuestas": happy["con_nota"],
        "mas_vendidos": [p["producto"] for p in sold["vendidos"][:3]],
        "whatsapp_usd": spend["costo_estimado_usd"],
    }


def render(tenant_name: str, data: dict[str, Any], *, to: str, panel_url: str) -> EmailMessage:
    name = escape(tenant_name)
    sales = " · ".join(f"{cur} {amount}" for cur, amount in data["ventas"].items()) or "—"
    happy = (
        f"{data['satisfaccion']} de 5 ({data['encuestas']} respuestas)"
        if data["satisfaccion"]
        else "—"
    )
    best = ", ".join(escape(p) for p in data["mas_vendidos"]) or "—"
    rows = [
        ("Conversaciones", str(data["conversaciones"])),
        ("Pedidos confirmados", str(data["pedidos"])),
        ("Ventas", escape(sales)),
        ("Horas que te ahorró el asistente", str(data["horas_ahorradas"])),
        ("Satisfacción de tus clientes", escape(happy)),
        ("Lo más vendido", best),
        (
            "WhatsApp (aprox., lo cobra Meta)",
            f"US$ {data['whatsapp_usd']}" if data["whatsapp_usd"] else "—",
        ),
    ]
    table = "".join(
        f"<tr><td style='padding:4px 12px 4px 0;color:#475569'>{label}</td>"
        f"<td style='padding:4px 0'><strong>{value}</strong></td></tr>"
        for label, value in rows
    )
    url = escape(panel_url, quote=True)
    html = (
        f"<p>Hola,</p><p>Así le fue a <strong>{name}</strong> en {escape(data['mes'])}:</p>"
        f'<table>{table}</table><p><a href="{url}">Ver el detalle en Leo</a></p>'
        "<p style='color:#64748b;font-size:12px'>Puedes apagar este correo en Resultados.</p>"
        "<p>El equipo de Leo</p>"
    )
    text = "\n".join(
        [f"Así le fue a {tenant_name} en {data['mes']}:"]
        + [f"- {label}: {value}" for label, value in rows]
        + [f"Detalle: {panel_url}"]
    )
    return EmailMessage(to=(to,), subject=f"Tu mes en Leo: {data['mes']}", html=html, text=text)


def claim(session: Session, tenant_id: uuid.UUID, month: date) -> bool:
    """Take this month's report for this business; False if it is off or already sent."""
    session.execute(insert(DashboardSettings).values(tenant_id=tenant_id).on_conflict_do_nothing())
    result = session.execute(
        update(DashboardSettings)
        .where(
            DashboardSettings.tenant_id == tenant_id,
            DashboardSettings.reporte_mensual.is_(True),
            or_(
                DashboardSettings.reporte_ultimo_mes.is_(None),
                DashboardSettings.reporte_ultimo_mes < month,
            ),
        )
        .values(reporte_ultimo_mes=month)
    )
    return bool(getattr(result, "rowcount", 0))


def due_reports(session: Session, *, today: date) -> list[tuple[uuid.UUID, EmailMessage]]:
    """Claim and write the reports of the month that just ended. Commits nothing."""
    if today.day != 1:
        return []
    first, last = previous_month(today)
    out: list[tuple[uuid.UUID, EmailMessage]] = []
    tenants = session.execute(
        select(Tenant).where(Tenant.status != "archived", Tenant.es_prueba.is_(False))
    ).scalars()
    panel = f"{get_settings().frontend_base_url}/resultados"
    for tenant in list(tenants):
        owner = owner_of(session, tenant.id)
        if owner is None or not claim(session, tenant.id, first):
            continue
        data = build(session, tenant.id, first, last)
        out.append((tenant.id, render(tenant.name, data, to=owner.email, panel_url=panel)))
    return out
