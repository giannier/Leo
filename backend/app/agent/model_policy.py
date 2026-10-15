"""Which model answers a tenant (Feature 004, TASK-445, C-02 and Milestone B decision 4).

The business chooses an intelligence level (`agent_configs.nivel_inteligencia`):
`economico`, `medio` or `mejor`. Here it becomes a model:

* **Own model.** A tenant that brings its own credential (`tenant_llm_settings`) has
  no level: their model is theirs.
* **The plan** limits the levels only while billing is enforced: a level the plan
  does not allow falls to the highest one it does (the free trial: `economico`).
  With `BILLING_ENFORCED=false` every level is allowed.
* **The model** of each level comes from settings (`LLM_MODEL_<NIVEL>`); unset, it
  is the profile's chat model. What each level costs in credits is
  `LLM_CREDITS_<NIVEL>`, charged by Feature 009.
"""

from __future__ import annotations

import dataclasses
import uuid
from collections.abc import Sequence

from sqlalchemy.orm import Session

from app.core.config import Settings, get_settings
from app.models.business import INTELLIGENCE_LEVELS, AgentConfig
from app.models.llm_settings import TenantLLMSettings
from app.models.tenant import Tenant
from app.providers.llm.base import LLMProvider
from app.providers.llm.factory import build_provider, platform_profile, provider_for_tenant
from app.repositories.plan import PlanRepository
from app.services.trial import billing_enforced

DEFAULT_LEVEL = "economico"


def allowed_levels(session: Session, tenant_id: uuid.UUID) -> list[str]:
    """The levels this tenant may use. Every one while billing is off."""
    if not billing_enforced():
        return list(INTELLIGENCE_LEVELS)
    tenant = session.get(Tenant, tenant_id)
    plan = PlanRepository(session).get(tenant.plan_id) if tenant else None
    return list(plan.allowed_intelligence) if plan else [DEFAULT_LEVEL]


def effective_level(configured: str | None, allowed: Sequence[str]) -> str:
    """The configured level, or the highest allowed one below it."""
    level = configured if configured in INTELLIGENCE_LEVELS else DEFAULT_LEVEL
    if level in allowed:
        return level
    ceiling = INTELLIGENCE_LEVELS.index(level)
    below = [name for name in INTELLIGENCE_LEVELS[: ceiling + 1] if name in allowed]
    if below:
        return below[-1]
    return DEFAULT_LEVEL


def model_for(level: str, settings: Settings | None = None) -> str:
    settings = settings or get_settings()
    configured = {
        "economico": settings.llm_model_economico,
        "medio": settings.llm_model_medio,
        "mejor": settings.llm_model_mejor,
    }.get(level)
    return configured or settings.openai_chat_model


def credits_for(level: str | None, settings: Settings | None = None) -> int:
    """What one reply at this level costs (1 without a level: the tenant's own model)."""
    if level is None:
        return 1
    settings = settings or get_settings()
    return {
        "economico": settings.llm_credits_economico,
        "medio": settings.llm_credits_medio,
        "mejor": settings.llm_credits_mejor,
    }.get(level, 1)


def tenant_level(session: Session, tenant_id: uuid.UUID) -> str | None:
    """The level a tenant's turns run at, or None when it brings its own model."""
    own = session.get(TenantLLMSettings, tenant_id)
    if own is not None and own.mode != "platform":
        return None
    config = session.get(AgentConfig, tenant_id)
    configured = config.nivel_inteligencia if config is not None else None
    return effective_level(configured, allowed_levels(session, tenant_id))


def chat_provider_for(
    session: Session, tenant_id: uuid.UUID, settings: Settings | None = None
) -> tuple[LLMProvider, str | None]:
    """The chat provider for a tenant's turn, and the level it runs at."""
    settings = settings or get_settings()
    own = session.get(TenantLLMSettings, tenant_id)
    level = tenant_level(session, tenant_id)
    if level is None or settings.llm_profile != "openai":
        return provider_for_tenant(own, settings=settings), level
    model = model_for(level, settings)
    if model == settings.openai_chat_model:
        return provider_for_tenant(own, settings=settings), level
    profile = dataclasses.replace(platform_profile(settings), chat_model=model)
    return build_provider(profile), level
