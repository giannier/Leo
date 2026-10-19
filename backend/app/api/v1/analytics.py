"""Advanced analytics, the satisfaction survey's settings, the monthly report and Leo's NPS
(Feature 013).

* `/tenant/analytics/funnel|products|csat`: any member; an operator sees no money and no
  per-person satisfaction (RF-007, C-105).
* `/tenant/analytics/settings`: the survey (off by default) and the monthly email (on).
* `/tenant/reports/monthly/{yyyymm}`: the email as the owner would get it.
* `/tenant/nps`: whether to show the card, and the answer. `/platform-admin/nps`: the results.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from typing import Annotated, Any

from fastapi import APIRouter, Query, Response, status
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field
from sqlalchemy.dialects.postgresql import insert

from app.api.deps import ConfigurationManagerDep, MemberDep, PlatformAdminDep, SessionDep
from app.api.errors import ApiError, error_responses
from app.models.metrics import DashboardSettings
from app.models.tenant import Tenant
from app.services.analytics import nps
from app.services.analytics.monthly import build, render
from app.services.analytics.report import funnel, products, satisfaction
from app.services.metrics.report import RangeTooLong

router = APIRouter(tags=["analytics"])
AUTH = ((401, "Not authenticated."), (403, "Not allowed in this business."))
DEFAULT_DAYS = 30
SEES_PEOPLE = ("owner", "tenant_admin")


def _range(desde: date | None, hasta: date | None) -> tuple[date, date]:
    end = hasta or datetime.now(UTC).date()
    start = desde or end - timedelta(days=DEFAULT_DAYS - 1)
    return start, end


def _too_long(exc: RangeTooLong) -> ApiError:
    return ApiError(status_code=400, error="range_too_long", message=f"Elige un rango de {exc}.")


Desde = Annotated[date | None, Query()]
Hasta = Annotated[date | None, Query()]


@router.get("/tenant/analytics/funnel", responses=error_responses(*AUTH))
def analytics_funnel(
    principal: MemberDep, session: SessionDep, desde: Desde = None, hasta: Hasta = None
) -> dict[str, Any]:
    try:
        return funnel(session, principal.tenant_id, *_range(desde, hasta))
    except RangeTooLong as exc:
        raise _too_long(exc) from exc


@router.get("/tenant/analytics/products", responses=error_responses(*AUTH))
def analytics_products(
    principal: MemberDep, session: SessionDep, desde: Desde = None, hasta: Hasta = None
) -> dict[str, Any]:
    try:
        return products(
            session, principal.tenant_id, *_range(desde, hasta), money=principal.role != "operator"
        )
    except RangeTooLong as exc:
        raise _too_long(exc) from exc


@router.get("/tenant/analytics/csat", responses=error_responses(*AUTH))
def analytics_csat(
    principal: MemberDep, session: SessionDep, desde: Desde = None, hasta: Hasta = None
) -> dict[str, Any]:
    try:
        return satisfaction(
            session,
            principal.tenant_id,
            *_range(desde, hasta),
            by_person=principal.role in SEES_PEOPLE,
        )
    except RangeTooLong as exc:
        raise _too_long(exc) from exc


class AnalyticsSettings(BaseModel):
    csat_activo: bool
    reporte_mensual: bool


@router.get("/tenant/analytics/settings", response_model=AnalyticsSettings)
def get_analytics_settings(principal: MemberDep, session: SessionDep) -> AnalyticsSettings:
    row = session.get(DashboardSettings, principal.tenant_id)
    if row is None:
        return AnalyticsSettings(csat_activo=False, reporte_mensual=True)
    return AnalyticsSettings(csat_activo=row.csat_activo, reporte_mensual=row.reporte_mensual)


@router.put("/tenant/analytics/settings", response_model=AnalyticsSettings)
def put_analytics_settings(
    body: AnalyticsSettings, principal: ConfigurationManagerDep, session: SessionDep
) -> AnalyticsSettings:
    session.execute(
        insert(DashboardSettings)
        .values(tenant_id=principal.tenant_id, **body.model_dump())
        .on_conflict_do_update(index_elements=["tenant_id"], set_=body.model_dump())
    )
    session.commit()
    return body


@router.get(
    "/tenant/reports/monthly/{month}",
    response_class=HTMLResponse,
    responses=error_responses(*AUTH, (400, "`bad_month`")),
)
def monthly_preview(
    month: str, principal: ConfigurationManagerDep, session: SessionDep
) -> HTMLResponse:
    try:
        first = datetime.strptime(month, "%Y%m").date()
    except ValueError as exc:
        raise ApiError(status_code=400, error="bad_month", message="Usa AAAAMM.") from exc
    last = (first.replace(day=28) + timedelta(days=4)).replace(day=1) - timedelta(days=1)
    tenant = session.get(Tenant, principal.tenant_id)
    data = build(session, principal.tenant_id, first, last)
    message = render(tenant.name if tenant else "", data, to=principal.user.email, panel_url="#")
    return HTMLResponse(message.html)


class NpsState(BaseModel):
    preguntar: bool
    ciclo: date | None = None


class NpsIn(BaseModel):
    score: int | None = Field(default=None, ge=0, le=10)
    comment: str | None = Field(default=None, max_length=1000)


@router.get("/tenant/nps", response_model=NpsState)
def nps_state(principal: MemberDep, session: SessionDep) -> NpsState:
    cycle = nps.due(
        session, principal.tenant_id, principal.user.id, principal.role, datetime.now(UTC).date()
    )
    return NpsState(preguntar=cycle is not None, ciclo=cycle)


@router.post("/tenant/nps", status_code=status.HTTP_204_NO_CONTENT)
def nps_answer(body: NpsIn, principal: MemberDep, session: SessionDep) -> Response:
    """An answer (score) or closing the card (no score); both settle this cycle."""
    cycle = nps.due(
        session, principal.tenant_id, principal.user.id, principal.role, datetime.now(UTC).date()
    )
    if cycle is not None:
        nps.record(
            session,
            principal.tenant_id,
            principal.user.id,
            cycle,
            score=body.score,
            comment=body.comment,
        )
        session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/platform-admin/nps")
def platform_nps(admin: PlatformAdminDep, session: SessionDep) -> dict[str, Any]:
    return nps.platform_summary(session, today=datetime.now(UTC).date())
