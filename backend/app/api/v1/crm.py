"""The mini-CRM: pipeline, leads, the day's list, history, CSV and numbers (Feature 012, TASK-1204).

    GET  /tenant/pipeline                       stages (with counts) and lost reasons
    PUT  /tenant/pipeline/stages                rename, switch, add or remove (managers)
    PUT  /tenant/pipeline/lost-reasons          the reasons a lead is lost (managers)
    GET  /tenant/leads                          filtered, paged list
    GET  /tenant/leads/board                    every active stage with its first cards
    GET  /tenant/leads/today                    leads with no activity for N days
    GET  /tenant/leads/metrics                  conversion, pipeline value, won and lost
    GET  /tenant/leads/export.csv               the pipeline as a file
    GET  /tenant/leads/{id}                     one lead with its history and notes
    POST /tenant/leads/{id}/move | assign | discard | notes

Every member works the board; configuring it is for managers. An operator never sees money
(C-106). Human changes publish `lead.updated` on the tenant's channel after the commit.
"""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Query, Response, status
from pydantic import BaseModel, Field, StringConstraints

from app.api.deps import ConfigurationManagerDep, MemberDep, Principal, SessionDep
from app.api.errors import ApiError, error_responses
from app.core.events import publish
from app.models.channel import Contact
from app.models.crm import MAX_STAGES, Lead, LostReason, PipelineStage
from app.models.inbox import ConversationNote
from app.models.user import User
from app.repositories.tenant import TenantUserRepository
from app.services.crm import CrmError, LeadService, free_key
from app.services.crm_report import contact_name, export_csv, pipeline_metrics
from app.services.inbox import Inbox, InboxError

router = APIRouter(tags=["crm"])

AUTH = (
    (401, "`not_authenticated`, `token_expired` or `invalid_token`"),
    (403, "`email_not_verified`, `no_active_tenant` or `tenant_access_denied`"),
)
Label = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=40)]
ReasonLabel = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=60)]
CLOSED_ON_BOARD_DAYS = 30
BOARD_PAGE = 50


class StageOut(BaseModel):
    id: uuid.UUID
    key: str
    label: str
    semantic: str | None
    is_active: bool
    leads: int


class ReasonOut(BaseModel):
    key: str
    label: str
    is_active: bool


class PipelineOut(BaseModel):
    stages: list[StageOut]
    lost_reasons: list[ReasonOut]
    max_stages: int = MAX_STAGES


class StageIn(BaseModel):
    key: str | None = Field(default=None, max_length=40)
    label: Label
    is_active: bool = True


class ReasonIn(BaseModel):
    key: str | None = Field(default=None, max_length=40)
    label: ReasonLabel
    is_active: bool = True


class QuoteOut(BaseModel):
    """The quote the customer asked for (feature 019): what was collected and where it stands."""

    estado: str
    resumen: str | None
    datos: dict[str, str]
    solicitada_en: datetime | None
    enviada_en: datetime | None
    # Days since the quote was sent, to follow up; None until it is sent.
    dias_desde_envio: int | None


class LeadOut(BaseModel):
    id: uuid.UUID
    stage_id: uuid.UUID
    stage_key: str
    status: str
    contact_id: uuid.UUID
    nombre: str | None
    telefono: str | None
    conversation_id: uuid.UUID | None
    assigned_to: uuid.UUID | None
    assigned_name: str | None
    product_interest: str | None
    origen: str | None
    lost_reason: str | None
    moneda: str | None
    # Null for an operator (C-106).
    amount_estimated: Decimal | None
    amount_real: Decimal | None
    days_in_stage: int
    days_idle: int
    moved_by_person: bool
    created_at: datetime
    last_activity_at: datetime
    closed_at: datetime | None
    cotizacion: QuoteOut | None = None


class LeadPage(BaseModel):
    items: list[LeadOut]
    total: int
    limit: int
    offset: int


class BoardColumn(BaseModel):
    stage: StageOut
    items: list[LeadOut]
    total: int


class BoardOut(BaseModel):
    columns: list[BoardColumn]


class EventOut(BaseModel):
    actor: str
    actor_name: str | None
    from_stage: str | None
    to_stage: str | None
    cause: str
    detail: dict[str, Any]
    created_at: datetime


class NoteOut(BaseModel):
    id: uuid.UUID
    author: str | None
    content: str
    created_at: datetime


class LeadDetail(BaseModel):
    lead: LeadOut
    history: list[EventOut]
    notes: list[NoteOut]


class MoveIn(BaseModel):
    stage_id: uuid.UUID
    lost_reason: str | None = Field(default=None, max_length=40)


class QuoteSentIn(BaseModel):
    # What the quote came to, if the person wants it on the lead. Never read by an operator (C-106).
    monto: Decimal | None = Field(default=None, ge=0, max_digits=12, decimal_places=2)
    moneda: Literal["PEN", "USD"] | None = None


class AssignIn(BaseModel):
    user_id: uuid.UUID | None = None


class NoteIn(BaseModel):
    content: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=2000)]


def _error(exc: CrmError) -> ApiError:
    return ApiError(status_code=422, error=exc.code, message=str(exc))


def _not_found() -> ApiError:
    return ApiError(status_code=404, error="not_found", message="Not found.")


def _money(principal: Principal) -> bool:
    return principal.role != "operator"


def _names(session: SessionDep, ids: set[uuid.UUID]) -> dict[uuid.UUID, str]:
    if not ids:
        return {}
    from sqlalchemy import select

    return {
        row.id: row.full_name
        for row in session.execute(select(User).where(User.id.in_(list(ids)))).scalars()
    }


def _out(
    lead: Lead,
    contact: Contact,
    stages: dict[uuid.UUID, PipelineStage],
    names: dict[uuid.UUID, str],
    *,
    money: bool,
    now: datetime,
) -> LeadOut:
    stage = stages.get(lead.stage_id)
    return LeadOut(
        id=lead.id,
        stage_id=lead.stage_id,
        stage_key=stage.key if stage else "",
        status=lead.status,
        contact_id=contact.id,
        nombre=contact_name(contact),
        telefono=contact.phone_e164,
        conversation_id=lead.conversation_id,
        assigned_to=lead.assigned_to,
        assigned_name=names.get(lead.assigned_to) if lead.assigned_to else None,
        product_interest=lead.product_interest,
        origen=lead.origen,
        lost_reason=lead.lost_reason,
        moneda=lead.moneda if money else None,
        amount_estimated=lead.amount_estimated if money else None,
        amount_real=lead.amount_real if money else None,
        days_in_stage=max(0, (now - lead.stage_entered_at).days),
        days_idle=max(0, (now - lead.last_activity_at).days),
        moved_by_person=lead.moved_by_person,
        created_at=lead.created_at,
        last_activity_at=lead.last_activity_at,
        closed_at=lead.closed_at,
        cotizacion=_quote(lead, now) if lead.quote_status else None,
    )


def _quote(lead: Lead, now: datetime) -> QuoteOut:
    return QuoteOut(
        estado=str(lead.quote_status),
        resumen=lead.quote_summary,
        datos={str(k): str(v) for k, v in (lead.quote_data or {}).items()},
        solicitada_en=lead.quote_requested_at,
        enviada_en=lead.quote_sent_at,
        dias_desde_envio=max(0, (now - lead.quote_sent_at).days) if lead.quote_sent_at else None,
    )


def _outs(
    session: SessionDep, service: LeadService, rows: list[tuple[Lead, Contact]], money: bool
) -> list[LeadOut]:
    stages = {s.id: s for s in service.stages.ordered()}
    names = _names(session, {lead.assigned_to for lead, _ in rows if lead.assigned_to})
    now = datetime.now(UTC)
    return [_out(lead, contact, stages, names, money=money, now=now) for lead, contact in rows]


def _pipeline(service: LeadService) -> PipelineOut:
    board = service.board()
    counts: dict[uuid.UUID, int] = {}
    for stage in board.stages:
        counts[stage.id] = service.leads.count(Lead.stage_id == stage.id, Lead.status == "active")
    return PipelineOut(
        stages=[
            StageOut(
                id=s.id,
                key=s.key,
                label=s.label,
                semantic=s.semantic,
                is_active=s.is_active,
                leads=counts.get(s.id, 0),
            )
            for s in board.stages
        ],
        lost_reasons=[
            ReasonOut(key=r.key, label=r.label, is_active=r.is_active)
            for r in service.reasons.ordered()
        ],
    )


def _lead(service: LeadService, lead_id: uuid.UUID) -> Lead:
    lead = service.leads.get(lead_id)
    if lead is None:
        raise _not_found()
    return lead


def _changed(tenant_id: uuid.UUID, lead: Lead) -> None:
    publish(tenant_id, "lead.updated", lead_id=lead.id)


# ---------------------------------------------------------------- pipeline


@router.get("/tenant/pipeline", responses=error_responses(*AUTH))
def get_pipeline(principal: MemberDep, session: SessionDep) -> PipelineOut:
    service = LeadService(session, principal.tenant_id)
    out = _pipeline(service)
    session.commit()  # the defaults, the first time
    return out


@router.put(
    "/tenant/pipeline/stages",
    responses=error_responses(*AUTH, (422, "`fixed_stages`, `too_many_stages`, `stage_in_use`")),
)
def put_stages(
    body: list[StageIn], principal: ConfigurationManagerDep, session: SessionDep
) -> PipelineOut:
    service = LeadService(session, principal.tenant_id)
    try:
        service.save_stages([item.model_dump() for item in body])
    except CrmError as exc:
        raise _error(exc) from exc
    session.commit()
    return _pipeline(service)


@router.put("/tenant/pipeline/lost-reasons", responses=error_responses(*AUTH))
def put_reasons(
    body: list[ReasonIn], principal: ConfigurationManagerDep, session: SessionDep
) -> PipelineOut:
    """Rename, switch or add reasons. Removing is switching off: past leads keep theirs."""
    service = LeadService(session, principal.tenant_id)
    service.board()
    existing = {r.key: r for r in service.reasons.ordered()}
    for item in body:
        key = item.key if item.key in existing else None
        if key is None:
            key = free_key(item.label, set(existing), fallback="motivo")
            row = LostReason(tenant_id=principal.tenant_id, key=key, label=item.label)
            session.add(row)
            existing[key] = row
        existing[key].label = item.label
        existing[key].is_active = item.is_active
    session.commit()
    return _pipeline(service)


# ------------------------------------------------------------------- leads


Status = Literal["active", "won", "lost", "discarded"]


@router.get("/tenant/leads", responses=error_responses(*AUTH))
def list_leads(
    principal: MemberDep,
    session: SessionDep,
    stage_id: uuid.UUID | None = None,
    estado: Annotated[list[Status] | None, Query()] = None,
    asignado: Annotated[str | None, Query(pattern=r"^(me|nadie|[0-9a-f-]{36})$")] = None,
    search: Annotated[str | None, Query(max_length=100)] = None,
    sin_actividad_dias: Annotated[int | None, Query(ge=1, le=365)] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> LeadPage:
    service = LeadService(session, principal.tenant_id)
    service.board()
    assigned = (
        principal.user_id
        if asignado == "me"
        else (uuid.UUID(asignado) if asignado and asignado != "nadie" else None)
    )
    rows, total = service.leads.board(
        stage_id=stage_id,
        status=estado or ["active"],
        assigned_to=assigned,
        unassigned=asignado == "nadie",
        search=search,
        idle_before=(
            datetime.now(UTC) - timedelta(days=sin_actividad_dias) if sin_actividad_dias else None
        ),
        limit=limit,
        offset=offset,
    )
    return LeadPage(
        items=_outs(session, service, rows, _money(principal)),
        total=total,
        limit=limit,
        offset=offset,
    )


@router.get("/tenant/leads/board", responses=error_responses(*AUTH))
def board(
    principal: MemberDep,
    session: SessionDep,
    asignado: Annotated[str | None, Query(pattern=r"^(me|nadie|[0-9a-f-]{36})$")] = None,
    search: Annotated[str | None, Query(max_length=100)] = None,
) -> BoardOut:
    """The kanban: every active stage with its first 50 cards. Won and lost show the last 30 days
    (C-005: a working tool, not an archive)."""
    service = LeadService(session, principal.tenant_id)
    stages = [s for s in service.board().stages if s.is_active]
    session.commit()
    assigned = (
        principal.user_id
        if asignado == "me"
        else (uuid.UUID(asignado) if asignado and asignado != "nadie" else None)
    )
    since = datetime.now(UTC) - timedelta(days=CLOSED_ON_BOARD_DAYS)
    columns: list[BoardColumn] = []
    for stage in stages:
        closed = {"ganado": "won", "perdido": "lost"}.get(stage.semantic or "")
        rows, total = service.leads.board(
            stage_id=stage.id,
            status=[closed] if closed else ["active"],
            assigned_to=assigned,
            unassigned=asignado == "nadie",
            search=search,
            closed_since=since if closed else None,
            limit=BOARD_PAGE,
        )
        columns.append(
            BoardColumn(
                stage=StageOut(
                    id=stage.id,
                    key=stage.key,
                    label=stage.label,
                    semantic=stage.semantic,
                    is_active=True,
                    leads=total,
                ),
                items=_outs(session, service, rows, _money(principal)),
                total=total,
            )
        )
    return BoardOut(columns=columns)


@router.get("/tenant/leads/today", responses=error_responses(*AUTH))
def today(
    principal: MemberDep,
    session: SessionDep,
    dias: Annotated[int, Query(ge=1, le=60)] = 3,
) -> LeadPage:
    """The day's list (HU-04): active leads with no activity for `dias` days, oldest first."""
    service = LeadService(session, principal.tenant_id)
    service.board()
    rows, total = service.leads.board(
        idle_before=datetime.now(UTC) - timedelta(days=dias), limit=200
    )
    rows.sort(key=lambda row: row[0].last_activity_at)
    return LeadPage(
        items=_outs(session, service, rows, _money(principal)), total=total, limit=200, offset=0
    )


@router.get("/tenant/leads/metrics", responses=error_responses(*AUTH))
def metrics(
    principal: MemberDep,
    session: SessionDep,
    desde: date | None = None,
    hasta: date | None = None,
) -> dict[str, Any]:
    end_day = hasta or datetime.now(UTC).date()
    start_day = desde or end_day - timedelta(days=29)
    if (end_day - start_day).days > 366 or end_day < start_day:
        raise ApiError(status_code=400, error="range_too_long", message="Elige hasta un año.")
    LeadService(session, principal.tenant_id).board()
    session.commit()
    return pipeline_metrics(
        session,
        principal.tenant_id,
        datetime.combine(start_day, time.min, tzinfo=UTC),
        datetime.combine(end_day + timedelta(days=1), time.min, tzinfo=UTC),
        money=_money(principal),
    )


@router.get(
    "/tenant/leads/export.csv",
    response_class=Response,
    responses={200: {"content": {"text/csv": {}}}},
)
def export(principal: MemberDep, session: SessionDep) -> Response:
    service = LeadService(session, principal.tenant_id)
    stages = {s.id: s for s in service.board().stages}
    rows, _ = service.leads.board(status=["active", "won", "lost"], limit=10_000)
    reasons = {r.key: r.label for r in service.reasons.ordered()}
    body = export_csv(rows, stages, reasons, money=_money(principal))
    stamp = datetime.now(UTC).date().isoformat()
    return Response(
        content=body,
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="leo-leads-{stamp}.csv"'},
    )


@router.get("/tenant/leads/{lead_id}", responses=error_responses(*AUTH, (404, "`not_found`")))
def detail(lead_id: uuid.UUID, principal: MemberDep, session: SessionDep) -> LeadDetail:
    service = LeadService(session, principal.tenant_id)
    lead = _lead(service, lead_id)
    contact = session.get(Contact, lead.contact_id)
    if contact is None or contact.tenant_id != principal.tenant_id:  # pragma: no cover
        raise _not_found()
    events = service.events.for_lead(lead.id)
    notes: list[ConversationNote] = []
    if lead.conversation_id is not None:
        try:
            notes = Inbox(session, principal.tenant_id).notes(lead.conversation_id)
        except InboxError:
            notes = []
    names = _names(
        session,
        {e.actor_user_id for e in events if e.actor_user_id}
        | {n.author_id for n in notes}
        | ({lead.assigned_to} if lead.assigned_to else set()),
    )
    stages = {s.id: s for s in service.stages.ordered()}
    return LeadDetail(
        lead=_out(lead, contact, stages, names, money=_money(principal), now=datetime.now(UTC)),
        history=[
            EventOut(
                actor=e.actor,
                actor_name=names.get(e.actor_user_id) if e.actor_user_id else None,
                from_stage=e.from_stage,
                to_stage=e.to_stage,
                cause=e.cause,
                detail=e.detail,
                created_at=e.created_at,
            )
            for e in events
        ],
        notes=[
            NoteOut(
                id=n.id, author=names.get(n.author_id), content=n.content, created_at=n.created_at
            )
            for n in notes
        ],
    )


@router.post(
    "/tenant/leads/{lead_id}/move",
    responses=error_responses(
        *AUTH, (404, "`not_found`"), (422, "`unknown_stage`, `unknown_reason`, `another_active`")
    ),
)
def move(lead_id: uuid.UUID, body: MoveIn, principal: MemberDep, session: SessionDep) -> LeadDetail:
    service = LeadService(session, principal.tenant_id)
    lead = _lead(service, lead_id)
    try:
        service.move(lead, body.stage_id, by=principal.user_id, lost_reason=body.lost_reason)
    except CrmError as exc:
        raise _error(exc) from exc
    session.commit()
    _changed(principal.tenant_id, lead)
    return detail(lead_id, principal, session)


@router.post(
    "/tenant/leads/{lead_id}/assign",
    responses=error_responses(*AUTH, (404, "`not_found`"), (422, "`unknown_member`")),
)
def assign(
    lead_id: uuid.UUID, body: AssignIn, principal: MemberDep, session: SessionDep
) -> LeadDetail:
    service = LeadService(session, principal.tenant_id)
    lead = _lead(service, lead_id)
    if (
        body.user_id is not None
        and TenantUserRepository(session, principal.tenant_id).membership_for(body.user_id) is None
    ):
        raise ApiError(
            status_code=422, error="unknown_member", message="Esa persona no es de tu equipo."
        )
    service.assign(lead, body.user_id, by=principal.user_id)
    session.commit()
    _changed(principal.tenant_id, lead)
    if body.user_id is not None and body.user_id != principal.user_id:
        publish(principal.tenant_id, "lead.assigned", lead_id=lead.id, user_id=body.user_id)
    return detail(lead_id, principal, session)


@router.post(
    "/tenant/leads/{lead_id}/quote/sent",
    responses=error_responses(*AUTH, (404, "`not_found`"), (422, "`no_quote`")),
)
def quote_sent(
    lead_id: uuid.UUID, body: QuoteSentIn, principal: MemberDep, session: SessionDep
) -> LeadDetail:
    """A person sent the quote the agent handed over: it becomes `enviada` and starts counting."""
    service = LeadService(session, principal.tenant_id)
    lead = _lead(service, lead_id)
    try:
        service.quote_sent(
            lead,
            by=principal.user_id,
            amount=body.monto if _money(principal) else None,
            moneda=body.moneda,
        )
    except CrmError as exc:
        raise _error(exc) from exc
    session.commit()
    _changed(principal.tenant_id, lead)
    return detail(lead_id, principal, session)


@router.post(
    "/tenant/leads/{lead_id}/discard",
    status_code=status.HTTP_204_NO_CONTENT,
    responses=error_responses(*AUTH, (404, "`not_found`")),
)
def discard(lead_id: uuid.UUID, principal: MemberDep, session: SessionDep) -> Response:
    service = LeadService(session, principal.tenant_id)
    lead = _lead(service, lead_id)
    service.discard(lead, by=principal.user_id)
    session.commit()
    _changed(principal.tenant_id, lead)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post(
    "/tenant/leads/{lead_id}/notes",
    status_code=status.HTTP_201_CREATED,
    responses=error_responses(*AUTH, (404, "`not_found`"), (422, "`no_conversation`")),
)
def note(lead_id: uuid.UUID, body: NoteIn, principal: MemberDep, session: SessionDep) -> NoteOut:
    """A note on the lead's conversation: the same notes the inbox shows (F007)."""
    service = LeadService(session, principal.tenant_id)
    lead = _lead(service, lead_id)
    if lead.conversation_id is None:
        raise ApiError(
            status_code=422, error="no_conversation", message="Este lead no tiene conversación."
        )
    written = Inbox(session, principal.tenant_id).note(
        lead.conversation_id, principal.user, body.content
    )
    session.commit()
    _changed(principal.tenant_id, lead)
    return NoteOut(
        id=written.id,
        author=principal.user.full_name,
        content=written.content,
        created_at=written.created_at,
    )
