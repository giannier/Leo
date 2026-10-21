"""The assistant's configuration by layers (Feature 017): `/assistant/...`.

This module holds the parts the 017 adds: situations (TASK-462) and the rules of
the process template and of the rubro with their switches (TASK-435). The
business profile, FAQs, promotions and media (TASK-434, TASK-436) live in
`assistant_info.py`.

Writes need the configuration role (`ConfigurationManagerDep`). Switching rules
and marking news seen are also open to a member with the reviewer permission.
"""

from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Query, Response, status
from pydantic import BaseModel, Field, StringConstraints, ValidationError
from sqlalchemy.orm import Session

from app.agent.prompts.stage import build_stage_prompt
from app.agent.tools.definitions import registry_for_stage
from app.api.deps import ConfigurationManagerDep, JobQueueDep, ReviewerDep, SessionDep
from app.api.errors import ApiError, error_responses
from app.models.situation import SITUATION_ORIGINS, SITUATION_STATES, Situation
from app.process.facts import Facts
from app.process.layers import LayerResolver, LayerRule, News, UnknownRule
from app.process.service import (
    DEFAULT_CACHE,
    DEFAULT_TEMPLATE_KEY,
    ProcessConfigService,
)
from app.process.templates import TEMPLATES
from app.process.templates.base import Stage
from app.repositories.business import AgentConfigRepository, BusinessProfileRepository
from app.repositories.catalog import CatalogRepository
from app.services.capabilities import automatic_again
from app.services.situations import (
    EMBED_JOB,
    MAX_QUE_HACE,
    SituationError,
    SituationInput,
    SituationService,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/assistant", tags=["assistant"])

AUTH_RESPONSES = (
    (401, "`not_authenticated`, `token_expired` or `invalid_token`"),
    (
        403,
        "`email_not_verified`, `no_active_tenant`, `tenant_access_denied` or `insufficient_role`",
    ),
)

Title = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=80)]
When = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=300)]
What = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1, max_length=MAX_QUE_HACE)
]
Example = Annotated[str, StringConstraints(strip_whitespace=True, max_length=600)]
State = Literal["borrador", "activa", "inactiva"]
Origin = Literal["manual", "sugerida_rubro", "migrada", "aprendida"]

assert set(State.__args__) == set(SITUATION_STATES)  # type: ignore[attr-defined]
assert set(Origin.__args__) == set(SITUATION_ORIGINS)  # type: ignore[attr-defined]


class SituationRequest(BaseModel):
    titulo: Title
    cuando_aplica: When
    que_hace: What
    media_asset_ids: list[uuid.UUID] = Field(default_factory=list, max_length=5)
    derivar: bool = False
    ejemplo_respuesta: Example | None = None
    # None or empty: every stage.
    etapas: list[str] | None = Field(default=None, max_length=11)
    siempre: bool = False

    def to_input(self) -> SituationInput:
        return SituationInput(
            titulo=self.titulo,
            cuando_aplica=self.cuando_aplica,
            que_hace=self.que_hace,
            media_asset_ids=self.media_asset_ids,
            derivar=self.derivar,
            ejemplo_respuesta=self.ejemplo_respuesta,
            etapas=self.etapas or None,
            siempre=self.siempre,
        )


class SituationResponse(BaseModel):
    id: uuid.UUID
    titulo: str
    cuando_aplica: str
    que_hace: str
    media_asset_ids: list[uuid.UUID]
    derivar: bool
    ejemplo_respuesta: str | None
    etapas: list[str] | None
    siempre: bool
    estado: State
    origen: Origin
    # False until the worker computed the embedding: until then it only enters
    # a turn if it is marked "siempre".
    lista_para_coincidir: bool
    created_at: datetime
    updated_at: datetime


class SituationList(BaseModel):
    items: list[SituationResponse]


class FromSuggestedRequest(BaseModel):
    # {campo: valor} for every field the suggestion asks for.
    valores: dict[str, Annotated[str, StringConstraints(strip_whitespace=True, max_length=200)]] = (
        Field(default_factory=dict)
    )


def _response(situation: Situation) -> SituationResponse:
    return SituationResponse(
        id=situation.id,
        titulo=situation.titulo,
        cuando_aplica=situation.cuando_aplica,
        que_hace=situation.que_hace,
        media_asset_ids=list(situation.media_asset_ids),
        derivar=situation.derivar,
        ejemplo_respuesta=situation.ejemplo_respuesta,
        etapas=list(situation.etapas) if situation.etapas else None,
        siempre=situation.siempre,
        estado=situation.estado,
        origen=situation.origen,
        lista_para_coincidir=situation.embedding is not None,
        created_at=situation.created_at,
        updated_at=situation.updated_at,
    )


def _invalid(exc: SituationError) -> ApiError:
    return ApiError(status_code=422, error=exc.code, message=str(exc))


def _not_found() -> ApiError:
    return ApiError(status_code=404, error="situation_not_found", message="Situation not found.")


async def _embed_later(queue: JobQueueDep, tenant_id: uuid.UUID) -> None:
    """Ask the worker for the embedding. A lost job is picked up by the hourly sweep."""
    try:
        await queue.enqueue(EMBED_JOB, str(tenant_id))
    except Exception:  # noqa: BLE001 - the sweep embeds it within the hour
        logger.warning("could not enqueue the situation embedding; the sweep will do it")


def _load(service: SituationService, situation_id: uuid.UUID) -> Situation:
    situation = service.get(situation_id)
    if situation is None:
        raise _not_found()
    return situation


@router.get(
    "/situations",
    response_model=SituationList,
    responses=error_responses(*AUTH_RESPONSES),
)
def list_situations(
    principal: ConfigurationManagerDep,
    session: SessionDep,
    estado: Annotated[State | None, Query()] = None,
    origen: Annotated[Origin | None, Query()] = None,
) -> SituationList:
    service = SituationService(session, principal.tenant_id)
    return SituationList(
        items=[_response(s) for s in service.listing(estado=estado, origen=origen)]
    )


@router.post(
    "/situations",
    status_code=status.HTTP_201_CREATED,
    response_model=SituationResponse,
    responses=error_responses(
        *AUTH_RESPONSES, (422, "`que_hace_too_long`, `unknown_stage` or `unknown_media`")
    ),
)
async def create_situation(
    body: SituationRequest,
    principal: ConfigurationManagerDep,
    session: SessionDep,
    queue: JobQueueDep,
) -> SituationResponse:
    service = SituationService(session, principal.tenant_id)
    try:
        situation = service.create(body.to_input(), by=principal.user_id)
    except SituationError as exc:
        raise _invalid(exc) from exc
    session.commit()
    await _embed_later(queue, principal.tenant_id)
    return _response(situation)


@router.post(
    "/situations/desde-sugerida/{suggested_id}",
    status_code=status.HTTP_201_CREATED,
    response_model=SituationResponse,
    responses=error_responses(*AUTH_RESPONSES, (422, "`unknown_suggestion` or `missing_fields`")),
)
async def create_from_suggested(
    suggested_id: uuid.UUID,
    body: FromSuggestedRequest,
    principal: ConfigurationManagerDep,
    session: SessionDep,
    queue: JobQueueDep,
) -> SituationResponse:
    profile = BusinessProfileRepository(session, principal.tenant_id).current()
    service = SituationService(session, principal.tenant_id)
    try:
        situation = service.from_suggested(
            suggested_id,
            profile.industry if profile else None,
            body.valores,
            by=principal.user_id,
        )
    except SituationError as exc:
        raise _invalid(exc) from exc
    session.commit()
    await _embed_later(queue, principal.tenant_id)
    return _response(situation)


@router.get(
    "/situations/{situation_id}",
    response_model=SituationResponse,
    responses=error_responses(*AUTH_RESPONSES, (404, "`situation_not_found`")),
)
def get_situation(
    situation_id: uuid.UUID, principal: ConfigurationManagerDep, session: SessionDep
) -> SituationResponse:
    return _response(_load(SituationService(session, principal.tenant_id), situation_id))


@router.put(
    "/situations/{situation_id}",
    response_model=SituationResponse,
    responses=error_responses(
        *AUTH_RESPONSES,
        (404, "`situation_not_found`"),
        (422, "`que_hace_too_long`, `unknown_stage` or `unknown_media`"),
    ),
)
async def update_situation(
    situation_id: uuid.UUID,
    body: SituationRequest,
    principal: ConfigurationManagerDep,
    session: SessionDep,
    queue: JobQueueDep,
) -> SituationResponse:
    service = SituationService(session, principal.tenant_id)
    situation = _load(service, situation_id)
    try:
        service.update(situation, body.to_input())
    except SituationError as exc:
        raise _invalid(exc) from exc
    session.commit()
    if situation.embedding is None:
        await _embed_later(queue, principal.tenant_id)
    return _response(situation)


@router.delete(
    "/situations/{situation_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    responses=error_responses(*AUTH_RESPONSES, (404, "`situation_not_found`")),
)
def delete_situation(
    situation_id: uuid.UUID, principal: ConfigurationManagerDep, session: SessionDep
) -> Response:
    service = SituationService(session, principal.tenant_id)
    service.delete(_load(service, situation_id))
    session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


def _set_state(
    situation_id: uuid.UUID, principal: ConfigurationManagerDep, session: SessionDep, estado: str
) -> SituationResponse:
    service = SituationService(session, principal.tenant_id)
    situation = service.set_state(_load(service, situation_id), estado)
    session.commit()
    return _response(situation)


@router.post(
    "/situations/{situation_id}/activar",
    response_model=SituationResponse,
    responses=error_responses(*AUTH_RESPONSES, (404, "`situation_not_found`")),
)
def activate_situation(
    situation_id: uuid.UUID, principal: ConfigurationManagerDep, session: SessionDep
) -> SituationResponse:
    return _set_state(situation_id, principal, session, "activa")


@router.post(
    "/situations/{situation_id}/desactivar",
    response_model=SituationResponse,
    responses=error_responses(*AUTH_RESPONSES, (404, "`situation_not_found`")),
)
def deactivate_situation(
    situation_id: uuid.UUID, principal: ConfigurationManagerDep, session: SessionDep
) -> SituationResponse:
    return _set_state(situation_id, principal, session, "inactiva")


# ------------------------------------------------------- process and rubro (TASK-435)

Kind = Literal["proceso", "rubro"]


class RuleView(BaseModel):
    rule_key: str
    texto: str
    apagada: bool


class StageView(BaseModel):
    key: str
    embudo: str
    reglas: list[RuleView]


class NewsView(BaseModel):
    version: int
    vista: int
    reglas: list[RuleView]


class TemplateView(BaseModel):
    key: str
    nombre: str
    descripcion: str
    opciones_schema: dict[str, Any]
    opciones_default: dict[str, Any]
    sugerida_por_rubro: bool


class TemplateList(BaseModel):
    items: list[TemplateView]


class ProcessView(BaseModel):
    template_key: str
    # True while the platform picks the template from what the agent does (feature 019).
    plantilla_automatica: bool = True
    template_version: int | None
    options: dict[str, Any]
    etapas: list[StageView]
    novedades: NewsView | None = None


class ProcessRequest(BaseModel):
    template_key: str
    options: dict[str, Any] = Field(default_factory=dict)


class SwitchRequest(BaseModel):
    apagada: bool


class FaqView(BaseModel):
    pregunta: str
    respuesta: str


class ObjectionView(BaseModel):
    objecion: str
    manejo: str


class SuggestedView(BaseModel):
    id: uuid.UUID
    titulo: str
    cuando_aplica: str
    que_hace: str
    campos: dict[str, str]


class RubroView(BaseModel):
    key: str | None
    nombre: str | None
    plantilla_sugerida: str | None
    capacidades_sugeridas: list[str] = Field(default_factory=list)
    version: int | None
    reglas: list[RuleView]
    faqs: list[FaqView]
    objeciones: list[ObjectionView]
    situaciones_sugeridas: list[SuggestedView]
    novedades: NewsView | None = None


class PromptPreview(BaseModel):
    stage: str
    prompt: str
    tokens_estimados: int
    secciones: dict[str, int]
    herramientas: list[str]


def _rules(rules: list[LayerRule]) -> list[RuleView]:
    return [RuleView(rule_key=r.rule_key, texto=r.texto, apagada=r.apagada) for r in rules]


def _news(news: News | None) -> NewsView | None:
    if news is None or not news.rules:
        return None
    return NewsView(version=news.version, vista=news.seen, reglas=_rules(list(news.rules)))


def _keys(session: Session, tenant_id: uuid.UUID) -> tuple[str, str | None]:
    """The business's template and rubro."""
    agent = AgentConfigRepository(session, tenant_id).current()
    profile = BusinessProfileRepository(session, tenant_id).current()
    template = agent.process_template_key if agent else DEFAULT_TEMPLATE_KEY
    if template not in TEMPLATES:
        template = DEFAULT_TEMPLATE_KEY
    return template, profile.industry if profile else None


@router.get("/templates", response_model=TemplateList, responses=error_responses(*AUTH_RESPONSES))
def list_templates(principal: ConfigurationManagerDep, session: SessionDep) -> TemplateList:
    _, rubro_key = _keys(session, principal.tenant_id)
    rubro = CatalogRepository(session).rubro(rubro_key)
    return TemplateList(
        items=[
            TemplateView(
                key=t.key,
                nombre=t.nombre,
                descripcion=t.descripcion,
                opciones_schema=t.options_schema,
                opciones_default=t.defaults.model_dump(mode="json", exclude_none=True),
                sugerida_por_rubro=rubro is not None and rubro.plantilla_sugerida == t.key,
            )
            for t in TEMPLATES.values()
        ]
    )


def _process(session: Session, tenant_id: uuid.UUID) -> ProcessView:
    template_key, _ = _keys(session, tenant_id)
    template = TEMPLATES[template_key]
    resolver = LayerResolver(session, tenant_id)
    rules = resolver.rules("proceso", template_key)
    version = CatalogRepository(session).published("proceso", template_key)
    agent = AgentConfigRepository(session, tenant_id).current()
    return ProcessView(
        template_key=template_key,
        plantilla_automatica=not (agent.plantilla_manual if agent else False),
        template_version=version.version if version else None,
        options=dict(agent.process_options or {}) if agent else {},
        etapas=[
            StageView(
                key=stage.value,
                embudo=template.stage(stage).funnel.value,
                reglas=_rules([r for r in rules if r.etapa == stage.value]),
            )
            for stage in Stage
        ],
        novedades=_news(resolver.news("proceso", template_key)),
    )


@router.get("/process", response_model=ProcessView, responses=error_responses(*AUTH_RESPONSES))
def get_process(principal: ReviewerDep, session: SessionDep) -> ProcessView:
    view = _process(session, principal.tenant_id)
    session.commit()  # `news` may record the first version seen
    return view


@router.put(
    "/process",
    response_model=ProcessView,
    responses=error_responses(*AUTH_RESPONSES, (422, "`unknown_template` or `invalid_options`")),
)
def put_process(
    body: ProcessRequest, principal: ConfigurationManagerDep, session: SessionDep
) -> ProcessView:
    template = TEMPLATES.get(body.template_key)
    if template is None:
        raise ApiError(status_code=422, error="unknown_template", message=body.template_key)
    try:
        options = template.validate_options(body.options)
    except ValidationError as exc:
        raise ApiError(
            status_code=422,
            error="invalid_options",
            message="The options do not fit the template.",
            fields={
                ".".join(str(p) for p in e["loc"]) or "options": e["msg"] for e in exc.errors()
            },
        ) from exc
    AgentConfigRepository(session, principal.tenant_id).upsert(
        process_template_key=template.key,
        process_options=options.model_dump(mode="json", exclude_none=True),
        # Chosen by hand: what the agent does no longer changes it (feature 019).
        plantilla_manual=True,
    )
    session.commit()
    DEFAULT_CACHE.invalidate(principal.tenant_id)
    return _process(session, principal.tenant_id)


@router.post(
    "/process/automatica",
    response_model=ProcessView,
    responses=error_responses(*AUTH_RESPONSES),
)
def automatic_process(principal: ConfigurationManagerDep, session: SessionDep) -> ProcessView:
    """Give the template back to the platform: it follows what the agent does again."""
    automatic_again(session, principal.tenant_id)
    session.commit()
    return _process(session, principal.tenant_id)


@router.put(
    "/rules/{kind}/{rule_key}",
    response_model=RuleView,
    responses=error_responses(*AUTH_RESPONSES, (404, "`unknown_rule`")),
)
def switch_rule(
    kind: Kind, rule_key: str, body: SwitchRequest, principal: ReviewerDep, session: SessionDep
) -> RuleView:
    """Switch a template or rubro rule on or off. Its text is never editable (C-01)."""
    template_key, rubro_key = _keys(session, principal.tenant_id)
    try:
        rule = LayerResolver(session, principal.tenant_id).switch(
            kind,
            template_key if kind == "proceso" else rubro_key,
            rule_key,
            apagada=body.apagada,
            by=principal.user_id,
        )
    except UnknownRule as exc:
        raise ApiError(status_code=404, error="unknown_rule", message=rule_key) from exc
    session.commit()
    return RuleView(rule_key=rule.rule_key, texto=rule.texto, apagada=rule.apagada)


@router.get("/rubro", response_model=RubroView, responses=error_responses(*AUTH_RESPONSES))
def get_rubro(principal: ReviewerDep, session: SessionDep) -> RubroView:
    _, rubro_key = _keys(session, principal.tenant_id)
    catalog = CatalogRepository(session)
    rubro = catalog.rubro(rubro_key)
    version = catalog.published("rubro", rubro_key) if rubro_key else None
    resolver = LayerResolver(session, principal.tenant_id)
    view = RubroView(
        key=rubro.key if rubro else None,
        nombre=rubro.nombre if rubro else None,
        plantilla_sugerida=rubro.plantilla_sugerida if rubro else None,
        capacidades_sugeridas=list(rubro.capacidades_sugeridas) if rubro else [],
        version=version.version if version else None,
        reglas=_rules(resolver.rules("rubro", rubro_key)),
        faqs=[FaqView(pregunta=f.pregunta, respuesta=f.respuesta) for f in catalog.faqs(version.id)]
        if version
        else [],
        objeciones=[
            ObjectionView(objecion=o.objecion, manejo=o.manejo)
            for o in catalog.objections(version.id)
        ]
        if version
        else [],
        situaciones_sugeridas=[
            SuggestedView(
                id=s.id,
                titulo=s.titulo,
                cuando_aplica=s.cuando_aplica,
                que_hace=s.que_hace,
                campos=dict(s.campos),
            )
            for s in catalog.suggested_situations(version.id)
        ]
        if version
        else [],
        novedades=_news(resolver.news("rubro", rubro_key)),
    )
    session.commit()
    return view


@router.post(
    "/novedades/{kind}/vistas",
    status_code=status.HTTP_204_NO_CONTENT,
    responses=error_responses(*AUTH_RESPONSES),
)
def mark_news_seen(kind: Kind, principal: ReviewerDep, session: SessionDep) -> Response:
    template_key, rubro_key = _keys(session, principal.tenant_id)
    LayerResolver(session, principal.tenant_id).mark_seen(
        kind, template_key if kind == "proceso" else rubro_key
    )
    session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get(
    "/prompt-preview",
    response_model=PromptPreview,
    responses=error_responses(*AUTH_RESPONSES),
)
def prompt_preview(
    principal: ConfigurationManagerDep,
    session: SessionDep,
    stage: Annotated[Stage, Query()] = Stage.IDENTIFICAR,
) -> PromptPreview:
    """The prompt of a stage as the model would get it, with example facts and no situations."""
    config = ProcessConfigService(session).effective(principal.tenant_id)
    facts = Facts()
    registry = registry_for_stage(config.template, stage, facts, config.options)
    prompt = build_stage_prompt(
        config, facts, stage, now=datetime.now(UTC), tool_specs=registry.specs()
    )
    return PromptPreview(
        stage=stage.value,
        prompt=prompt.text,
        tokens_estimados=prompt.tokens,
        secciones=dict(prompt.sections),
        herramientas=list(registry.names()),
    )
