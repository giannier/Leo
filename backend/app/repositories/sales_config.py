"""Business information, FAQs, promotions and stage overrides (Feature 004).

Everything here is configuration the tenant owns, read by the prompt builder
and the pricing code on every turn, so each query starts from `_scoped()`.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from datetime import datetime
from typing import Any

from sqlalchemy import or_

from app.models.sales_config import BusinessInfo, Faq, ProcessStageOverride, Promotion
from app.repositories.base import BaseRepository


class BusinessInfoRepository(BaseRepository[BusinessInfo]):
    """The structured sections of one tenant's business information."""

    model = BusinessInfo

    def get(self, entity_id: uuid.UUID) -> BusinessInfo | None:
        """Not available: rows are keyed by `(tenant_id, section)`, not an id."""
        raise NotImplementedError(
            f"{type(self).__name__} is keyed by section, not by a row id. "
            "Use section() to read and upsert() to write."
        )

    def section(self, section: str) -> BusinessInfo | None:
        statement = self._scoped().where(BusinessInfo.section == section)
        return self._session.execute(statement).scalar_one_or_none()

    def sections(self) -> dict[str, dict[str, Any]]:
        """Every section this tenant has saved, by name."""
        return {row.section: row.data for row in self.list()}

    def upsert(self, section: str, data: dict[str, Any]) -> BusinessInfo:
        """Replace one section's data, creating the row on first save."""
        row = self.section(section)
        if row is None:
            return self.add(section=section, data=data)
        row.data = data
        self._session.flush()
        return row


class FaqRepository(BaseRepository[Faq]):
    """The questions and answers one tenant has written."""

    model = Faq

    def active(self) -> Sequence[Faq]:
        """Active FAQs in the order the tenant arranged them."""
        statement = (
            self._scoped().where(Faq.is_active.is_(True)).order_by(Faq.orden, Faq.created_at)
        )
        return self._session.execute(statement).scalars().all()


class PromotionRepository(BaseRepository[Promotion]):
    """The offers one tenant has configured."""

    model = Promotion

    def in_force(self, *, now: datetime) -> Sequence[Promotion]:
        """Active promotions whose date window contains `now`.

        A missing bound means "no limit on that side". Whether a `cierre`
        promotion was offered to *this* customer is conversation state, not a
        property of the row, and is decided by the pricing code.
        """
        statement = self._scoped().where(
            Promotion.is_active.is_(True),
            or_(Promotion.inicia_en.is_(None), Promotion.inicia_en <= now),
            or_(Promotion.termina_en.is_(None), Promotion.termina_en > now),
        )
        return self._session.execute(statement).scalars().all()


class ProcessStageOverrideRepository(BaseRepository[ProcessStageOverride]):
    """The stage instructions one tenant has edited."""

    model = ProcessStageOverride

    def get(self, entity_id: uuid.UUID) -> ProcessStageOverride | None:
        """Not available: rows are keyed by template and stage, not an id."""
        raise NotImplementedError(
            f"{type(self).__name__} is keyed by (template, stage), not by a row id. "
            "Use stage() to read and upsert() to write."
        )

    def stage(self, template_key: str, stage_key: str) -> ProcessStageOverride | None:
        statement = self._scoped().where(
            ProcessStageOverride.template_key == template_key,
            ProcessStageOverride.stage_key == stage_key,
        )
        return self._session.execute(statement).scalar_one_or_none()

    def for_template(self, template_key: str) -> dict[str, ProcessStageOverride]:
        """Every edited stage of one template, by stage key."""
        statement = self._scoped().where(ProcessStageOverride.template_key == template_key)
        rows = self._session.execute(statement).scalars().all()
        return {row.stage_key: row for row in rows}

    def upsert(self, template_key: str, stage_key: str, **values: Any) -> ProcessStageOverride:
        """Create the override or update the fields supplied."""
        row = self.stage(template_key, stage_key)
        if row is None:
            return self.add(template_key=template_key, stage_key=stage_key, **values)
        for field, value in values.items():
            setattr(row, field, value)
        self._session.flush()
        return row
