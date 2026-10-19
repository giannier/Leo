"""The CRM of one business (Feature 012)."""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from datetime import datetime
from typing import Any

from sqlalchemy import func, or_, select
from sqlalchemy.dialects.postgresql import insert

from app.models.channel import Contact
from app.models.crm import (
    DEFAULT_LOST_REASONS,
    DEFAULT_STAGE_LABELS,
    SEMANTIC_STAGES,
    Lead,
    LeadEvent,
    LostReason,
    PipelineStage,
)
from app.repositories.base import BaseRepository

# Room between the fixed stages for the business's own (positions 10, 20, ...).
STAGE_STEP = 10


class PipelineStageRepository(BaseRepository[PipelineStage]):
    model = PipelineStage

    def ensure_defaults(self) -> None:
        """The five fixed stages and the default lost reasons, created once."""
        self._session.execute(
            insert(PipelineStage)
            .values(
                [
                    {
                        "tenant_id": self._tenant_id,
                        "key": key,
                        "label": DEFAULT_STAGE_LABELS[key],
                        "position": (index + 1) * STAGE_STEP,
                        "semantic": key,
                    }
                    for index, key in enumerate(SEMANTIC_STAGES)
                ]
            )
            .on_conflict_do_nothing(constraint="uq_pipeline_stages_tenant_id_key")
        )
        self._session.execute(
            insert(LostReason)
            .values(
                [
                    {"tenant_id": self._tenant_id, "key": key, "label": label}
                    for key, label in DEFAULT_LOST_REASONS.items()
                ]
            )
            .on_conflict_do_nothing(index_elements=["tenant_id", "key"])
        )
        self._session.flush()

    def ordered(self, *, active_only: bool = False) -> list[PipelineStage]:
        statement = self._scoped().order_by(PipelineStage.position, PipelineStage.key)
        if active_only:
            statement = statement.where(PipelineStage.is_active.is_(True))
        return list(self._session.execute(statement).scalars())

    def by_semantic(self, semantic: str) -> PipelineStage | None:
        statement = self._scoped().where(PipelineStage.semantic == semantic)
        return self._session.execute(statement).scalar_one_or_none()

    def by_key(self, key: str) -> PipelineStage | None:
        statement = self._scoped().where(PipelineStage.key == key)
        return self._session.execute(statement).scalar_one_or_none()


class LostReasonRepository(BaseRepository[LostReason]):
    model = LostReason

    def ordered(self, *, active_only: bool = False) -> list[LostReason]:
        statement = self._scoped().order_by(LostReason.created_at, LostReason.key)
        if active_only:
            statement = statement.where(LostReason.is_active.is_(True))
        return list(self._session.execute(statement).scalars())

    def by_key(self, key: str) -> LostReason | None:
        statement = self._scoped().where(LostReason.key == key)
        return self._session.execute(statement).scalar_one_or_none()


class LeadRepository(BaseRepository[Lead]):
    model = Lead

    def active_for_contact(self, contact_id: uuid.UUID) -> Lead | None:
        statement = self._scoped().where(Lead.contact_id == contact_id, Lead.status == "active")
        return self._session.execute(statement).scalar_one_or_none()

    def latest_for_contact(self, contact_id: uuid.UUID) -> Lead | None:
        statement = (
            self._scoped()
            .where(Lead.contact_id == contact_id)
            # Two leads of one transaction share created_at: the newer one is still open or
            # was closed later.
            .order_by(Lead.created_at.desc(), Lead.closed_at.desc().nulls_first())
            .limit(1)
        )
        return self._session.execute(statement).scalar_one_or_none()

    def board(
        self,
        *,
        stage_id: uuid.UUID | None = None,
        status: Sequence[str] = ("active",),
        assigned_to: uuid.UUID | None = None,
        unassigned: bool = False,
        search: str | None = None,
        idle_before: datetime | None = None,
        closed_since: datetime | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> tuple[list[tuple[Lead, Contact]], int]:
        """Leads with their contact, newest activity first, and how many matched."""
        criteria: list[Any] = [Lead.status.in_(list(status))]
        if stage_id is not None:
            criteria.append(Lead.stage_id == stage_id)
        if assigned_to is not None:
            criteria.append(Lead.assigned_to == assigned_to)
        if unassigned:
            criteria.append(Lead.assigned_to.is_(None))
        if idle_before is not None:
            criteria.append(Lead.last_activity_at < idle_before)
        if closed_since is not None:
            criteria.append(or_(Lead.closed_at.is_(None), Lead.closed_at >= closed_since))
        if search:
            pattern = f"%{search.replace('%', '').replace('_', '')}%"
            criteria.append(
                or_(
                    Contact.profile_name.ilike(pattern),
                    Contact.custom_data["nombres"].astext.ilike(pattern),
                    Contact.phone_e164.ilike(pattern),
                    Lead.product_interest.ilike(pattern),
                )
            )
        base = (
            select(Lead, Contact)
            .join(Contact, Contact.id == Lead.contact_id)
            .where(self._tenant_predicate, Contact.tenant_id == self._tenant_id, *criteria)
        )
        total = self._session.execute(
            select(func.count()).select_from(base.subquery())
        ).scalar_one()
        rows = self._session.execute(
            base.order_by(Lead.last_activity_at.desc(), Lead.id).limit(limit).offset(offset)
        ).all()
        return [(row[0], row[1]) for row in rows], int(total)


class LeadEventRepository(BaseRepository[LeadEvent]):
    model = LeadEvent

    def for_lead(self, lead_id: uuid.UUID) -> list[LeadEvent]:
        statement = (
            self._scoped().where(LeadEvent.lead_id == lead_id).order_by(LeadEvent.created_at)
        )
        return list(self._session.execute(statement).scalars())
