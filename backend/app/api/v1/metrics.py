"""Dashboard endpoints (Feature 010, TASK-1003).

    GET /tenant/metrics?desde&hasta            the business's report (closed days + today live)
    GET /tenant/metrics/export.csv?desde&hasta one row per day, at most 90 days
    GET /tenant/metrics/settings | PUT         minutes a person spends per conversation
    GET /platform-admin/metrics?desde&hasta    the platform's own numbers
    GET /platform-admin/model-costs | PUT      model prices, versioned (audited)

Every member sees the business's report; an operator gets it without money
(RF-004): no sales, no best sellers, and no CSV with sales either.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query, Response
from pydantic import BaseModel, Field, StringConstraints
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import ConfigurationManagerDep, MemberDep, PlatformAdminDep, get_db
from app.api.errors import ApiError
from app.models.metrics import DashboardSettings, ModelCost
from app.services.admin import record_audit
from app.services.metrics.report import (
    RangeTooLong,
    export_csv,
    minutes_per_conversation,
    platform_report,
    tenant_report,
)

router = APIRouter(tags=["metrics"])
SessionDep = Annotated[Session, Depends(get_db)]
DEFAULT_DAYS = 30


class MetricsSettings(BaseModel):
    minutos_por_conversacion: int = Field(ge=1, le=120)


class ModelCostBody(BaseModel):
    modelo: Annotated[str, StringConstraints(strip_whitespace=True, min_length=2, max_length=100)]
    entrada_por_1k_usd: Decimal = Field(ge=0, max_digits=12, decimal_places=6)
    salida_por_1k_usd: Decimal = Field(ge=0, max_digits=12, decimal_places=6)
    desde: datetime | None = None


class ModelCostOut(BaseModel):
    modelo: str
    entrada_por_1k_usd: Decimal
    salida_por_1k_usd: Decimal
    desde: datetime


def _range(desde: date | None, hasta: date | None) -> tuple[date, date]:
    end = hasta or datetime.now(UTC).date()
    start = desde or end - timedelta(days=DEFAULT_DAYS - 1)
    return start, end


def _too_long(exc: RangeTooLong) -> ApiError:
    return ApiError(status_code=400, error="range_too_long", message=f"Elige un rango de {exc}.")


@router.get("/tenant/metrics")
async def metrics(
    session: SessionDep,
    principal: MemberDep,
    desde: Annotated[date | None, Query()] = None,
    hasta: Annotated[date | None, Query()] = None,
) -> dict[str, Any]:
    start, end = _range(desde, hasta)
    try:
        return tenant_report(
            session,
            principal.tenant_id,
            start,
            end,
            now=datetime.now(UTC),
            money=principal.role != "operator",
        )
    except RangeTooLong as exc:
        raise _too_long(exc) from exc


@router.get(
    "/tenant/metrics/export.csv",
    response_class=Response,
    responses={200: {"content": {"text/csv": {}}}},
)
async def export(
    session: SessionDep,
    principal: MemberDep,
    desde: Annotated[date | None, Query()] = None,
    hasta: Annotated[date | None, Query()] = None,
) -> Response:
    start, end = _range(desde, hasta)
    try:
        body = export_csv(
            session,
            principal.tenant_id,
            start,
            end,
            now=datetime.now(UTC),
            money=principal.role != "operator",
        )
    except RangeTooLong as exc:
        raise _too_long(exc) from exc
    return Response(
        content=body,
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="leo-metricas-{start}-{end}.csv"'},
    )


@router.get("/tenant/metrics/settings", response_model=MetricsSettings)
async def get_settings_(session: SessionDep, principal: MemberDep) -> MetricsSettings:
    return MetricsSettings(
        minutos_por_conversacion=minutes_per_conversation(session, principal.tenant_id)
    )


@router.put("/tenant/metrics/settings", response_model=MetricsSettings)
async def put_settings(
    body: MetricsSettings, session: SessionDep, principal: ConfigurationManagerDep
) -> MetricsSettings:
    row = session.get(DashboardSettings, principal.tenant_id)
    if row is None:
        row = DashboardSettings(tenant_id=principal.tenant_id)
        session.add(row)
    row.minutes_per_conversation = body.minutos_por_conversacion
    session.commit()
    return body


@router.get("/platform-admin/metrics")
async def platform_metrics(
    session: SessionDep,
    admin: PlatformAdminDep,
    desde: Annotated[date | None, Query()] = None,
    hasta: Annotated[date | None, Query()] = None,
) -> dict[str, Any]:
    start, end = _range(desde, hasta)
    return platform_report(session, start, end, now=datetime.now(UTC))


@router.get("/platform-admin/model-costs", response_model=list[ModelCostOut])
async def model_costs(session: SessionDep, admin: PlatformAdminDep) -> list[ModelCostOut]:
    rows = session.execute(
        select(ModelCost).order_by(ModelCost.model, ModelCost.valid_from.desc())
    ).scalars()
    return [
        ModelCostOut(
            modelo=r.model,
            entrada_por_1k_usd=r.input_per_1k_usd,
            salida_por_1k_usd=r.output_per_1k_usd,
            desde=r.valid_from,
        )
        for r in rows
    ]


@router.put("/platform-admin/model-costs", response_model=ModelCostOut)
async def set_model_cost(
    body: ModelCostBody, session: SessionDep, admin: PlatformAdminDep
) -> ModelCostOut:
    """A new price from `desde` on: past traces keep the cost they were written with."""
    since = body.desde or datetime.now(UTC)
    session.add(
        ModelCost(
            model=body.modelo,
            input_per_1k_usd=body.entrada_por_1k_usd,
            output_per_1k_usd=body.salida_por_1k_usd,
            valid_from=since,
            created_by=admin.id,
        )
    )
    record_audit(
        session,
        actor_id=admin.id,
        action="metrics.model_cost_set",
        detail={
            "modelo": body.modelo,
            "entrada": str(body.entrada_por_1k_usd),
            "salida": str(body.salida_por_1k_usd),
            "desde": since.isoformat(),
        },
    )
    session.commit()
    return ModelCostOut(
        modelo=body.modelo,
        entrada_por_1k_usd=body.entrada_por_1k_usd,
        salida_por_1k_usd=body.salida_por_1k_usd,
        desde=since,
    )
