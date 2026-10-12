"""The effective sales-process configuration of a tenant (Feature 004, TASK-414).

`ProcessConfigService.effective(tenant_id)` answers one question for the turn
pipeline and the prompt builder: *what process does this business run today?*
That is the template (code), the tenant's options laid over its defaults, the
instruction text of every stage (the tenant's edit, or the template's own), the
behaviour settings (tone, pace, detail), and the business information the prompt
shows in compact form.

It is read once per turn, so the result is cached per tenant for 60 seconds. The
cache holds **plain, session-independent values** (frozen dataclasses, validated
schemas, detached promotion copies) and never an ORM row bound to a session that
has since been closed. A write (TASK-435) calls `invalidate()`; any other worker
sees it within the TTL, which is the staleness the plan accepts.

This is the read side only. Saving options, overrides and "restore default"
arrives with TASK-435.
"""

from __future__ import annotations

import logging
import time
import uuid
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from types import MappingProxyType
from typing import Any

from pydantic import ValidationError
from sqlalchemy import inspect
from sqlalchemy.orm import Session

from app.models.business import AgentConfig, BusinessProfile
from app.models.sales_config import Promotion
from app.process.facts import FactsConfig
from app.process.templates import TEMPLATES, get_template
from app.process.templates.base import EffectiveOptions, Stage, Template
from app.repositories.business import AgentConfigRepository, BusinessProfileRepository
from app.repositories.payment import PaymentMethodRepository
from app.repositories.sales_config import (
    BusinessInfoRepository,
    ProcessStageOverrideRepository,
    PromotionRepository,
)
from app.schemas.business_info import (
    SECTION_SCHEMAS,
    Contacto,
    Devoluciones,
    Entregas,
    Locales,
    TiposCliente,
)

logger = logging.getLogger(__name__)

CACHE_TTL_SECONDS = 60.0
DEFAULT_TEMPLATE_KEY = "productos"
DEFAULT_AGENT_NAME = "Leo"
DEFAULT_TONE = "cercano"
DEFAULT_PACE = "equilibrado"
DEFAULT_DETAIL = "moderado"
DEFAULT_CURRENCY = "PEN"
DEFAULT_TIMEZONE = "America/Lima"


@dataclass(frozen=True)
class Behavior:
    """How the assistant talks (`agent_configs`)."""

    agent_name: str = DEFAULT_AGENT_NAME
    tone: str = DEFAULT_TONE
    ritmo: str = DEFAULT_PACE
    detalle: str = DEFAULT_DETAIL
    # `agent_configs.restrictions`, the tenant's "additional rules".
    reglas_adicionales: str | None = None


@dataclass(frozen=True)
class BusinessInfoView:
    """The five structured sections, parsed. A missing or invalid one is None."""

    locales: Locales | None = None
    entregas: Entregas | None = None
    tipos_cliente: TiposCliente | None = None
    devoluciones: Devoluciones | None = None
    contacto: Contacto | None = None


@dataclass(frozen=True)
class EffectiveProcessConfig:
    """Everything a turn needs to know about the tenant's process."""

    tenant_id: uuid.UUID
    template: Template
    options: EffectiveOptions
    # The text of every stage as it runs: the tenant's edit if it has one.
    stage_instructions: Mapping[Stage, str]
    customized_stages: frozenset[Stage] = frozenset()
    behavior: Behavior = field(default_factory=Behavior)
    business_name: str | None = None
    business_description: str | None = None
    industry: str | None = None
    currency: str = DEFAULT_CURRENCY
    timezone: str = DEFAULT_TIMEZONE
    opening_hours: Mapping[str, Any] = field(default_factory=dict)
    info: BusinessInfoView = field(default_factory=BusinessInfoView)
    # Active payment methods, by code (`yape`, `transferencia`...).
    payment_methods: tuple[str, ...] = ()
    # Promotions in force when the config was loaded (detached copies).
    promotions: tuple[Promotion, ...] = ()

    def instructions_for(self, stage: Stage) -> str:
        return self.stage_instructions[stage]

    def facts_config(self) -> FactsConfig:
        """What `FactsLoader` needs, so it adds no query of its own."""
        return FactsConfig(
            options=self.options, entregas=self.info.entregas, promotions=self.promotions
        )


class ProcessConfigCache:
    """Per-tenant cache with a time to live. Process-local, like a worker."""

    def __init__(
        self, ttl_seconds: float = CACHE_TTL_SECONDS, clock: Callable[[], float] = time.monotonic
    ) -> None:
        self._ttl = ttl_seconds
        self._clock = clock
        self._entries: dict[uuid.UUID, tuple[float, EffectiveProcessConfig]] = {}

    def get(self, tenant_id: uuid.UUID) -> EffectiveProcessConfig | None:
        entry = self._entries.get(tenant_id)
        if entry is None:
            return None
        stored_at, config = entry
        if self._clock() - stored_at >= self._ttl:
            del self._entries[tenant_id]
            return None
        return config

    def put(self, config: EffectiveProcessConfig) -> None:
        self._entries[config.tenant_id] = (self._clock(), config)

    def invalidate(self, tenant_id: uuid.UUID) -> None:
        self._entries.pop(tenant_id, None)

    def clear(self) -> None:
        self._entries.clear()


# One cache per worker process, shared by every service built without its own.
DEFAULT_CACHE = ProcessConfigCache()


def _detach(promotion: Promotion) -> Promotion:
    """A transient copy of a promotion row, safe to keep after the session closes."""
    columns = {attr.key: getattr(promotion, attr.key) for attr in inspect(Promotion).column_attrs}
    return Promotion(**columns)


def _template_for(config: AgentConfig | None) -> Template:
    key = config.process_template_key if config is not None else DEFAULT_TEMPLATE_KEY
    if key not in TEMPLATES:
        # A key that no longer exists must not take every conversation down.
        logger.warning("unknown process template; using the default", extra={"template": key})
        key = DEFAULT_TEMPLATE_KEY
    return get_template(key)


def _options_for(template: Template, config: AgentConfig | None) -> EffectiveOptions:
    raw = config.process_options if config is not None else {}
    try:
        return template.resolve(template.validate_options(raw or {}))
    except ValidationError:
        # Stored options that do not fit the template (it changed, or the row
        # was edited by hand): run on the defaults rather than fail the turn.
        logger.warning(
            "invalid process options; using the defaults", extra={"template": template.key}
        )
        return template.resolve()


def _info_view(sections: Mapping[str, dict[str, Any]]) -> BusinessInfoView:
    parsed: dict[str, Any] = {}
    for name, data in sections.items():
        schema = SECTION_SCHEMAS.get(name)
        if schema is None:
            continue
        try:
            parsed[name] = schema.model_validate(data)
        except ValidationError:
            logger.warning("invalid business info section; skipping it", extra={"section": name})
    return BusinessInfoView(**parsed)


class ProcessConfigService:
    """Reads the effective process configuration of a tenant."""

    def __init__(
        self,
        session: Session,
        *,
        cache: ProcessConfigCache | None = None,
        now: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._session = session
        self._cache = cache if cache is not None else DEFAULT_CACHE
        self._now = now

    def effective(self, tenant_id: uuid.UUID) -> EffectiveProcessConfig:
        """The configuration of `tenant_id`, from the cache if it is under 60 s old."""
        cached = self._cache.get(tenant_id)
        if cached is not None:
            return cached
        config = self._load(tenant_id)
        self._cache.put(config)
        return config

    def invalidate(self, tenant_id: uuid.UUID) -> None:
        """Forget the cached configuration: call it after a write."""
        self._cache.invalidate(tenant_id)

    def _load(self, tenant_id: uuid.UUID) -> EffectiveProcessConfig:
        agent = AgentConfigRepository(self._session, tenant_id).current()
        profile: BusinessProfile | None = BusinessProfileRepository(
            self._session, tenant_id
        ).current()

        template = _template_for(agent)
        options = _options_for(template, agent)

        overrides = ProcessStageOverrideRepository(self._session, tenant_id).for_template(
            template.key
        )
        texts: dict[Stage, str] = {}
        customized: set[Stage] = set()
        for stage in Stage:
            text = template.stage(stage).instrucciones
            override = overrides.get(stage.value)
            if override is not None and override.instrucciones:
                text = override.instrucciones
                customized.add(stage)
            texts[stage] = text

        sections = BusinessInfoRepository(self._session, tenant_id).sections()
        methods = PaymentMethodRepository(self._session, tenant_id).list_active()
        promotions = PromotionRepository(self._session, tenant_id).in_force(now=self._now())

        return EffectiveProcessConfig(
            tenant_id=tenant_id,
            template=template,
            options=options,
            stage_instructions=MappingProxyType(texts),
            customized_stages=frozenset(customized),
            behavior=_behavior(agent),
            business_name=profile.business_name if profile else None,
            business_description=profile.description if profile else None,
            industry=profile.industry if profile else None,
            currency=(profile.currency if profile else None) or DEFAULT_CURRENCY,
            timezone=(profile.timezone if profile else None) or DEFAULT_TIMEZONE,
            opening_hours=dict(profile.opening_hours) if profile and profile.opening_hours else {},
            info=_info_view(sections),
            payment_methods=tuple(method.method for method in methods),
            promotions=tuple(_detach(promotion) for promotion in promotions),
        )


def _behavior(agent: AgentConfig | None) -> Behavior:
    if agent is None:
        return Behavior()
    return Behavior(
        agent_name=agent.agent_name or DEFAULT_AGENT_NAME,
        tone=agent.tone or DEFAULT_TONE,
        ritmo=agent.ritmo or DEFAULT_PACE,
        detalle=agent.detalle or DEFAULT_DETAIL,
        reglas_adicionales=agent.restrictions or None,
    )
