"""Saving what the agent does and re-deriving what follows (feature 019).

The rules live in `app/process/capabilities.py`; this is the writer: it stores the capabilities,
the derived objective and, while the template is automatic, the derived template.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterable
from typing import Any

from pydantic import ValidationError
from sqlalchemy.orm import Session

from app.process.capabilities import derive_objective, derive_template, normalize
from app.process.service import DEFAULT_CACHE
from app.process.templates import TEMPLATES
from app.repositories.business import AgentConfigRepository, BusinessProfileRepository
from app.repositories.catalog import CatalogRepository


def apply(session: Session, tenant_id: uuid.UUID, capabilities: Iterable[str]) -> list[str]:
    """Save the capabilities and re-derive the objective and, if it is automatic, the template."""
    chosen = normalize(capabilities)
    agents = AgentConfigRepository(session, tenant_id)
    current = agents.current()
    values: dict[str, object] = {"capacidades": chosen, "objective": derive_objective(chosen)}
    if current is None or not current.plantilla_manual:
        profile = BusinessProfileRepository(session, tenant_id).current()
        rubro = (
            CatalogRepository(session).rubro(profile.industry)
            if profile and profile.industry
            else None
        )
        key = derive_template(chosen, rubro.plantilla_sugerida if rubro else None)
        if current is None or current.process_template_key != key:
            values["process_template_key"] = key
            values["process_options"] = _options_for(
                key, current.process_options if current else {}
            )
    agents.upsert(**values)
    DEFAULT_CACHE.invalidate(tenant_id)
    return chosen


def automatic_again(session: Session, tenant_id: uuid.UUID) -> str:
    """Give the template back to the platform: derive it from the capabilities again."""
    agents = AgentConfigRepository(session, tenant_id)
    current = agents.current()
    chosen = normalize(current.capacidades if current else None)
    profile = BusinessProfileRepository(session, tenant_id).current()
    rubro = (
        CatalogRepository(session).rubro(profile.industry) if profile and profile.industry else None
    )
    key = derive_template(chosen, rubro.plantilla_sugerida if rubro else None)
    agents.upsert(
        plantilla_manual=False,
        process_template_key=key,
        process_options=_options_for(key, current.process_options if current else {}),
    )
    DEFAULT_CACHE.invalidate(tenant_id)
    return key


def _options_for(template_key: str, previous: dict[str, Any] | None) -> dict[str, Any]:
    """The options kept if they still fit the new template, otherwise the template's defaults."""
    template = TEMPLATES[template_key]
    try:
        return template.validate_options(previous or {}).model_dump(mode="json", exclude_none=True)
    except ValidationError:
        return {}
