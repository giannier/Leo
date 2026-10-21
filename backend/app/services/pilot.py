"""Pilot mode: which customers the assistant answers (feature 019, phase C).

`agent_configs.piloto_pct` is the share of customers, 0 to 100, that go to the assistant; the rest
wait for the team in the inbox. The side is a function of the person (their contact id), not of
the message or of the day, so a customer is always answered by the same side and the share is the
same whoever looks.
"""

from __future__ import annotations

import uuid

from sqlalchemy.orm import Session

from app.models.business import AgentConfig

BUCKETS = 100


def bucket(contact_id: uuid.UUID) -> int:
    """0 to 99, stable for a contact."""
    return contact_id.int % BUCKETS


def share_of(session: Session, tenant_id: uuid.UUID) -> int:
    config = session.get(AgentConfig, tenant_id)
    return config.piloto_pct if config is not None else 100


def in_pilot(session: Session, tenant_id: uuid.UUID, contact_id: uuid.UUID) -> bool:
    """Whether the assistant answers this customer."""
    share = share_of(session, tenant_id)
    return share >= BUCKETS or bucket(contact_id) < share
