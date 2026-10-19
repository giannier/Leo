"""Who a campaign goes to: a declarative filter over the contacts (Feature 011, HU-03).

Every segment requires permission for promotions and no BAJA (RF-009, not switchable).
On top of that, all optional and combined with AND:

* `etiquetas`: has at least one of these tags;
* `etapas`: had a lead in one of these CRM stages (C-104, the sales process);
* `compro`: bought at least once (true) or never (false);
* `producto`: bought it or asked about it (text, case-insensitive);
* `dias_sin_escribir_min` / `_max`: wrote last at least / at most this many days ago.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import Select, and_, exists, func, or_, select
from sqlalchemy.orm import Session

from app.models.campaigns import ContactOptin, ContactTag
from app.models.channel import Contact
from app.models.crm import SEMANTIC_STAGES, Lead, PipelineStage
from app.models.orders import Order, OrderItem
from app.process.facts import CONFIRMED_ORDER_STATES
from app.services.campaigns import CampaignError

MAX_DAYS = 3650


@dataclass(frozen=True)
class Segment:
    etiquetas: list[str] = field(default_factory=list)
    etapas: list[str] = field(default_factory=list)
    compro: bool | None = None
    producto: str | None = None
    dias_sin_escribir_min: int | None = None
    dias_sin_escribir_max: int | None = None

    @classmethod
    def parse(cls, raw: dict[str, Any] | None) -> Segment:
        data = raw or {}
        tags = [str(t).strip().lower() for t in data.get("etiquetas") or [] if str(t).strip()]
        stages = [str(s) for s in data.get("etapas") or []]
        if any(stage not in SEMANTIC_STAGES for stage in stages):
            raise CampaignError("etapa_invalida", "Elige etapas del embudo.")
        compro = data.get("compro")
        if compro is not None and not isinstance(compro, bool):
            raise CampaignError("filtro_invalido", "El filtro de compra no es válido.")
        product = str(data.get("producto") or "").strip()[:80] or None
        days: list[int | None] = []
        for key in ("dias_sin_escribir_min", "dias_sin_escribir_max"):
            value = data.get(key)
            if value is None or value == "":
                days.append(None)
                continue
            try:
                number = int(value)
            except (TypeError, ValueError) as exc:
                raise CampaignError("filtro_invalido", "Los días deben ser un número.") from exc
            if not 0 <= number <= MAX_DAYS:
                raise CampaignError("filtro_invalido", "Los días deben ser entre 0 y 3650.")
            days.append(number)
        if days[0] is not None and days[1] is not None and days[0] > days[1]:
            raise CampaignError("filtro_invalido", "El mínimo de días no puede pasar al máximo.")
        return cls(tags, stages, compro, product, days[0], days[1])

    def as_dict(self) -> dict[str, Any]:
        return {
            "etiquetas": self.etiquetas,
            "etapas": self.etapas,
            "compro": self.compro,
            "producto": self.producto,
            "dias_sin_escribir_min": self.dias_sin_escribir_min,
            "dias_sin_escribir_max": self.dias_sin_escribir_max,
        }


def _bought(tenant_id: uuid.UUID) -> Any:
    return exists().where(
        Order.tenant_id == tenant_id,
        Order.contact_id == Contact.id,
        Order.is_sandbox.is_(False),
        Order.estado.in_(CONFIRMED_ORDER_STATES),
    )


def query(tenant_id: uuid.UUID, segment: Segment, *, now: datetime) -> Select[tuple[uuid.UUID]]:
    """The contact ids, permission and BAJA already applied."""
    statement = (
        select(Contact.id)
        .join(
            ContactOptin,
            and_(ContactOptin.contact_id == Contact.id, ContactOptin.tenant_id == tenant_id),
        )
        .where(
            Contact.tenant_id == tenant_id,
            ContactOptin.status == "granted",
            Contact.opted_out_at.is_(None),
            or_(Contact.phone_e164.is_not(None), Contact.external_user_id.is_not(None)),
        )
    )
    if segment.etiquetas:
        statement = statement.where(
            exists().where(
                ContactTag.tenant_id == tenant_id,
                ContactTag.contact_id == Contact.id,
                ContactTag.tag.in_(segment.etiquetas),
            )
        )
    if segment.etapas:
        statement = statement.where(
            exists().where(
                Lead.tenant_id == tenant_id,
                Lead.contact_id == Contact.id,
                Lead.status != "discarded",
                Lead.stage_id == PipelineStage.id,
                PipelineStage.semantic.in_(segment.etapas),
            )
        )
    if segment.compro is True:
        statement = statement.where(_bought(tenant_id))
    elif segment.compro is False:
        statement = statement.where(~_bought(tenant_id))
    if segment.producto:
        pattern = f"%{segment.producto}%"
        statement = statement.where(
            or_(
                exists().where(
                    Order.tenant_id == tenant_id,
                    Order.contact_id == Contact.id,
                    Order.is_sandbox.is_(False),
                    OrderItem.order_id == Order.id,
                    OrderItem.nombre.ilike(pattern),
                ),
                exists().where(
                    Lead.tenant_id == tenant_id,
                    Lead.contact_id == Contact.id,
                    Lead.product_interest.ilike(pattern),
                ),
            )
        )
    if segment.dias_sin_escribir_min is not None:
        statement = statement.where(
            Contact.last_inbound_at <= now - timedelta(days=segment.dias_sin_escribir_min)
        )
    if segment.dias_sin_escribir_max is not None:
        statement = statement.where(
            Contact.last_inbound_at >= now - timedelta(days=segment.dias_sin_escribir_max)
        )
    return statement.order_by(Contact.created_at, Contact.id)


def count(session: Session, tenant_id: uuid.UUID, segment: Segment, *, now: datetime) -> int:
    inner = query(tenant_id, segment, now=now).order_by(None).subquery()
    return int(session.execute(select(func.count()).select_from(inner)).scalar_one())


def contact_ids(
    session: Session, tenant_id: uuid.UUID, segment: Segment, *, now: datetime, limit: int | None
) -> list[uuid.UUID]:
    statement = query(tenant_id, segment, now=now)
    if limit is not None:
        statement = statement.limit(limit)
    return list(session.execute(statement).scalars())
