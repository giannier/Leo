"""The business's own layer, by forms (Feature 004 §1 and 017 RF-010; TASK-434).

`/assistant/business-info/{section}`, `/assistant/faqs`, `/assistant/promotions`,
`/assistant/behavior` and `/assistant/categories`. Nothing here is free text the
model has to interpret as rules: every section has a schema, money is Decimal,
and the prompt shows it as data (the business's own rules are situations).

Every write invalidates the tenant's cached process configuration, so the next
turn already sees it.
"""

from __future__ import annotations

import contextlib
import uuid
from datetime import UTC, datetime
from decimal import Decimal
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Query, Response, status
from pydantic import BaseModel, Field, StringConstraints, ValidationError, model_validator
from sqlalchemy import func, select, update

from app.agent.model_policy import allowed_levels
from app.api.deps import ConfigurationManagerDep, JobQueueDep, SessionDep
from app.api.errors import ApiError, error_responses
from app.models.business import Product, ProductCategory
from app.models.orders import Order
from app.models.sales_config import BUSINESS_INFO_SECTIONS, Faq, Promotion
from app.process.service import DEFAULT_CACHE
from app.process.templates.base import (
    DEFAULT_SYSTEM_MESSAGES,
    SYSTEM_MESSAGE_PLACEHOLDERS,
    placeholders_in,
)
from app.repositories.business import AgentConfigRepository
from app.repositories.product import ProductCategoryRepository, ProductRepository
from app.repositories.sales_config import BusinessInfoRepository, PromotionRepository
from app.schemas.business_info import SECTION_SCHEMAS
from app.services.knowledge import (
    IndexingUnavailableError,
    KnowledgeError,
    KnowledgeService,
    RagLimitExceededError,
    SourceNotFoundError,
)
from app.services.overview import build_overview

router = APIRouter(prefix="/assistant", tags=["assistant"])

AUTH_RESPONSES = (
    (401, "`not_authenticated`, `token_expired` or `invalid_token`"),
    (
        403,
        "`email_not_verified`, `no_active_tenant`, `tenant_access_denied` or `insufficient_role`",
    ),
)
ALL_LEVELS = ("economico", "medio", "mejor")

Section = Literal["locales", "entregas", "tipos_cliente", "devoluciones", "contacto"]
assert set(Section.__args__) == set(BUSINESS_INFO_SECTIONS)  # type: ignore[attr-defined]


def _changed(tenant_id: uuid.UUID) -> None:
    DEFAULT_CACHE.invalidate(tenant_id)


def _not_found(what: str = "not_found") -> ApiError:
    return ApiError(status_code=404, error=what, message="Not found.")


# ------------------------------------------------------------ business info


class SectionResponse(BaseModel):
    section: Section
    data: dict[str, Any] | None
    updated_at: datetime | None = None


@router.get(
    "/business-info/{section}",
    response_model=SectionResponse,
    responses=error_responses(*AUTH_RESPONSES),
)
def get_section(
    section: Section, principal: ConfigurationManagerDep, session: SessionDep
) -> SectionResponse:
    row = BusinessInfoRepository(session, principal.tenant_id).section(section)
    return SectionResponse(
        section=section,
        data=row.data if row else None,
        updated_at=row.updated_at if row else None,
    )


@router.put(
    "/business-info/{section}",
    response_model=SectionResponse,
    responses=error_responses(*AUTH_RESPONSES, (422, "`validation_error`")),
)
def put_section(
    section: Section,
    body: dict[str, Any],
    principal: ConfigurationManagerDep,
    session: SessionDep,
) -> SectionResponse:
    schema = SECTION_SCHEMAS[section]
    try:
        parsed = schema.model_validate(body)
    except ValidationError as exc:
        raise ApiError(
            status_code=422,
            error="validation_error",
            message=f"The {section} section does not fit its schema.",
            fields={
                ".".join(str(part) for part in error["loc"]) or section: error["msg"]
                for error in exc.errors()
            },
        ) from exc
    row = BusinessInfoRepository(session, principal.tenant_id).upsert(
        section, parsed.model_dump(mode="json")
    )
    session.commit()
    _changed(principal.tenant_id)
    return SectionResponse(section=section, data=row.data, updated_at=row.updated_at)


# --------------------------------------------------------------------- FAQs

Question = Annotated[str, StringConstraints(strip_whitespace=True, min_length=3, max_length=300)]
Answer = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=2000)]


class FaqRequest(BaseModel):
    pregunta: Question
    respuesta: Answer
    orden: int = Field(default=0, ge=0, le=1000)
    is_active: bool = True


class FaqPatch(BaseModel):
    pregunta: Question | None = None
    respuesta: Answer | None = None
    orden: int | None = Field(default=None, ge=0, le=1000)
    is_active: bool | None = None


class FaqResponse(BaseModel):
    id: uuid.UUID
    pregunta: str
    respuesta: str
    orden: int
    is_active: bool
    indexada: bool


class FaqList(BaseModel):
    items: list[FaqResponse]


def _faq(row: Faq) -> FaqResponse:
    return FaqResponse(
        id=row.id,
        pregunta=row.pregunta,
        respuesta=row.respuesta,
        orden=row.orden,
        is_active=row.is_active,
        indexada=row.knowledge_source_id is not None,
    )


def _knowledge_error(exc: KnowledgeError) -> ApiError:
    if isinstance(exc, RagLimitExceededError):
        return ApiError(
            status_code=409, error="rag_limit_exceeded", message="The knowledge budget is full."
        )
    if isinstance(exc, IndexingUnavailableError):
        return ApiError(
            status_code=503,
            error="indexing_unavailable",
            message="The FAQ was not saved: indexing is unavailable. Try again.",
        )
    return ApiError(status_code=422, error="validation_error", message=str(exc))


async def _index(session: SessionDep, tenant_id: uuid.UUID, queue: JobQueueDep, faq: Faq) -> None:
    """Index the FAQ as knowledge (`consultar_conocimiento` answers from it)."""
    service = KnowledgeService(session, tenant_id, queue=queue)
    result = await service.ingest_text(
        source_type="faq", title=faq.pregunta, content=f"{faq.pregunta}\n{faq.respuesta}"
    )
    faq.knowledge_source_id = result.id
    session.commit()


def _unindex(session: SessionDep, tenant_id: uuid.UUID, faq: Faq) -> None:
    if faq.knowledge_source_id is None:
        return
    with contextlib.suppress(SourceNotFoundError):
        KnowledgeService(session, tenant_id).delete(faq.knowledge_source_id)
    faq.knowledge_source_id = None


def _faq_row(session: SessionDep, tenant_id: uuid.UUID, faq_id: uuid.UUID) -> Faq:
    row = session.execute(
        select(Faq).where(Faq.tenant_id == tenant_id, Faq.id == faq_id)
    ).scalar_one_or_none()
    if row is None:
        raise _not_found()
    return row


@router.get("/faqs", response_model=FaqList, responses=error_responses(*AUTH_RESPONSES))
def list_faqs(principal: ConfigurationManagerDep, session: SessionDep) -> FaqList:
    rows = session.execute(
        select(Faq).where(Faq.tenant_id == principal.tenant_id).order_by(Faq.orden, Faq.created_at)
    ).scalars()
    return FaqList(items=[_faq(row) for row in rows])


@router.post(
    "/faqs",
    status_code=status.HTTP_201_CREATED,
    response_model=FaqResponse,
    responses=error_responses(
        *AUTH_RESPONSES, (409, "`rag_limit_exceeded`"), (503, "`indexing_unavailable`")
    ),
)
async def create_faq(
    body: FaqRequest, principal: ConfigurationManagerDep, session: SessionDep, queue: JobQueueDep
) -> FaqResponse:
    faq = Faq(tenant_id=principal.tenant_id, **body.model_dump())
    session.add(faq)
    session.flush()
    if faq.is_active:
        try:
            await _index(session, principal.tenant_id, queue, faq)
        except KnowledgeError as exc:
            session.rollback()
            raise _knowledge_error(exc) from exc
    else:
        session.commit()
    return _faq(faq)


@router.patch(
    "/faqs/{faq_id}",
    response_model=FaqResponse,
    responses=error_responses(*AUTH_RESPONSES, (404, "`not_found`"), (409, "`rag_limit_exceeded`")),
)
async def update_faq(
    faq_id: uuid.UUID,
    body: FaqPatch,
    principal: ConfigurationManagerDep,
    session: SessionDep,
    queue: JobQueueDep,
) -> FaqResponse:
    faq = _faq_row(session, principal.tenant_id, faq_id)
    values = body.model_dump(exclude_unset=True, exclude_none=True)
    text_changed = any(
        key in values and values[key] != getattr(faq, key) for key in ("pregunta", "respuesta")
    )
    for key, value in values.items():
        setattr(faq, key, value)
    if text_changed or not faq.is_active:
        _unindex(session, principal.tenant_id, faq)
    session.flush()
    if faq.is_active and faq.knowledge_source_id is None:
        try:
            await _index(session, principal.tenant_id, queue, faq)
        except KnowledgeError as exc:
            session.rollback()
            raise _knowledge_error(exc) from exc
    else:
        session.commit()
    return _faq(faq)


@router.delete(
    "/faqs/{faq_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    responses=error_responses(*AUTH_RESPONSES, (404, "`not_found`")),
)
def delete_faq(
    faq_id: uuid.UUID, principal: ConfigurationManagerDep, session: SessionDep
) -> Response:
    faq = _faq_row(session, principal.tenant_id, faq_id)
    _unindex(session, principal.tenant_id, faq)
    session.delete(faq)
    session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# --------------------------------------------------------------- promotions

Money = Annotated[Decimal, Field(gt=0, max_digits=12, decimal_places=2)]


class PromotionRequest(BaseModel):
    nombre: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=120)]
    descripcion: Annotated[str, StringConstraints(strip_whitespace=True, max_length=400)] | None = (
        None
    )
    uso: Literal["automatica", "cierre"] = "automatica"
    tipo: Literal["porcentaje", "monto_fijo", "nxm", "precio_cantidad"]
    valor: Money | None = None
    n: int | None = Field(default=None, ge=1, le=100)
    m: int | None = Field(default=None, ge=1, le=100)
    aplica_a: Literal["todo", "productos", "categorias"] = "todo"
    product_ids: list[uuid.UUID] = Field(default_factory=list, max_length=200)
    categorias: list[str] = Field(default_factory=list, max_length=50)
    monto_minimo: Money | None = None
    vigencia_horas: int | None = Field(default=None, ge=1, le=168)
    para_seguimiento: bool = False
    inicia_en: datetime | None = None
    termina_en: datetime | None = None
    is_active: bool = True

    @model_validator(mode="after")
    def _rules(self) -> PromotionRequest:
        """The database's CHECKs, said in a way the panel can show."""
        if self.tipo == "nxm" and not (self.n and self.m and self.n > self.m):
            raise ValueError("2x1 y similares: n mayor que m, los dos desde 1")
        if self.tipo == "porcentaje" and not (self.valor and self.valor <= 100):
            raise ValueError("porcentaje: valor entre 0 y 100")
        if self.tipo in ("monto_fijo", "precio_cantidad") and not self.valor:
            raise ValueError("falta el valor")
        if self.tipo == "precio_cantidad" and not (self.m and self.m >= 2):
            raise ValueError("precio por cantidad: m es el tamaño del grupo, desde 2")
        if self.uso == "cierre" and not self.vigencia_horas:
            raise ValueError("una promoción de cierre necesita vigencia_horas")
        if self.aplica_a == "productos" and not self.product_ids:
            raise ValueError("elige al menos un producto")
        if self.aplica_a == "categorias" and not self.categorias:
            raise ValueError("elige al menos una categoría")
        if self.inicia_en and self.termina_en and self.termina_en <= self.inicia_en:
            raise ValueError("termina_en debe ser posterior a inicia_en")
        return self


class PromotionResponse(PromotionRequest):
    id: uuid.UUID


class PromotionList(BaseModel):
    items: list[PromotionResponse]


def _promotion(row: Promotion) -> PromotionResponse:
    return PromotionResponse.model_validate(row, from_attributes=True)


def _check_products(session: SessionDep, tenant_id: uuid.UUID, body: PromotionRequest) -> None:
    products = ProductRepository(session, tenant_id)
    if any(products.get(pid) is None for pid in body.product_ids):
        raise ApiError(
            status_code=422, error="unknown_product", message="A product is not in your catalogue."
        )


def _promotion_row(session: SessionDep, tenant_id: uuid.UUID, promotion_id: uuid.UUID) -> Promotion:
    row = PromotionRepository(session, tenant_id).get(promotion_id)
    if row is None:
        raise _not_found()
    return row


@router.get("/promotions", response_model=PromotionList, responses=error_responses(*AUTH_RESPONSES))
def list_promotions(
    principal: ConfigurationManagerDep,
    session: SessionDep,
    vigentes: Annotated[bool, Query()] = False,
) -> PromotionList:
    repository = PromotionRepository(session, principal.tenant_id)
    rows = repository.in_force(now=datetime.now(UTC)) if vigentes else repository.list()
    return PromotionList(items=[_promotion(row) for row in rows])


@router.post(
    "/promotions",
    status_code=status.HTTP_201_CREATED,
    response_model=PromotionResponse,
    responses=error_responses(*AUTH_RESPONSES, (422, "`validation_error` or `unknown_product`")),
)
def create_promotion(
    body: PromotionRequest, principal: ConfigurationManagerDep, session: SessionDep
) -> PromotionResponse:
    _check_products(session, principal.tenant_id, body)
    row = PromotionRepository(session, principal.tenant_id).add(**body.model_dump())
    session.commit()
    _changed(principal.tenant_id)
    return _promotion(row)


@router.put(
    "/promotions/{promotion_id}",
    response_model=PromotionResponse,
    responses=error_responses(
        *AUTH_RESPONSES, (404, "`not_found`"), (422, "`validation_error` or `unknown_product`")
    ),
)
def update_promotion(
    promotion_id: uuid.UUID,
    body: PromotionRequest,
    principal: ConfigurationManagerDep,
    session: SessionDep,
) -> PromotionResponse:
    row = _promotion_row(session, principal.tenant_id, promotion_id)
    _check_products(session, principal.tenant_id, body)
    for key, value in body.model_dump().items():
        setattr(row, key, value)
    session.commit()
    _changed(principal.tenant_id)
    return _promotion(row)


@router.delete(
    "/promotions/{promotion_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    responses=error_responses(*AUTH_RESPONSES, (404, "`not_found`")),
)
def delete_promotion(
    promotion_id: uuid.UUID, principal: ConfigurationManagerDep, session: SessionDep
) -> Response:
    """Deleted, or only switched off if an order already used it (its snapshot stays)."""
    row = _promotion_row(session, principal.tenant_id, promotion_id)
    used = session.execute(
        select(Order.id)
        .where(Order.tenant_id == principal.tenant_id, Order.promotion_id == row.id)
        .limit(1)
    ).first()
    if used:
        row.is_active = False
    else:
        session.delete(row)
    session.commit()
    _changed(principal.tenant_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# ----------------------------------------------------------------- behavior


class FollowUp(BaseModel):
    activo: bool = False
    horas: int = Field(default=4, ge=1, le=20)
    max_intentos: int = Field(default=2, ge=1, le=3)


SystemText = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=1000)]


class BehaviorRequest(BaseModel):
    agent_name: (
        Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=40)] | None
    ) = None
    tone: Literal["profesional", "cercano", "juvenil"] | None = None
    ritmo: Literal["directo", "equilibrado", "paso_a_paso"] | None = None
    detalle: Literal["breve", "moderado", "extenso"] | None = None
    seguimiento: FollowUp | None = None
    nivel_inteligencia: Literal["economico", "medio", "mejor"] | None = None
    derivacion_pausa_horas: int | None = Field(default=None, ge=1, le=72)
    mensajes_sistema: dict[str, SystemText | None] | None = None
    # The profile by form (017, RF-010).
    tratamiento: Literal["tu", "usted"] | None = None
    emojis: Literal["no", "pocos", "si"] | None = None
    saludo: Annotated[str, StringConstraints(strip_whitespace=True, max_length=120)] | None = None
    politica_descuentos: Literal["no_ofrece", "solo_promociones", "negociable"] | None = None
    descuento_maximo_pct: Decimal | None = Field(default=None, ge=0, le=100, decimal_places=2)
    max_situaciones: int | None = Field(default=None, ge=1, le=10)


class BehaviorResponse(BaseModel):
    agent_name: str
    tone: str
    ritmo: str
    detalle: str
    seguimiento: FollowUp
    nivel_inteligencia: str
    niveles_permitidos: list[str]
    derivacion_pausa_horas: int
    activo: bool
    mensajes_sistema: dict[str, str]
    tratamiento: str
    emojis: str
    saludo: str | None
    politica_descuentos: str
    descuento_maximo_pct: Decimal | None
    max_situaciones: int


def _allowed_levels(session: SessionDep, tenant_id: uuid.UUID) -> list[str]:
    """Every level while billing is off (CLAUDE.md, "Cobro de la plataforma")."""
    return allowed_levels(session, tenant_id)


def _behavior(session: SessionDep, tenant_id: uuid.UUID) -> BehaviorResponse:
    agent = AgentConfigRepository(session, tenant_id).current()
    if agent is None:
        agent = AgentConfigRepository(session, tenant_id).upsert(agent_name="Leo")
        session.commit()
    stored = dict(agent.system_messages or {})
    return BehaviorResponse(
        agent_name=agent.agent_name,
        tone=agent.tone,
        ritmo=agent.ritmo,
        detalle=agent.detalle,
        seguimiento=FollowUp(
            activo=agent.seguimiento_activo,
            horas=agent.seguimiento_horas,
            max_intentos=agent.seguimiento_max,
        ),
        nivel_inteligencia=agent.nivel_inteligencia,
        niveles_permitidos=_allowed_levels(session, tenant_id),
        derivacion_pausa_horas=agent.derivacion_pausa_horas,
        activo=agent.is_active,
        mensajes_sistema={
            key: str(stored.get(key) or default) for key, default in DEFAULT_SYSTEM_MESSAGES.items()
        },
        tratamiento=agent.tratamiento,
        emojis=agent.emojis,
        saludo=agent.saludo,
        politica_descuentos=agent.politica_descuentos,
        descuento_maximo_pct=agent.descuento_maximo_pct,
        max_situaciones=agent.max_situaciones,
    )


@router.get(
    "/behavior", response_model=BehaviorResponse, responses=error_responses(*AUTH_RESPONSES)
)
def get_behavior(principal: ConfigurationManagerDep, session: SessionDep) -> BehaviorResponse:
    return _behavior(session, principal.tenant_id)


@router.put(
    "/behavior",
    response_model=BehaviorResponse,
    responses=error_responses(
        *AUTH_RESPONSES,
        (403, "`plan_limit`: the level is not in the plan (only with billing on)"),
        (422, "`unknown_message` or `unknown_placeholder`"),
    ),
)
def put_behavior(
    body: BehaviorRequest, principal: ConfigurationManagerDep, session: SessionDep
) -> BehaviorResponse:
    values = body.model_dump(exclude_unset=True)
    if (level := values.get("nivel_inteligencia")) and level not in _allowed_levels(
        session, principal.tenant_id
    ):
        raise ApiError(
            status_code=403, error="plan_limit", message="Your plan does not include that level."
        )
    follow_up = values.pop("seguimiento", None)
    if follow_up is not None:
        values.update(
            seguimiento_activo=follow_up["activo"],
            seguimiento_horas=follow_up["horas"],
            seguimiento_max=follow_up["max_intentos"],
        )
    messages = values.pop("mensajes_sistema", None)
    repository = AgentConfigRepository(session, principal.tenant_id)
    if messages is not None:
        agent = repository.current()
        current = dict(agent.system_messages or {}) if agent is not None else {}
        for key, text in messages.items():
            if key not in DEFAULT_SYSTEM_MESSAGES:
                raise ApiError(status_code=422, error="unknown_message", message=key)
            if text is None:
                current.pop(key, None)  # back to the default
                continue
            unknown = placeholders_in(text) - SYSTEM_MESSAGE_PLACEHOLDERS
            if unknown:
                raise ApiError(
                    status_code=422,
                    error="unknown_placeholder",
                    message=f"{key}: {', '.join(sorted(unknown))}",
                )
            current[key] = text
        values["system_messages"] = current
    if values.get("politica_descuentos") not in (None, "negociable"):
        values["descuento_maximo_pct"] = None
    if "saludo" in values and not values["saludo"]:
        values["saludo"] = None
    repository.upsert(**values)
    session.commit()
    _changed(principal.tenant_id)
    return _behavior(session, principal.tenant_id)


# --------------------------------------------------------------- categories


class CategoryRequest(BaseModel):
    nombre: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=80)]
    orden: int = Field(default=0, ge=0, le=1000)
    activa: bool = True


class CategoryResponse(CategoryRequest):
    id: uuid.UUID
    productos: int


class CategoryList(BaseModel):
    items: list[CategoryResponse]


def _categories(session: SessionDep, tenant_id: uuid.UUID) -> list[CategoryResponse]:
    counts: dict[uuid.UUID | None, int] = dict(
        session.execute(
            select(Product.category_id, func.count())
            .where(Product.tenant_id == tenant_id, Product.deleted_at.is_(None))
            .group_by(Product.category_id)
        )
        .tuples()
        .all()
    )
    rows = session.execute(
        select(ProductCategory)
        .where(ProductCategory.tenant_id == tenant_id)
        .order_by(ProductCategory.orden, ProductCategory.nombre)
    ).scalars()
    return [
        CategoryResponse(
            id=row.id,
            nombre=row.nombre,
            orden=row.orden,
            activa=row.activa,
            productos=int(counts.get(row.id, 0)),
        )
        for row in rows
    ]


@router.get("/categories", response_model=CategoryList, responses=error_responses(*AUTH_RESPONSES))
def list_categories(principal: ConfigurationManagerDep, session: SessionDep) -> CategoryList:
    return CategoryList(items=_categories(session, principal.tenant_id))


@router.put(
    "/categories/{category_id}",
    response_model=CategoryResponse,
    responses=error_responses(*AUTH_RESPONSES, (404, "`not_found`"), (409, "`category_exists`")),
)
def update_category(
    category_id: uuid.UUID,
    body: CategoryRequest,
    principal: ConfigurationManagerDep,
    session: SessionDep,
) -> CategoryResponse:
    """Rename, reorder or switch off. Renaming also renames the products' label."""
    repository = ProductCategoryRepository(session, principal.tenant_id)
    row = repository.get(category_id)
    if row is None:
        raise _not_found()
    other = repository.by_name(body.nombre)
    if other is not None and other.id != row.id:
        raise ApiError(status_code=409, error="category_exists", message="That name is taken.")
    if body.nombre != row.nombre:
        session.execute(
            update(Product)
            .where(Product.tenant_id == principal.tenant_id, Product.category_id == row.id)
            .values(category=body.nombre)
        )
    row.nombre, row.orden, row.activa = body.nombre, body.orden, body.activa
    session.commit()
    _changed(principal.tenant_id)
    return next(c for c in _categories(session, principal.tenant_id) if c.id == row.id)


# ----------------------------------------------------------------- overview


class OverviewSection(BaseModel):
    clave: str
    estado: str
    detalle: dict[str, str] = Field(default_factory=dict)


class OverviewStep(BaseModel):
    paso: str
    hecho: bool
    accion: str


class OverviewResponse(BaseModel):
    progreso: int
    secciones: list[OverviewSection]
    activacion: list[OverviewStep]
    siguiente_paso: str | None


@router.get(
    "/overview", response_model=OverviewResponse, responses=error_responses(*AUTH_RESPONSES)
)
def get_overview(principal: ConfigurationManagerDep, session: SessionDep) -> OverviewResponse:
    overview = build_overview(session, principal.tenant_id)
    return OverviewResponse(
        progreso=overview.progreso,
        secciones=[
            OverviewSection(clave=s.clave, estado=s.estado, detalle=dict(s.detalle))
            for s in overview.secciones
        ],
        activacion=[
            OverviewStep(paso=s.paso, hecho=s.hecho, accion=s.accion) for s in overview.activacion
        ],
        siguiente_paso=overview.siguiente_paso,
    )
