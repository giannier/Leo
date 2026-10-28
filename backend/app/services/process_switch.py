"""Move a business to the universal process, or back (feature 020, the switch).

The universal process is hidden from businesses until it passes the real-model evals (F5): only the
platform administration moves a business to it, one at a time, with Giannier's go-ahead. Going back
gives the template to the platform again (the one its capabilities derive), exactly as the panel's
«que Leo elija» does. Both are audited.

Open conversations keep their stage, order, quote and customer data: the two processes share the
stage engine and the facts (feature 020, spec §7).
"""

from __future__ import annotations

import uuid
from typing import Any

from pydantic import ValidationError
from sqlalchemy.orm import Session

from app.models.tenant import Tenant
from app.process.service import DEFAULT_CACHE
from app.process.templates import TEMPLATES, UNIVERSAL
from app.repositories.business import AgentConfigRepository
from app.services.admin import record_audit
from app.services.capabilities import automatic_again


class NoAgent(LookupError):
    """There is no such business."""


def _require(session: Session, tenant_id: uuid.UUID) -> None:
    if session.get(Tenant, tenant_id) is None:
        raise NoAgent(str(tenant_id))


def to_universal(session: Session, tenant_id: uuid.UUID, *, actor_id: uuid.UUID) -> str:
    """Put the business on the universal process, keeping the options that still fit it."""
    _require(session, tenant_id)
    agents = AgentConfigRepository(session, tenant_id)
    current = agents.current()
    previous = current.process_template_key if current is not None else None
    agents.upsert(
        process_template_key=UNIVERSAL,
        process_options=_kept_options(current.process_options if current is not None else None),
        # Chosen, not derived: changing the capabilities must not take it off the process.
        plantilla_manual=True,
    )
    record_audit(
        session,
        actor_id=actor_id,
        tenant_id=tenant_id,
        action="process.universal",
        detail={"antes": previous, "despues": UNIVERSAL},
    )
    session.commit()
    DEFAULT_CACHE.invalidate(tenant_id)
    return UNIVERSAL


def back_to_templates(session: Session, tenant_id: uuid.UUID, *, actor_id: uuid.UUID) -> str:
    """Take the business off the universal process: the template follows its capabilities again."""
    _require(session, tenant_id)
    current = AgentConfigRepository(session, tenant_id).current()
    previous = current.process_template_key if current is not None else None
    key = automatic_again(session, tenant_id)
    record_audit(
        session,
        actor_id=actor_id,
        tenant_id=tenant_id,
        action="process.templates",
        detail={"antes": previous, "despues": key},
    )
    session.commit()
    DEFAULT_CACHE.invalidate(tenant_id)
    return key


def _kept_options(previous: dict[str, Any] | None) -> dict[str, Any]:
    """The business's options if the universal process takes them (paths, data, payment)."""
    try:
        return (
            TEMPLATES[UNIVERSAL]
            .validate_options(previous or {})
            .model_dump(mode="json", exclude_none=True)
        )
    except ValidationError:
        return {}
