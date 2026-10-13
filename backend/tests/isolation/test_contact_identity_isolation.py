"""Contacts identified by BSUID or phone, their customer data, and the richer
agent traces (Feature 004, TASK-408).

A contact's identity is the key a leak travels on: the same BSUID or phone can
write to two businesses and must be two contacts, each invisible to the other.
"""

from __future__ import annotations

from typing import Any

import pytest
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models.conversation import AgentTrace
from app.repositories.base import TenantScopeError
from app.repositories.channel import ContactRepository
from app.repositories.conversation import AgentTraceRepository
from tests.isolation.conftest import SHARED_CUSTOMER_PHONE, TenantFixture

BSUID = "ARG.13491208609772"


class TestUpsertByWhicheverIdentifierArrives:
    def test_a_contact_with_only_a_bsuid_is_created(
        self, db: Session, tenant_a: TenantFixture
    ) -> None:
        contact = ContactRepository(db, tenant_a.tenant_id).upsert(
            external_user_id=BSUID, profile_name="Ana"
        )

        assert contact.phone_e164 is None
        assert contact.external_user_id == BSUID
        assert contact.profile_name == "Ana"

    def test_the_same_bsuid_is_the_same_contact(self, db: Session, tenant_a: TenantFixture) -> None:
        contacts = ContactRepository(db, tenant_a.tenant_id)
        first = contacts.upsert(external_user_id=BSUID)

        again = contacts.upsert(external_user_id=BSUID, profile_name="Ana")

        assert again.id == first.id
        assert again.profile_name == "Ana"
        assert contacts.count(ContactRepository.model.external_user_id == BSUID) == 1

    def test_a_phone_only_contact_is_found_as_before(
        self, db: Session, tenant_a: TenantFixture
    ) -> None:
        found = ContactRepository(db, tenant_a.tenant_id).upsert(SHARED_CUSTOMER_PHONE)

        assert found.id == tenant_a.contact_id

    def test_a_new_phone_creates_a_phone_contact(
        self, db: Session, tenant_a: TenantFixture
    ) -> None:
        created = ContactRepository(db, tenant_a.tenant_id).upsert("+51911111111")

        assert created.phone_e164 == "+51911111111"
        assert created.external_user_id is None

    def test_a_phone_contact_learns_its_bsuid(self, db: Session, tenant_a: TenantFixture) -> None:
        contacts = ContactRepository(db, tenant_a.tenant_id)

        learned = contacts.upsert(SHARED_CUSTOMER_PHONE, external_user_id=BSUID)

        assert learned.id == tenant_a.contact_id
        assert learned.external_user_id == BSUID
        # The next message carries the BSUID alone and finds the same person.
        assert contacts.upsert(external_user_id=BSUID).id == tenant_a.contact_id

    def test_a_bsuid_contact_learns_its_phone(self, db: Session, tenant_a: TenantFixture) -> None:
        contacts = ContactRepository(db, tenant_a.tenant_id)
        original = contacts.upsert(external_user_id=BSUID)

        learned = contacts.upsert("+51922222222", external_user_id=BSUID)

        assert learned.id == original.id
        assert learned.phone_e164 == "+51922222222"

    def test_a_stored_identifier_is_never_overwritten(
        self, db: Session, tenant_a: TenantFixture
    ) -> None:
        contacts = ContactRepository(db, tenant_a.tenant_id)
        original = contacts.upsert("+51933333333", external_user_id=BSUID)

        again = contacts.upsert("+51944444444", external_user_id=BSUID)

        assert again.id == original.id
        assert again.phone_e164 == "+51933333333"
        # And the phone that was not stored did not become a contact of its own.
        assert contacts.by_phone("+51944444444") is None

    def test_a_phone_conflict_with_another_contact_keeps_the_bsuid_one(
        self, db: Session, tenant_a: TenantFixture
    ) -> None:
        """Two rows of one person: this method does not merge them, and it must
        not break the phone's unique index by attaching the phone twice."""
        contacts = ContactRepository(db, tenant_a.tenant_id)
        by_bsuid = contacts.upsert(external_user_id=BSUID)

        resolved = contacts.upsert(SHARED_CUSTOMER_PHONE, external_user_id=BSUID)

        assert resolved.id == by_bsuid.id
        assert resolved.phone_e164 is None
        phone_contact = contacts.by_phone(SHARED_CUSTOMER_PHONE)
        assert phone_contact is not None and phone_contact.id == tenant_a.contact_id
        # Not merged, but marked for the operator (Milestone B, decision 12).
        assert resolved.posible_duplicado_de == phone_contact.id
        assert phone_contact.posible_duplicado_de == resolved.id

    def test_a_missing_name_does_not_erase_a_known_one(
        self, db: Session, tenant_a: TenantFixture
    ) -> None:
        contacts = ContactRepository(db, tenant_a.tenant_id)
        contacts.upsert(external_user_id=BSUID, profile_name="Ana")

        assert contacts.upsert(external_user_id=BSUID).profile_name == "Ana"

    def test_an_upsert_with_no_identifier_is_refused(
        self, db: Session, tenant_a: TenantFixture
    ) -> None:
        with pytest.raises(ValueError, match="phone number or a BSUID"):
            ContactRepository(db, tenant_a.tenant_id).upsert()


class TestIdentityIsPerTenant:
    def test_the_same_bsuid_in_two_businesses_is_two_contacts(
        self, db: Session, tenant_a: TenantFixture, tenant_b: TenantFixture
    ) -> None:
        mine = ContactRepository(db, tenant_a.tenant_id).upsert(external_user_id=BSUID)
        theirs = ContactRepository(db, tenant_b.tenant_id).upsert(external_user_id=BSUID)

        assert mine.id != theirs.id
        assert (mine.tenant_id, theirs.tenant_id) == (tenant_a.tenant_id, tenant_b.tenant_id)

    def test_a_foreign_bsuid_finds_nothing(
        self, db: Session, tenant_a: TenantFixture, tenant_b: TenantFixture
    ) -> None:
        ContactRepository(db, tenant_b.tenant_id).upsert(external_user_id=BSUID)

        assert ContactRepository(db, tenant_a.tenant_id).by_external_user_id(BSUID) is None

    def test_a_duplicate_bsuid_in_one_business_fails(
        self, db: Session, tenant_a: TenantFixture
    ) -> None:
        contacts = ContactRepository(db, tenant_a.tenant_id)
        contacts.add(external_user_id=BSUID)

        with pytest.raises(IntegrityError), db.begin_nested():
            contacts.add(external_user_id=BSUID)

    def test_many_contacts_may_lack_a_phone(self, db: Session, tenant_a: TenantFixture) -> None:
        contacts = ContactRepository(db, tenant_a.tenant_id)

        contacts.add(external_user_id="one")
        contacts.add(external_user_id="two")

        assert contacts.count(ContactRepository.model.phone_e164.is_(None)) == 2

    def test_a_contact_must_have_an_identity(self, db: Session, tenant_a: TenantFixture) -> None:
        with pytest.raises(IntegrityError), db.begin_nested():
            ContactRepository(db, tenant_a.tenant_id).add(profile_name="Nobody")

    def test_a_repository_cannot_write_for_another_tenant(
        self, db: Session, tenant_a: TenantFixture, tenant_b: TenantFixture
    ) -> None:
        with pytest.raises(TenantScopeError):
            ContactRepository(db, tenant_a.tenant_id).add(
                tenant_id=tenant_b.tenant_id, external_user_id=BSUID
            )


class TestCustomerData:
    def test_a_new_contact_has_no_data(self, db: Session, tenant_a: TenantFixture) -> None:
        contact = ContactRepository(db, tenant_a.tenant_id).get(tenant_a.contact_id)
        assert contact is not None
        db.refresh(contact)

        assert contact.custom_data == {}

    def test_merging_adds_and_keeps(self, db: Session, tenant_a: TenantFixture) -> None:
        contacts = ContactRepository(db, tenant_a.tenant_id)
        contact = contacts.upsert(external_user_id=BSUID)

        contacts.merge_custom_data(contact, {"dni": "12345678"})
        contacts.merge_custom_data(contact, {"correo": "ana@example.com"})
        contacts.merge_custom_data(contact, {"dni": "87654321"})
        db.refresh(contact)

        assert contact.custom_data == {"dni": "87654321", "correo": "ana@example.com"}

    def test_the_same_person_in_two_businesses_keeps_separate_data(
        self, db: Session, tenant_a: TenantFixture, tenant_b: TenantFixture
    ) -> None:
        mine = ContactRepository(db, tenant_a.tenant_id)
        theirs = ContactRepository(db, tenant_b.tenant_id)
        mine.merge_custom_data(mine.upsert(SHARED_CUSTOMER_PHONE), {"dni": "11111111"})

        other = theirs.upsert(SHARED_CUSTOMER_PHONE)

        assert other.custom_data == {}

    def test_a_foreign_contact_cannot_be_changed(
        self, db: Session, tenant_a: TenantFixture, tenant_b: TenantFixture
    ) -> None:
        foreign = ContactRepository(db, tenant_b.tenant_id).upsert(SHARED_CUSTOMER_PHONE)

        with pytest.raises(TenantScopeError):
            ContactRepository(db, tenant_a.tenant_id).merge_custom_data(foreign, {"dni": "1"})


def _trace(tenant: TenantFixture, **overrides: Any) -> dict[str, Any]:
    values: dict[str, Any] = {"message_id": tenant.message_id, "model": "offline"}
    values.update(overrides)
    return values


class TestTraceColumns:
    def test_the_engine_columns_are_optional(self, db: Session, tenant_a: TenantFixture) -> None:
        trace = AgentTraceRepository(db, tenant_a.tenant_id).add(**_trace(tenant_a))
        db.refresh(trace)

        assert trace.stage_key is None
        assert trace.funnel_code is None
        assert trace.tools_enabled is None
        assert trace.outbound_count is None
        assert trace.model_tier is None

    def test_they_are_stored(self, db: Session, tenant_a: TenantFixture) -> None:
        trace = AgentTraceRepository(db, tenant_a.tenant_id).add(
            **_trace(
                tenant_a,
                stage_key="IDENTIFICAR",
                funnel_code="ATENDIDO",
                tools_enabled=["buscar_productos", "ver_producto"],
                outbound_count=2,
                model_tier="economico",
            )
        )
        db.refresh(trace)

        assert isinstance(trace, AgentTrace)
        assert (trace.stage_key, trace.funnel_code) == ("IDENTIFICAR", "ATENDIDO")
        assert trace.tools_enabled == ["buscar_productos", "ver_producto"]
        assert (trace.outbound_count, trace.model_tier) == (2, "economico")

    def test_a_foreign_trace_is_invisible(
        self, db: Session, tenant_a: TenantFixture, tenant_b: TenantFixture
    ) -> None:
        AgentTraceRepository(db, tenant_b.tenant_id).add(**_trace(tenant_b, stage_key="COBRO"))

        assert AgentTraceRepository(db, tenant_a.tenant_id).for_message(tenant_b.message_id) == []
