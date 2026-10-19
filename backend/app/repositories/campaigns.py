"""Templates, opt-ins, tags and campaigns of one business (Feature 011)."""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from datetime import datetime

from sqlalchemy import delete, func, select, update
from sqlalchemy.dialects.postgresql import insert

from app.models.campaigns import (
    Campaign,
    CampaignRecipient,
    ContactOptin,
    ContactTag,
    MessageTemplate,
    OptinImport,
)
from app.repositories.base import BaseRepository

# A delivery report never moves a recipient backwards (a late "sent" after "read").
RECIPIENT_RANK = {
    "pending": 0,
    "skipped": 0,
    "failed": 1,
    "sent": 2,
    "delivered": 3,
    "read": 4,
    "replied": 5,
}


class MessageTemplateRepository(BaseRepository[MessageTemplate]):
    model = MessageTemplate

    def ordered(self) -> Sequence[MessageTemplate]:
        statement = self._scoped().order_by(MessageTemplate.created_at.desc(), MessageTemplate.id)
        return self._session.execute(statement).scalars().all()

    def by_name(self, name: str, language: str) -> MessageTemplate | None:
        statement = self._scoped().where(
            MessageTemplate.name == name, MessageTemplate.language == language
        )
        return self._session.execute(statement).scalar_one_or_none()

    def names(self) -> set[str]:
        statement = select(MessageTemplate.name).where(self._tenant_predicate)
        return set(self._session.execute(statement).scalars())


class ContactOptinRepository(BaseRepository[ContactOptin]):
    model = ContactOptin

    def for_contact(self, contact_id: uuid.UUID) -> ContactOptin | None:
        statement = self._scoped().where(ContactOptin.contact_id == contact_id)
        return self._session.execute(statement).scalar_one_or_none()

    def for_contacts(self, contact_ids: Sequence[uuid.UUID]) -> dict[uuid.UUID, ContactOptin]:
        if not contact_ids:
            return {}
        statement = self._scoped().where(ContactOptin.contact_id.in_(list(contact_ids)))
        return {row.contact_id: row for row in self._session.execute(statement).scalars()}


class OptinImportRepository(BaseRepository[OptinImport]):
    model = OptinImport

    def imported_since(self, since: datetime) -> int:
        statement = select(func.coalesce(func.sum(OptinImport.imported), 0)).where(
            self._tenant_predicate, OptinImport.created_at >= since
        )
        return int(self._session.execute(statement).scalar_one())


class ContactTagRepository(BaseRepository[ContactTag]):
    model = ContactTag

    def for_contacts(self, contact_ids: Sequence[uuid.UUID]) -> dict[uuid.UUID, list[str]]:
        if not contact_ids:
            return {}
        statement = (
            select(ContactTag.contact_id, ContactTag.tag)
            .where(self._tenant_predicate, ContactTag.contact_id.in_(list(contact_ids)))
            .order_by(ContactTag.tag)
        )
        tags: dict[uuid.UUID, list[str]] = {}
        for contact_id, tag in self._session.execute(statement).all():
            tags.setdefault(contact_id, []).append(tag)
        return tags

    def all_tags(self) -> list[tuple[str, int]]:
        statement = (
            select(ContactTag.tag, func.count())
            .where(self._tenant_predicate)
            .group_by(ContactTag.tag)
            .order_by(ContactTag.tag)
        )
        return [(tag, int(count)) for tag, count in self._session.execute(statement).all()]

    def replace(self, contact_id: uuid.UUID, tags: Sequence[str]) -> None:
        self._session.execute(
            delete(ContactTag).where(self._tenant_predicate, ContactTag.contact_id == contact_id)
        )
        self.add_tags(contact_id, tags)

    def add_tags(self, contact_id: uuid.UUID, tags: Sequence[str]) -> None:
        if not tags:
            return
        self._session.execute(
            insert(ContactTag)
            .values(
                [{"tenant_id": self._tenant_id, "contact_id": contact_id, "tag": t} for t in tags]
            )
            .on_conflict_do_nothing()
        )
        self._session.flush()


class CampaignRepository(BaseRepository[Campaign]):
    model = Campaign

    def ordered(self) -> Sequence[Campaign]:
        statement = self._scoped().order_by(Campaign.created_at.desc(), Campaign.id)
        return self._session.execute(statement).scalars().all()

    def active(self) -> Sequence[Campaign]:
        statement = self._scoped().where(Campaign.status.in_(("scheduled", "sending")))
        return self._session.execute(statement).scalars().all()

    def started_since(self, since: datetime) -> int:
        """Campaigns that went out (or are going out) since a date, for the plan's cap."""
        return self.count(
            Campaign.status.in_(("scheduled", "sending", "paused", "completed")),
            func.coalesce(Campaign.started_at, Campaign.scheduled_at, Campaign.created_at) >= since,
        )

    def using_template(self, template_id: uuid.UUID) -> int:
        return self.count(Campaign.template_id == template_id)


class CampaignRecipientRepository(BaseRepository[CampaignRecipient]):
    model = CampaignRecipient

    def snapshot(
        self, campaign_id: uuid.UUID, contact_ids: Sequence[uuid.UUID], *, ab: bool = False
    ) -> int:
        """Freeze who gets the campaign. A contact is in it at most once (RF-004).

        In an A/B campaign each contact falls in A or B by its id: stable, and about half each.
        """
        if not contact_ids:
            return 0
        result = self._session.execute(
            insert(CampaignRecipient)
            .values(
                [
                    {
                        "campaign_id": campaign_id,
                        "contact_id": c,
                        "tenant_id": self._tenant_id,
                        "variant": "B" if ab and c.int % 2 else "A",
                    }
                    for c in contact_ids
                ]
            )
            .on_conflict_do_nothing()
        )
        self._session.flush()
        return int(getattr(result, "rowcount", 0) or 0)

    def pending(self, campaign_id: uuid.UUID, limit: int) -> Sequence[CampaignRecipient]:
        """The next batch, locked so a second worker takes other rows."""
        statement = (
            self._scoped()
            .where(
                CampaignRecipient.campaign_id == campaign_id,
                CampaignRecipient.status == "pending",
            )
            .order_by(CampaignRecipient.created_at, CampaignRecipient.contact_id)
            .limit(limit)
            .with_for_update(skip_locked=True)
        )
        return self._session.execute(statement).scalars().all()

    def for_campaign(self, campaign_id: uuid.UUID) -> Sequence[CampaignRecipient]:
        statement = self._scoped().where(CampaignRecipient.campaign_id == campaign_id)
        return self._session.execute(statement).scalars().all()

    def remaining(self, campaign_id: uuid.UUID) -> int:
        return self.count(
            CampaignRecipient.campaign_id == campaign_id, CampaignRecipient.status == "pending"
        )

    def sent_since(self, since: datetime) -> int:
        """Templates of this business's campaigns sent since a moment (the daily cap)."""
        return self.count(CampaignRecipient.sent_at >= since)

    def by_message(self, message_id: uuid.UUID) -> CampaignRecipient | None:
        statement = self._scoped().where(CampaignRecipient.message_id == message_id)
        return self._session.execute(statement).scalar_one_or_none()

    def awaiting_reply(self, contact_id: uuid.UUID, since: datetime) -> CampaignRecipient | None:
        """The latest template sent to this contact since a moment, not yet answered."""
        statement = (
            self._scoped()
            .where(
                CampaignRecipient.contact_id == contact_id,
                CampaignRecipient.sent_at >= since,
                CampaignRecipient.replied_at.is_(None),
            )
            .order_by(CampaignRecipient.sent_at.desc())
            .limit(1)
        )
        return self._session.execute(statement).scalar_one_or_none()

    def skip_all_pending(self, campaign_id: uuid.UUID, reason: str) -> int:
        result = self._session.execute(
            update(CampaignRecipient)
            .where(
                self._tenant_predicate,
                CampaignRecipient.campaign_id == campaign_id,
                CampaignRecipient.status == "pending",
            )
            .values(status="skipped", skip_reason=reason)
        )
        self._session.flush()
        return int(getattr(result, "rowcount", 0) or 0)
