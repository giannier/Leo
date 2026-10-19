"""Campaigns, templates, contacts with permission, and the platform's Meta rates (Feature 011).

* `/tenant/templates`: the business's WhatsApp templates and the library by industry.
* `/tenant/contacts`: who agreed to promotions, their tags, and importing a consented list.
* `/tenant/campaigns`: build, estimate, schedule, pause, resume, cancel, report.
* `/tenant/campaigns/settings`: ask for permission after a sale (C-102) and the template for
  follow-ups outside the window (C-110).
* `/platform-admin/meta-rates` and `/platform-admin/numbers/{id}/daily-limit`: Zentek360's.

Writing is for whoever manages the configuration (it reaches every customer); any member
reads and tags contacts. An operator sees no money (as in Results and the CRM).
"""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, File, Form, Query, Response, UploadFile, status
from pydantic import BaseModel, Field, StringConstraints
from sqlalchemy import func, or_, select

from app.api.deps import (
    ConfigurationManagerDep,
    MemberDep,
    PlatformAdminDep,
    Principal,
    SessionDep,
    WhatsAppProviderDep,
    get_storage_provider,
)
from app.api.errors import ApiError, error_responses
from app.models.campaigns import (
    MAX_TAG_LENGTH,
    WEEKLY_IMPORT_LIMIT,
    Campaign,
    ContactOptin,
    ContactTag,
    MessageTemplate,
    MetaRate,
)
from app.models.channel import Contact
from app.models.whatsapp import WhatsAppNumber
from app.providers.storage.base import StorageProvider
from app.repositories.business import AgentConfigRepository
from app.repositories.campaigns import (
    CampaignRepository,
    ContactTagRepository,
    MessageTemplateRepository,
    OptinImportRepository,
)
from app.repositories.channel import ContactRepository
from app.services.campaigns import CampaignError
from app.services.campaigns.library import library_for
from app.services.campaigns.optins import DECLARATION, OptinService
from app.services.campaigns.report import campaign_report
from app.services.campaigns.segments import Segment
from app.services.campaigns.service import CampaignService
from app.services.campaigns.templates import TemplateService, industry_of, rejection_hint
from app.services.crm_report import contact_name

router = APIRouter(tags=["campaigns"])
AUTH = ((401, "Not authenticated."), (403, "Not allowed in this business."))
CONFLICT = (
    409,
    "`no_editable`, `en_uso`, `ya_enviada`, `plantilla_no_aprobada`, `calidad_baja`, "
    "`limite_plan`, ...",
)
StorageDep = Annotated[StorageProvider, Depends(get_storage_provider)]
Tag = Annotated[
    str, StringConstraints(strip_whitespace=True, to_lower=True, min_length=1, max_length=30)
]


def _error(exc: CampaignError) -> ApiError:
    return ApiError(status_code=exc.status, error=exc.code, message=str(exc))


def _not_found() -> ApiError:
    return ApiError(status_code=404, error="not_found", message="Not found.")


def _now() -> datetime:
    return datetime.now(UTC)


# ------------------------------------------------------------------ templates


class TemplateIn(BaseModel):
    label: str = Field(max_length=60)
    category: Literal["utility", "marketing"]
    body: str = Field(max_length=1024)
    footer: str | None = Field(default=None, max_length=60)
    header_media_asset_id: uuid.UUID | None = None


class TemplateOut(BaseModel):
    id: uuid.UUID
    name: str
    label: str
    category: str
    language: str
    body: str
    footer: str | None
    header_media_asset_id: uuid.UUID | None
    variables: list[str]
    status: str
    rejection_reason: str | None
    rejection_hint: str | None
    library_key: str | None
    submitted_at: datetime | None
    reviewed_at: datetime | None
    created_at: datetime


class LibraryOut(BaseModel):
    key: str
    label: str
    category: str
    body: str


def _template_out(template: MessageTemplate) -> TemplateOut:
    return TemplateOut(
        id=template.id,
        name=template.name,
        label=template.label,
        category=template.category,
        language=template.language,
        body=template.body,
        footer=template.footer,
        header_media_asset_id=template.header_media_asset_id,
        variables=list(template.variables),
        status=template.status,
        rejection_reason=template.rejection_reason,
        rejection_hint=rejection_hint(template.rejection_reason)
        if template.status == "rejected"
        else None,
        library_key=template.library_key,
        submitted_at=template.submitted_at,
        reviewed_at=template.reviewed_at,
        created_at=template.created_at,
    )


def _template(session: Any, principal: Principal, template_id: uuid.UUID) -> MessageTemplate:
    template = MessageTemplateRepository(session, principal.tenant_id).get(template_id)
    if template is None:
        raise _not_found()
    return template


@router.get("/tenant/templates", response_model=list[TemplateOut])
def list_templates(principal: MemberDep, session: SessionDep) -> list[TemplateOut]:
    return [
        _template_out(t) for t in MessageTemplateRepository(session, principal.tenant_id).ordered()
    ]


@router.get("/tenant/templates/library", response_model=list[LibraryOut])
def template_library(principal: MemberDep, session: SessionDep) -> list[LibraryOut]:
    industry = industry_of(session, principal.tenant_id)
    return [
        LibraryOut(key=e.key, label=e.label, category=e.category, body=e.body)
        for e in library_for(industry)
    ]


@router.post(
    "/tenant/templates/library/{key}/clone",
    response_model=TemplateOut,
    status_code=status.HTTP_201_CREATED,
    responses=error_responses(*AUTH, (404, "`not_found`")),
)
def clone_template(
    key: str, principal: ConfigurationManagerDep, session: SessionDep
) -> TemplateOut:
    try:
        template = TemplateService(session, principal.tenant_id).clone(key)
    except CampaignError as exc:
        raise _error(exc) from exc
    session.commit()
    return _template_out(template)


@router.post(
    "/tenant/templates",
    response_model=TemplateOut,
    status_code=status.HTTP_201_CREATED,
    responses=error_responses(
        *AUTH, (422, "Validation: `nombre_invalido`, `variable_desconocida`, `limite_semanal`, ...")
    ),
)
def create_template(
    body: TemplateIn, principal: ConfigurationManagerDep, session: SessionDep
) -> TemplateOut:
    try:
        template = TemplateService(session, principal.tenant_id).create(body.model_dump())
    except CampaignError as exc:
        raise _error(exc) from exc
    session.commit()
    return _template_out(template)


@router.put(
    "/tenant/templates/{template_id}",
    response_model=TemplateOut,
    responses=error_responses(
        *AUTH,
        (404, "`not_found`"),
        CONFLICT,
        (422, "Validation: `nombre_invalido`, `variable_desconocida`, `limite_semanal`, ..."),
    ),
)
def update_template(
    template_id: uuid.UUID,
    body: TemplateIn,
    principal: ConfigurationManagerDep,
    session: SessionDep,
) -> TemplateOut:
    template = _template(session, principal, template_id)
    try:
        TemplateService(session, principal.tenant_id).update(template, body.model_dump())
    except CampaignError as exc:
        raise _error(exc) from exc
    session.commit()
    return _template_out(template)


@router.post(
    "/tenant/templates/{template_id}/duplicate",
    response_model=TemplateOut,
    status_code=status.HTTP_201_CREATED,
    responses=error_responses(*AUTH, (404, "`not_found`")),
)
def duplicate_template(
    template_id: uuid.UUID, principal: ConfigurationManagerDep, session: SessionDep
) -> TemplateOut:
    template = _template(session, principal, template_id)
    try:
        copy = TemplateService(session, principal.tenant_id).duplicate(template)
    except CampaignError as exc:
        raise _error(exc) from exc
    session.commit()
    return _template_out(copy)


@router.delete(
    "/tenant/templates/{template_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    responses=error_responses(
        *AUTH,
        (404, "`not_found`"),
        CONFLICT,
    ),
)
def delete_template(
    template_id: uuid.UUID, principal: ConfigurationManagerDep, session: SessionDep
) -> Response:
    template = _template(session, principal, template_id)
    try:
        TemplateService(session, principal.tenant_id).delete(template)
    except CampaignError as exc:
        raise _error(exc) from exc
    session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post(
    "/tenant/templates/{template_id}/submit",
    response_model=TemplateOut,
    responses=error_responses(
        *AUTH,
        (404, "`not_found`"),
        CONFLICT,
        (502, "`meta_rechazo`: WhatsApp did not take the template"),
    ),
)
async def submit_template(
    template_id: uuid.UUID,
    principal: ConfigurationManagerDep,
    session: SessionDep,
    whatsapp: WhatsAppProviderDep,
    storage: StorageDep,
) -> TemplateOut:
    template = _template(session, principal, template_id)
    try:
        await TemplateService(session, principal.tenant_id).submit(
            template, provider=whatsapp, storage=storage
        )
    except CampaignError as exc:
        session.rollback()
        raise _error(exc) from exc
    session.commit()
    return _template_out(template)


# ------------------------------------------------------------------- contacts

PermissionFilter = Literal["granted", "asked", "declined", "revoked", "none"]


class ContactOut(BaseModel):
    id: uuid.UUID
    nombre: str | None
    telefono: str | None
    permiso: str | None
    permiso_origen: str | None
    baja: bool
    etiquetas: list[str]
    ultimo_mensaje: datetime | None


class ContactPage(BaseModel):
    items: list[ContactOut]
    total: int
    con_permiso: int


class TagCount(BaseModel):
    tag: str
    total: int


class TagsIn(BaseModel):
    tags: list[Tag] = Field(default_factory=list, max_length=10)


class ImportInfo(BaseModel):
    declaracion: str
    limite_semanal: int
    disponibles: int


class ImportOut(BaseModel):
    importados: int
    ya_tenian_permiso: int
    rechazados: int
    invalidos: list[dict[str, Any]]


@router.get("/tenant/contacts", response_model=ContactPage)
def list_contacts(
    principal: MemberDep,
    session: SessionDep,
    q: Annotated[str | None, Query(max_length=80)] = None,
    tag: Annotated[str | None, Query(max_length=MAX_TAG_LENGTH)] = None,
    permiso: PermissionFilter | None = None,
    page: Annotated[int, Query(ge=1)] = 1,
    size: Annotated[int, Query(ge=1, le=100)] = 50,
) -> ContactPage:
    tenant_id = principal.tenant_id
    statement = (
        select(Contact, ContactOptin)
        .outerjoin(
            ContactOptin,
            (ContactOptin.contact_id == Contact.id) & (ContactOptin.tenant_id == tenant_id),
        )
        .where(Contact.tenant_id == tenant_id)
    )
    if q and q.strip():
        pattern = f"%{q.strip()}%"
        statement = statement.where(
            or_(
                Contact.profile_name.ilike(pattern),
                Contact.phone_e164.ilike(pattern),
                Contact.custom_data["nombres"].astext.ilike(pattern),
            )
        )
    if tag:
        statement = statement.where(
            select(ContactTag.contact_id)
            .where(
                ContactTag.tenant_id == tenant_id,
                ContactTag.contact_id == Contact.id,
                ContactTag.tag == tag.strip().lower(),
            )
            .exists()
        )
    if permiso == "none":
        statement = statement.where(ContactOptin.id.is_(None))
    elif permiso is not None:
        statement = statement.where(ContactOptin.status == permiso)
    total = int(
        session.execute(select(func.count()).select_from(statement.subquery())).scalar_one()
    )
    granted = int(
        session.execute(
            select(func.count())
            .select_from(ContactOptin)
            .join(Contact, Contact.id == ContactOptin.contact_id)
            .where(
                ContactOptin.tenant_id == tenant_id,
                ContactOptin.status == "granted",
                Contact.opted_out_at.is_(None),
            )
        ).scalar_one()
    )
    rows = session.execute(
        statement.order_by(Contact.last_inbound_at.desc().nulls_last(), Contact.id)
        .limit(size)
        .offset((page - 1) * size)
    ).all()
    tags = ContactTagRepository(session, tenant_id).for_contacts([c.id for c, _ in rows])
    return ContactPage(
        items=[
            ContactOut(
                id=contact.id,
                nombre=contact_name(contact),
                telefono=contact.phone_e164,
                permiso=optin.status if optin is not None else None,
                permiso_origen=optin.source if optin is not None else None,
                baja=contact.opted_out_at is not None,
                etiquetas=tags.get(contact.id, []),
                ultimo_mensaje=contact.last_inbound_at,
            )
            for contact, optin in rows
        ],
        total=total,
        con_permiso=granted,
    )


@router.get("/tenant/contacts/tags", response_model=list[TagCount])
def list_tags(principal: MemberDep, session: SessionDep) -> list[TagCount]:
    return [
        TagCount(tag=tag, total=total)
        for tag, total in ContactTagRepository(session, principal.tenant_id).all_tags()
    ]


@router.put(
    "/tenant/contacts/{contact_id}/tags",
    response_model=list[str],
    responses=error_responses(*AUTH, (404, "`not_found`")),
)
def set_tags(
    contact_id: uuid.UUID, body: TagsIn, principal: MemberDep, session: SessionDep
) -> list[str]:
    if ContactRepository(session, principal.tenant_id).get(contact_id) is None:
        raise _not_found()
    clean = sorted(set(body.tags))
    ContactTagRepository(session, principal.tenant_id).replace(contact_id, clean)
    session.commit()
    return clean


@router.get("/tenant/contacts/import", response_model=ImportInfo)
def import_info(principal: ConfigurationManagerDep, session: SessionDep) -> ImportInfo:
    used = OptinImportRepository(session, principal.tenant_id).imported_since(
        _now() - timedelta(days=7)
    )
    return ImportInfo(
        declaracion=DECLARATION,
        limite_semanal=WEEKLY_IMPORT_LIMIT,
        disponibles=max(0, WEEKLY_IMPORT_LIMIT - used),
    )


@router.post(
    "/tenant/contacts/import",
    response_model=ImportOut,
    responses=error_responses(
        *AUTH, (422, "Validation: `nombre_invalido`, `variable_desconocida`, `limite_semanal`, ...")
    ),
)
async def import_contacts(
    principal: ConfigurationManagerDep,
    session: SessionDep,
    file: Annotated[UploadFile, File()],
    acepto: Annotated[bool, Form()] = False,
    etiquetas: Annotated[str, Form(max_length=200)] = "",
) -> ImportOut:
    raw = await file.read()
    tags = sorted({t.strip().lower()[:MAX_TAG_LENGTH] for t in etiquetas.split(",") if t.strip()})[
        :10
    ]
    try:
        result = OptinService(session, principal.tenant_id).import_csv(
            raw,
            filename=file.filename,
            accepted=acepto,
            user_id=principal.user.id,
            tags=tags,
        )
    except CampaignError as exc:
        session.rollback()
        raise _error(exc) from exc
    session.commit()
    return ImportOut(
        importados=len(result.imported),
        ya_tenian_permiso=len(result.already),
        rechazados=len(result.refused),
        invalidos=result.invalid[:50],
    )


# ------------------------------------------------------------------- settings


class SettingsOut(BaseModel):
    optin_preguntar: bool
    seguimiento_plantilla_id: uuid.UUID | None
    seguimiento_activo: bool


class SettingsIn(BaseModel):
    optin_preguntar: bool
    seguimiento_plantilla_id: uuid.UUID | None = None


@router.get("/tenant/campaigns/settings", response_model=SettingsOut)
def campaign_settings(principal: MemberDep, session: SessionDep) -> SettingsOut:
    config = AgentConfigRepository(session, principal.tenant_id).current()
    if config is None:
        # Nothing saved yet: the defaults of the columns.
        return SettingsOut(
            optin_preguntar=True, seguimiento_plantilla_id=None, seguimiento_activo=False
        )
    return SettingsOut(
        optin_preguntar=config.optin_preguntar,
        seguimiento_plantilla_id=config.seguimiento_plantilla_id,
        seguimiento_activo=config.seguimiento_activo,
    )


@router.put(
    "/tenant/campaigns/settings",
    response_model=SettingsOut,
    responses=error_responses(
        *AUTH, (422, "Validation: `nombre_invalido`, `variable_desconocida`, `limite_semanal`, ...")
    ),
)
def put_settings(
    body: SettingsIn, principal: ConfigurationManagerDep, session: SessionDep
) -> SettingsOut:
    if body.seguimiento_plantilla_id is not None:
        template = MessageTemplateRepository(session, principal.tenant_id).get(
            body.seguimiento_plantilla_id
        )
        if template is None or template.category != "utility":
            raise ApiError(
                status_code=422,
                error="plantilla_invalida",
                message="Para los seguimientos elige una plantilla de Utilidad.",
            )
    AgentConfigRepository(session, principal.tenant_id).upsert(
        optin_preguntar=body.optin_preguntar,
        seguimiento_plantilla_id=body.seguimiento_plantilla_id,
    )
    session.commit()
    return campaign_settings(principal, session)


# ------------------------------------------------------------------ campaigns


class SegmentIn(BaseModel):
    etiquetas: list[Tag] = Field(default_factory=list, max_length=10)
    etapas: list[Literal["nuevo", "interesado", "por_pagar", "ganado", "perdido"]] = Field(
        default_factory=list
    )
    compro: bool | None = None
    producto: str | None = Field(default=None, max_length=80)
    dias_sin_escribir_min: int | None = Field(default=None, ge=0, le=3650)
    dias_sin_escribir_max: int | None = Field(default=None, ge=0, le=3650)


class CampaignIn(BaseModel):
    name: str = Field(max_length=80)
    template_id: uuid.UUID
    # A/B (Feature 013, C-106): half of the recipients get this one.
    template_b_id: uuid.UUID | None = None
    segment: SegmentIn = Field(default_factory=SegmentIn)
    window_from: str = Field(default="09:00", pattern=r"^\d{2}:\d{2}$")
    window_to: str = Field(default="20:00", pattern=r"^\d{2}:\d{2}$")
    scheduled_at: datetime | None = None


class EstimateIn(BaseModel):
    template_id: uuid.UUID
    segment: SegmentIn = Field(default_factory=SegmentIn)


class EstimateOut(BaseModel):
    destinatarios: int
    tarifa_usd: str | None
    costo_estimado_usd: str | None
    pais: str
    categoria: str
    avisos: list[str]
    limites_aplicados: bool


class CampaignOut(BaseModel):
    id: uuid.UUID
    name: str
    template_id: uuid.UUID
    template_label: str | None
    template_b_id: uuid.UUID | None = None
    template_b_label: str | None = None
    segment: dict[str, Any]
    window_from: str
    window_to: str
    scheduled_at: datetime | None
    status: str
    pause_reason: str | None
    estimated_cost_usd: str | None
    started_at: datetime | None
    finished_at: datetime | None
    created_at: datetime
    report: dict[str, Any] | None = None


def _money(principal: Principal) -> bool:
    return principal.role != "operator"


def _campaign_out(
    session: Any, principal: Principal, campaign: Campaign, *, report: bool = False
) -> CampaignOut:
    template = session.get(MessageTemplate, campaign.template_id)
    money = _money(principal)
    return CampaignOut(
        id=campaign.id,
        name=campaign.name,
        template_id=campaign.template_id,
        template_label=template.label if template is not None else None,
        template_b_id=campaign.template_b_id,
        template_b_label=(
            b.label
            if campaign.template_b_id
            and (b := session.get(MessageTemplate, campaign.template_b_id))
            else None
        ),
        segment=campaign.segment,
        window_from=campaign.window_from,
        window_to=campaign.window_to,
        scheduled_at=campaign.scheduled_at,
        status=campaign.status,
        pause_reason=campaign.pause_reason,
        estimated_cost_usd=str(campaign.estimated_cost_usd)
        if money and campaign.estimated_cost_usd is not None
        else None,
        started_at=campaign.started_at,
        finished_at=campaign.finished_at,
        created_at=campaign.created_at,
        report=campaign_report(session, campaign, money=money) if report else None,
    )


def _campaign(session: Any, principal: Principal, campaign_id: uuid.UUID) -> Campaign:
    campaign = CampaignRepository(session, principal.tenant_id).get(campaign_id)
    if campaign is None:
        raise _not_found()
    return campaign


@router.get("/tenant/campaigns", response_model=list[CampaignOut])
def list_campaigns(principal: MemberDep, session: SessionDep) -> list[CampaignOut]:
    return [
        _campaign_out(session, principal, c, report=c.status != "draft")
        for c in CampaignRepository(session, principal.tenant_id).ordered()
    ]


@router.post(
    "/tenant/campaigns/estimate",
    response_model=EstimateOut,
    responses=error_responses(
        *AUTH, (422, "Validation: `nombre_invalido`, `variable_desconocida`, `limite_semanal`, ...")
    ),
)
def estimate_campaign(
    body: EstimateIn, principal: ConfigurationManagerDep, session: SessionDep
) -> EstimateOut:
    service = CampaignService(session, principal.tenant_id)
    try:
        template = service.template(body.template_id)
        estimate = service.estimate(Segment.parse(body.segment.model_dump()), template, now=_now())
    except CampaignError as exc:
        raise _error(exc) from exc
    return EstimateOut(**estimate)


@router.post(
    "/tenant/campaigns",
    response_model=CampaignOut,
    status_code=status.HTTP_201_CREATED,
    responses=error_responses(
        *AUTH, (422, "Validation: `nombre_invalido`, `variable_desconocida`, `limite_semanal`, ...")
    ),
)
def create_campaign(
    body: CampaignIn, principal: ConfigurationManagerDep, session: SessionDep
) -> CampaignOut:
    try:
        campaign = CampaignService(session, principal.tenant_id).create(
            body.model_dump(), by=principal.user.id
        )
    except CampaignError as exc:
        raise _error(exc) from exc
    session.commit()
    return _campaign_out(session, principal, campaign)


@router.get(
    "/tenant/campaigns/{campaign_id}",
    response_model=CampaignOut,
    responses=error_responses(*AUTH, (404, "`not_found`")),
)
def get_campaign(campaign_id: uuid.UUID, principal: MemberDep, session: SessionDep) -> CampaignOut:
    return _campaign_out(
        session, principal, _campaign(session, principal, campaign_id), report=True
    )


@router.put(
    "/tenant/campaigns/{campaign_id}",
    response_model=CampaignOut,
    responses=error_responses(
        *AUTH,
        (404, "`not_found`"),
        CONFLICT,
        (422, "Validation: `nombre_invalido`, `variable_desconocida`, `limite_semanal`, ..."),
    ),
)
def update_campaign(
    campaign_id: uuid.UUID,
    body: CampaignIn,
    principal: ConfigurationManagerDep,
    session: SessionDep,
) -> CampaignOut:
    campaign = _campaign(session, principal, campaign_id)
    try:
        CampaignService(session, principal.tenant_id).update(campaign, body.model_dump())
    except CampaignError as exc:
        raise _error(exc) from exc
    session.commit()
    return _campaign_out(session, principal, campaign)


@router.delete(
    "/tenant/campaigns/{campaign_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    responses=error_responses(
        *AUTH,
        (404, "`not_found`"),
        CONFLICT,
    ),
)
def delete_campaign(
    campaign_id: uuid.UUID, principal: ConfigurationManagerDep, session: SessionDep
) -> Response:
    campaign = _campaign(session, principal, campaign_id)
    try:
        CampaignService(session, principal.tenant_id).delete(campaign)
    except CampaignError as exc:
        raise _error(exc) from exc
    session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post(
    "/tenant/campaigns/{campaign_id}/{action}",
    response_model=CampaignOut,
    responses=error_responses(
        *AUTH,
        (404, "`not_found`"),
        CONFLICT,
        (422, "Validation: `nombre_invalido`, `variable_desconocida`, `limite_semanal`, ..."),
    ),
)
def campaign_action(
    campaign_id: uuid.UUID,
    action: Literal["schedule", "pause", "resume", "cancel"],
    principal: ConfigurationManagerDep,
    session: SessionDep,
) -> CampaignOut:
    campaign = _campaign(session, principal, campaign_id)
    service = CampaignService(session, principal.tenant_id)
    try:
        if action == "schedule":
            service.schedule(campaign, now=_now())
        elif action == "pause":
            service.pause(campaign)
        elif action == "resume":
            service.resume(campaign)
        else:
            service.cancel(campaign, now=_now())
    except CampaignError as exc:
        session.rollback()
        raise _error(exc) from exc
    session.commit()
    return _campaign_out(session, principal, campaign, report=True)


# ------------------------------------------------------------- platform admin


class MetaRateOut(BaseModel):
    country: str
    category: str
    valid_from: date
    rate_usd: str


class MetaRateIn(BaseModel):
    country: str = Field(pattern=r"^[A-Z]{2}$")
    category: Literal["utility", "marketing", "service"]
    valid_from: date
    rate_usd: Decimal = Field(gt=0, lt=1, decimal_places=5)


class DailyLimitIn(BaseModel):
    limite_diario: int | None = Field(default=None, ge=1, le=100000)


@router.get("/platform-admin/meta-rates", response_model=list[MetaRateOut])
def list_meta_rates(admin: PlatformAdminDep, session: SessionDep) -> list[MetaRateOut]:
    rows = session.execute(
        select(MetaRate).order_by(MetaRate.country, MetaRate.category, MetaRate.valid_from.desc())
    ).scalars()
    return [
        MetaRateOut(
            country=r.country,
            category=r.category,
            valid_from=r.valid_from,
            rate_usd=str(r.rate_usd),
        )
        for r in rows
    ]


@router.put("/platform-admin/meta-rates", response_model=list[MetaRateOut])
def put_meta_rate(
    body: MetaRateIn, admin: PlatformAdminDep, session: SessionDep
) -> list[MetaRateOut]:
    existing = session.get(MetaRate, (body.country, body.category, body.valid_from))
    if existing is None:
        session.add(
            MetaRate(
                country=body.country,
                category=body.category,
                valid_from=body.valid_from,
                rate_usd=body.rate_usd,
            )
        )
    else:
        existing.rate_usd = body.rate_usd
    session.commit()
    return list_meta_rates(admin, session)


@router.put(
    "/platform-admin/numbers/{number_id}/daily-limit",
    status_code=status.HTTP_204_NO_CONTENT,
    responses=error_responses(*AUTH, (404, "`not_found`")),
)
def put_daily_limit(
    number_id: uuid.UUID, body: DailyLimitIn, admin: PlatformAdminDep, session: SessionDep
) -> Response:
    number = session.get(WhatsAppNumber, number_id)
    if number is None:
        raise _not_found()
    number.limite_diario = body.limite_diario
    session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)
