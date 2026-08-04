"""One business never reaches another's contacts or deliveries (TASK-201).

The channel tables are the ones where a leak is most tempting and most
damaging, because their natural keys look global and are not.

A phone number is the obvious case: the same person can be a customer of two
businesses on the same street, and each of them must see their own thread with
that person and nothing of the other's. The whole fixture is built on that -
both tenants have a contact with the *same* phone number, so any query that
forgets the tenant filter returns the wrong row rather than nothing at all,
and a test that passed by accident is impossible.

`messages.provider_message_id` is the subtler one. It really is globally
unique - it is the provider's own identifier - which makes an unscoped lookup
look correct right up to the moment somebody holds the id.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy.orm import Session

from app.models.channel import SESSION_WINDOW_HOURS
from app.repositories.base import TenantScopeError
from app.repositories.channel import (
    ContactRepository,
    MessageStatusEventRepository,
    WebhookEventRepository,
    session_window_is_open,
)
from app.repositories.conversation import MessageRepository
from tests.isolation.conftest import SHARED_CUSTOMER_PHONE, TenantFixture

NOW = datetime(2026, 8, 4, 12, 0, tzinfo=UTC)


class TestTheSamePersonWritingToTwoBusinesses:
    def test_the_fixture_really_does_share_the_number(
        self, db: Session, tenant_a: TenantFixture, tenant_b: TenantFixture
    ) -> None:
        """Guards the fixture itself. Two different numbers would make every
        case below pass for the wrong reason."""
        a = ContactRepository(db, tenant_a.tenant_id).get(tenant_a.contact_id)
        b = ContactRepository(db, tenant_b.tenant_id).get(tenant_b.contact_id)
        assert a is not None and b is not None
        assert a.phone_e164 == b.phone_e164 == SHARED_CUSTOMER_PHONE
        assert a.id != b.id

    def test_a_lookup_by_phone_returns_your_own_contact(
        self, db: Session, tenant_a: TenantFixture, tenant_b: TenantFixture
    ) -> None:
        found = ContactRepository(db, tenant_a.tenant_id).by_phone(SHARED_CUSTOMER_PHONE)

        assert found is not None
        assert found.id == tenant_a.contact_id
        assert found.id != tenant_b.contact_id

    def test_a_foreign_contact_id_reads_as_missing(
        self, db: Session, tenant_a: TenantFixture, tenant_b: TenantFixture
    ) -> None:
        assert ContactRepository(db, tenant_a.tenant_id).get(tenant_b.contact_id) is None

    def test_listing_never_includes_the_other_business(
        self, db: Session, tenant_a: TenantFixture, tenant_b: TenantFixture
    ) -> None:
        rows = ContactRepository(db, tenant_a.tenant_id).list()

        assert [row.id for row in rows] == [tenant_a.contact_id]

    def test_an_upsert_finds_your_row_rather_than_creating_a_second(
        self, db: Session, tenant_a: TenantFixture, tenant_b: TenantFixture
    ) -> None:
        """And it must not find B's. An upsert keyed on the phone alone would
        either update the wrong business's contact or hit the unique index."""
        repository = ContactRepository(db, tenant_a.tenant_id)

        upserted = repository.upsert(SHARED_CUSTOMER_PHONE, profile_name="Renamed by A")

        assert upserted.id == tenant_a.contact_id
        assert repository.count() == 1
        untouched = ContactRepository(db, tenant_b.tenant_id).get(tenant_b.contact_id)
        assert untouched is not None
        assert untouched.profile_name == "Customer of B"


class TestMutatingSomebodyElsesContact:
    def test_recording_an_inbound_message_is_refused(
        self, db: Session, tenant_a: TenantFixture, tenant_b: TenantFixture
    ) -> None:
        """The 24-hour window is computed from this timestamp. Moving another
        tenant's would let one business open or close another's sending
        window."""
        foreign = ContactRepository(db, tenant_b.tenant_id).get(tenant_b.contact_id)
        assert foreign is not None

        with pytest.raises(TenantScopeError):
            ContactRepository(db, tenant_a.tenant_id).record_inbound(foreign, at=NOW)

    def test_opting_somebody_else_out_is_refused(
        self, db: Session, tenant_a: TenantFixture, tenant_b: TenantFixture
    ) -> None:
        """Otherwise one business could silence another's customer."""
        foreign = ContactRepository(db, tenant_b.tenant_id).get(tenant_b.contact_id)
        assert foreign is not None

        with pytest.raises(TenantScopeError):
            ContactRepository(db, tenant_a.tenant_id).set_opted_out(foreign, at=NOW)

        db.refresh(foreign)
        assert foreign.opted_out_at is None

    def test_opting_out_your_own_contact_leaves_the_other_alone(
        self, db: Session, tenant_a: TenantFixture, tenant_b: TenantFixture
    ) -> None:
        """The same person telling one business to stop has not told the
        other anything."""
        mine = ContactRepository(db, tenant_a.tenant_id).get(tenant_a.contact_id)
        assert mine is not None
        ContactRepository(db, tenant_a.tenant_id).set_opted_out(mine, at=NOW)

        theirs = ContactRepository(db, tenant_b.tenant_id).get(tenant_b.contact_id)
        assert theirs is not None
        assert theirs.opted_out_at is None


class TestTheSessionWindow:
    def test_it_is_open_while_the_last_message_is_recent(
        self, db: Session, tenant_a: TenantFixture
    ) -> None:
        contact = ContactRepository(db, tenant_a.tenant_id).get(tenant_a.contact_id)
        assert contact is not None
        contact.last_inbound_at = NOW - timedelta(hours=SESSION_WINDOW_HOURS - 1)

        assert session_window_is_open(contact, now=NOW) is True

    def test_it_is_closed_once_the_window_has_passed(
        self, db: Session, tenant_a: TenantFixture
    ) -> None:
        contact = ContactRepository(db, tenant_a.tenant_id).get(tenant_a.contact_id)
        assert contact is not None
        contact.last_inbound_at = NOW - timedelta(hours=SESSION_WINDOW_HOURS, seconds=1)

        assert session_window_is_open(contact, now=NOW) is False

    def test_somebody_who_has_never_written_has_no_window(
        self, db: Session, tenant_a: TenantFixture
    ) -> None:
        """A contact created some other way is not somebody the business may
        message out of the blue."""
        contact = ContactRepository(db, tenant_a.tenant_id).get(tenant_a.contact_id)
        assert contact is not None
        contact.last_inbound_at = None

        assert session_window_is_open(contact, now=NOW) is False

    def test_an_older_message_never_shortens_the_window(
        self, db: Session, tenant_a: TenantFixture
    ) -> None:
        """Providers redeliver, and out-of-order arrival is normal. A
        redelivered message from yesterday must not close a window that
        today's message opened."""
        repository = ContactRepository(db, tenant_a.tenant_id)
        contact = repository.get(tenant_a.contact_id)
        assert contact is not None
        # The fixture stamps wall-clock time. Cleared so the two timestamps
        # under test are the only ones in play.
        contact.last_inbound_at = None
        repository.record_inbound(contact, at=NOW)

        repository.record_inbound(contact, at=NOW - timedelta(hours=20))

        assert contact.last_inbound_at == NOW


class TestDeliveryHistory:
    def test_a_status_cannot_be_written_against_a_foreign_message(
        self, db: Session, tenant_a: TenantFixture, tenant_b: TenantFixture
    ) -> None:
        with pytest.raises(TenantScopeError):
            MessageStatusEventRepository(db, tenant_a.tenant_id).append(
                tenant_b.message_id, status="read", provider_ts=NOW
            )

    def test_the_history_of_a_foreign_message_is_empty(
        self, db: Session, tenant_a: TenantFixture, tenant_b: TenantFixture
    ) -> None:
        MessageStatusEventRepository(db, tenant_b.tenant_id).append(
            tenant_b.message_id, status="delivered", provider_ts=NOW
        )

        seen = MessageStatusEventRepository(db, tenant_a.tenant_id).history(tenant_b.message_id)

        assert list(seen) == []

    def test_the_history_is_ordered_by_the_providers_clock(
        self, db: Session, tenant_a: TenantFixture
    ) -> None:
        """`delivered` and `read` webhooks arrive out of order routinely. A
        history in arrival order would report the customer reading a message
        before it reached them."""
        repository = MessageStatusEventRepository(db, tenant_a.tenant_id)
        repository.append(tenant_a.message_id, status="read", provider_ts=NOW)
        repository.append(
            tenant_a.message_id, status="delivered", provider_ts=NOW - timedelta(minutes=5)
        )

        assert [row.status for row in repository.history(tenant_a.message_id)] == [
            "delivered",
            "read",
        ]


class TestTheProvidersMessageId:
    def test_a_globally_unique_id_is_still_looked_up_per_tenant(
        self, db: Session, tenant_a: TenantFixture, tenant_b: TenantFixture
    ) -> None:
        """The id really is unique platform-wide, which is exactly what makes
        an unscoped lookup look correct until somebody holds one."""
        provider_id = f"wamid.{uuid.uuid4().hex}"
        messages_b = MessageRepository(db, tenant_b.tenant_id)
        message = messages_b.get(tenant_b.message_id)
        assert message is not None
        message.provider_message_id = provider_id
        db.flush()

        assert messages_b.by_provider_message_id(provider_id) is not None
        assert MessageRepository(db, tenant_a.tenant_id).by_provider_message_id(provider_id) is None

    def test_a_delivery_status_cannot_be_set_on_a_foreign_message(
        self, db: Session, tenant_a: TenantFixture, tenant_b: TenantFixture
    ) -> None:
        foreign = MessageRepository(db, tenant_b.tenant_id).get(tenant_b.message_id)
        assert foreign is not None

        with pytest.raises(TenantScopeError):
            MessageRepository(db, tenant_a.tenant_id).set_delivery_status(foreign, status="read")


class TestWebhookEventsAreNotTenantData:
    def test_they_carry_no_tenant_and_that_is_deliberate(self, db: Session) -> None:
        """Which tenant a delivery belongs to is a conclusion drawn from the
        payload, and drawing it is the step that can fail. An event nobody can
        attribute must still be stored, or the parser bug that caused it
        destroys a customer's message."""
        repository = WebhookEventRepository(db)
        event = repository.record(
            provider="ycloud",
            provider_event_id=f"evt-{uuid.uuid4().hex}",
            payload={"addressed": "to nobody we know"},
        )

        assert event is not None
        assert not hasattr(event, "tenant_id")

    def test_a_redelivery_is_recognised_and_not_stored_twice(self, db: Session) -> None:
        repository = WebhookEventRepository(db)
        event_id = f"evt-{uuid.uuid4().hex}"

        first = repository.record(provider="ycloud", provider_event_id=event_id, payload={"n": 1})
        second = repository.record(provider="ycloud", provider_event_id=event_id, payload={"n": 2})

        assert first is not None
        assert second is None
        stored = repository.by_provider_event_id(event_id)
        assert stored is not None
        # The first payload wins. A retry is the same delivery, not a new one.
        assert stored.payload == {"n": 1}
