"""Business profile and agent configuration endpoints (TASK-010).

The `/tenant/business-profile` and `/tenant/agent-config` pairs from
`contracts/api.md`. Both are one row per tenant, created on first write.

`is_active` is absent from the agent payload on purpose: the contract routes
activation through `/agent/activate`, which has preconditions - the activation
checklist - that a configuration form does not evaluate.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Annotated, Any, Literal

from fastapi import APIRouter
from pydantic import BaseModel, Field, StringConstraints

from app.api.deps import ConfigurationManagerDep, MemberDep, SessionDep
from app.api.errors import ApiError, error_responses
from app.models.business import MAX_SALUDO
from app.repositories.tenant import TenantUserRepository
from app.services.wizard import WizardService

router = APIRouter(prefix="/tenant", tags=["wizard"])

# Spelled out rather than derived from the tuples in `app.models.business`:
# `Literal` needs its members at type-check time, and writing them here is what
# lets mypy and the generated OpenAPI schema both see the allowed values.
# `tests/unit/test_wizard_validation.py` asserts these stay in step with the
# CHECK constraints, so a divergence fails the build rather than production.
Industry = Literal[
    "restaurante",
    "instituto",
    "retail",
    "servicios",
    "salud",
    "tecnologia",
    "belleza",
    "inmobiliaria",
    "automotriz",
    "hogar",
    "turismo",
    "b2b",
    "otros",
]
Tone = Literal["profesional", "cercano", "juvenil"]
Objective = Literal["vender", "atender", "ambos"]
# What the screens offer: every capability of the platform.
Capability = Literal["atender", "vender", "cotizar", "agendar", "captar"]

ShortText = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=160)]
LongText = Annotated[str, StringConstraints(strip_whitespace=True, max_length=4000)]
# IANA zone name. Not validated against the database here: the list ships with
# the OS and drifts, and a wrong-but-well-formed value fails visibly at the
# first scheduled job rather than silently.
TimezoneName = Annotated[str, StringConstraints(strip_whitespace=True, max_length=64)]
CurrencyCode = Annotated[str, StringConstraints(strip_whitespace=True, pattern=r"^[A-Z]{3}$")]


class ConcurrencyAware(BaseModel):
    """Mixin for the optional last-write-wins check."""

    # The `updated_at` the client last saw. Optional: a client that does not
    # send it simply is not warned. Sending it never blocks the write -
    # Clarification 008 chose last-write-wins over pessimistic locking.
    expected_updated_at: datetime | None = None


class BusinessProfileRequest(ConcurrencyAware):
    business_name: ShortText | None = None
    industry: Industry | None = None
    description: LongText | None = None
    website: Annotated[str, StringConstraints(max_length=500)] | None = None
    social_links: dict[str, str] | None = None
    currency: CurrencyCode | None = None
    timezone: TimezoneName | None = None
    opening_hours: dict[str, list[list[str]]] | None = None


class BusinessProfileResponse(BaseModel):
    business_name: str | None
    industry: str | None
    description: str | None
    website: str | None
    social_links: dict[str, Any]
    currency: str
    timezone: str
    opening_hours: dict[str, Any]
    updated_at: datetime | None
    # True when this save landed on top of a newer version. The panel shows
    # "somebody else saved changes while you were editing"; the write still
    # happened.
    overwrote_newer: bool = False


class AgentConfigRequest(ConcurrencyAware):
    agent_name: ShortText | None = None
    tone: Tone | None = None
    objective: Objective | None = None
    # Kept as the migrated situation (017, RF-011); TASK-468 retires it.
    restrictions: LongText | None = None
    escalation_rules: list[str] | None = Field(default=None, max_length=20)
    tratamiento: Literal["tu", "usted"] | None = None
    emojis: Literal["no", "pocos", "si"] | None = None
    saludo: (
        Annotated[str, StringConstraints(strip_whitespace=True, max_length=MAX_SALUDO)] | None
    ) = None
    farewell_on_pause: LongText | None = None
    # What the agent does (feature 019): the template and the objective follow from it.
    capacidades: list[Capability] | None = Field(default=None, max_length=10)
    # What the agent asks to know a customer (`captar`), up to five. An empty list goes back to the
    # rubro's own questions; absent leaves them as they are (feature 019, phase E).
    preguntas_calificacion: (
        list[Annotated[str, StringConstraints(strip_whitespace=True, max_length=120)]] | None
    ) = Field(default=None, max_length=5)
    # Who a hot lead is assigned to and told (`captar`): a member of the team. Sent as null, nobody
    # in particular (it reaches the whole team); not sent, it stays as it is.
    destino_leads: uuid.UUID | None = None


class AgentConfigResponse(BaseModel):
    agent_name: str
    tone: str
    objective: str
    restrictions: str | None
    escalation_rules: list[str]
    tratamiento: str = "tu"
    emojis: str = "pocos"
    saludo: str | None = None
    capacidades: list[str] = Field(default_factory=lambda: ["atender", "vender"])
    # None: the rubro's questions are used.
    preguntas_calificacion: list[str] | None = None
    destino_leads: uuid.UUID | None = None
    farewell_on_pause: str | None
    is_active: bool
    updated_at: datetime | None
    overwrote_newer: bool = False


PROFILE_DEFAULTS = BusinessProfileResponse(
    business_name=None,
    industry=None,
    description=None,
    website=None,
    social_links={},
    currency="PEN",
    timezone="America/Lima",
    opening_hours={},
    updated_at=None,
)

AGENT_DEFAULTS = AgentConfigResponse(
    agent_name="Leo",
    tone="cercano",
    objective="ambos",
    restrictions=None,
    escalation_rules=["cliente_molesto", "pide_humano", "fuera_de_catalogo"],
    farewell_on_pause=None,
    is_active=False,
    updated_at=None,
)

AUTH_RESPONSES = (
    (401, "`not_authenticated`, `token_expired` or `invalid_token`"),
    (403, "`email_not_verified`, `no_active_tenant` or `tenant_access_denied`"),
)


def _profile_response(record: Any, *, overwrote_newer: bool = False) -> BusinessProfileResponse:
    if record is None:
        return PROFILE_DEFAULTS
    return BusinessProfileResponse(
        business_name=record.business_name,
        industry=record.industry,
        description=record.description,
        website=record.website,
        social_links=record.social_links,
        currency=record.currency,
        timezone=record.timezone,
        opening_hours=record.opening_hours,
        updated_at=record.updated_at,
        overwrote_newer=overwrote_newer,
    )


def _agent_response(
    record: Any, *, restrictions: str | None = None, overwrote_newer: bool = False
) -> AgentConfigResponse:
    if record is None:
        return AGENT_DEFAULTS.model_copy(update={"restrictions": restrictions})
    return AgentConfigResponse(
        agent_name=record.agent_name,
        tone=record.tone,
        objective=record.objective,
        restrictions=restrictions,
        tratamiento=record.tratamiento,
        emojis=record.emojis,
        saludo=record.saludo,
        capacidades=list(record.capacidades or ["atender", "vender"]),
        preguntas_calificacion=(
            list(record.preguntas_calificacion)
            if record.preguntas_calificacion is not None
            else None
        ),
        destino_leads=record.destino_leads,
        escalation_rules=record.escalation_rules,
        farewell_on_pause=record.farewell_on_pause,
        is_active=record.is_active,
        updated_at=record.updated_at,
        overwrote_newer=overwrote_newer,
    )


@router.get(
    "/business-profile",
    summary="Read the business identity",
    responses=error_responses(*AUTH_RESPONSES),
)
async def get_business_profile(
    principal: MemberDep, session: SessionDep
) -> BusinessProfileResponse:
    """Returns the column defaults when the wizard has not written a row yet.

    The alternative - 404 until first save - would make every client handle a
    state that carries no information the defaults do not already give.
    """
    return _profile_response(WizardService(session, principal.tenant_id).business_profile())


@router.put(
    "/business-profile",
    summary="Save the business identity",
    responses=error_responses((400, "`validation_error`"), *AUTH_RESPONSES),
)
async def put_business_profile(
    payload: BusinessProfileRequest,
    principal: ConfigurationManagerDep,
    session: SessionDep,
) -> BusinessProfileResponse:
    """Partial save: fields not sent are left alone.

    The wizard writes step by step, so a PUT carrying only the current step
    must not null out what an earlier one collected.
    """
    values = payload.model_dump(exclude_unset=True, exclude={"expected_updated_at"})
    outcome = WizardService(session, principal.tenant_id).save_business_profile(
        expected_updated_at=payload.expected_updated_at, **values
    )
    return _profile_response(outcome.record, overwrote_newer=outcome.overwrote_newer)


@router.get(
    "/agent-config",
    summary="Read the agent's personality and guardrails",
    responses=error_responses(*AUTH_RESPONSES),
)
async def get_agent_config(principal: MemberDep, session: SessionDep) -> AgentConfigResponse:
    service = WizardService(session, principal.tenant_id)
    return _agent_response(service.agent_config(), restrictions=service.migrated_rules())


@router.put(
    "/agent-config",
    summary="Save the agent's personality and guardrails",
    responses=error_responses((400, "`validation_error`"), *AUTH_RESPONSES),
)
async def put_agent_config(
    payload: AgentConfigRequest,
    principal: ConfigurationManagerDep,
    session: SessionDep,
) -> AgentConfigResponse:
    values = payload.model_dump(exclude_unset=True, exclude={"expected_updated_at"})
    destination = values.get("destino_leads")
    if (
        destination is not None
        and TenantUserRepository(session, principal.tenant_id).active_membership_for(destination)
        is None
    ):
        raise ApiError(
            status_code=400,
            error="validation_error",
            message="Esa persona no está en tu equipo.",
            fields={"destino_leads": "Elige a alguien de tu equipo."},
        )
    service = WizardService(session, principal.tenant_id)
    outcome = service.save_agent_config(expected_updated_at=payload.expected_updated_at, **values)
    return _agent_response(
        outcome.record,
        restrictions=service.migrated_rules(),
        overwrote_newer=outcome.overwrote_newer,
    )
