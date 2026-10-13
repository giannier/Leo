"""End-to-end tests for TASK-006: registration and email verification.

Every test drives the real HTTP endpoints against the real database inside a
rolled-back transaction, so what is asserted is the behaviour a client gets,
including the rows that survive - or do not survive - a failed registration.
"""

from __future__ import annotations

import re
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import parse_qs, urlparse

import pytest
from fastapi import FastAPI
from httpx import AsyncClient
from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from app.api.deps import get_email_provider
from app.core.config import get_settings
from app.models import EmailToken, Plan, Tenant, TenantUser, User
from app.providers.email.base import EmailError, EmailMessage
from app.providers.email.mock import MockEmailProvider
from app.services.trial import archive_tenant, pause_tenant

AUTH = f"{get_settings().api_v1_prefix}/auth"

PASSWORD = "una-clave-suficientemente-larga"

_HREF = re.compile(r'href="([^"]+)"')


def _unique_phone() -> str:
    """A fresh Peruvian mobile in E.164, unique per call."""
    return f"+519{uuid.uuid4().int % 10**8:08d}"


def _payload(**overrides: Any) -> dict[str, Any]:
    """A valid registration body; override only what a test is about."""
    body: dict[str, Any] = {
        "full_name": "Ana Quispe",
        "email": f"ana-{uuid.uuid4().hex[:12]}@example.com",
        "password": PASSWORD,
        "phone_e164": _unique_phone(),
        "country": "PE",
        "business_name": "Panaderia Quispe",
    }
    body.update(overrides)
    return body


def _token_from(message: EmailMessage) -> str:
    """Pull the verification token out of the link the customer receives."""
    for href in _HREF.findall(message.html):
        query = parse_qs(urlparse(href).query)
        if "token" in query:
            return str(query["token"][0])
    raise AssertionError(f"no verification link in the email body:\n{message.html}")


def _user_by_email(db: Session, email: str) -> User | None:
    return db.execute(select(User).where(User.email == email)).scalar_one_or_none()


def _count(db: Session, model: Any) -> int:
    return db.execute(select(func.count()).select_from(model)).scalar_one()


def _leave_trial(db: Session, tenant_id: uuid.UUID, status: str) -> None:
    """Move a tenant out of trial the way the domain itself would.

    Every exit is driven through the real transition where one exists, so
    these tests keep testing the product rather than a status column a test
    helper happens to write.
    """
    tenant = db.get(Tenant, tenant_id)
    assert tenant is not None
    now = datetime.now(UTC)

    if status == "active":
        # Conversion to a paid plan is Feature 009 and has no service yet.
        # The state is real - `tenants.status` allows it and `agent_may_reply`
        # already treats it as live - so it is set directly, and this is the
        # only exit in the table that has no transition to call.
        tenant.status = "active"
        db.flush()
        return

    pause_tenant(db, tenant, now=now)
    if status == "archived":
        archive_tenant(tenant, now=now)
    db.flush()


class TestRegister:
    async def test_creates_user_tenant_and_membership(self, api: AsyncClient, db: Session) -> None:
        body = _payload()
        response = await api.post(f"{AUTH}/register", json=body)

        assert response.status_code == 201
        data = response.json()
        assert data["verification_email_sent"] is True

        user = db.get(User, uuid.UUID(data["user_id"]))
        assert user is not None
        assert user.email == body["email"]
        assert user.full_name == "Ana Quispe"
        assert user.phone_e164 == body["phone_e164"]
        # Unverified until the link is used - that is the whole point of
        # Clarification 005.
        assert user.email_verified_at is None
        assert user.phone_verified_at is None
        assert user.is_platform_admin is False

        tenant = db.get(Tenant, uuid.UUID(data["tenant_id"]))
        assert tenant is not None
        assert tenant.name == "Panaderia Quispe"
        assert tenant.status == "trial"
        assert tenant.plan_id == "trial"
        assert tenant.ai_messages_used == 0

        membership = db.execute(
            select(TenantUser).where(TenantUser.tenant_id == tenant.id)
        ).scalar_one()
        assert membership.user_id == user.id
        assert membership.role == "owner"
        assert membership.status == "active"

    async def test_the_country_is_stored_on_the_tenant(self, api: AsyncClient, db: Session) -> None:
        """TASK-450: it decides the prefix of a phone a customer types without one."""
        response = await api.post(f"{AUTH}/register", json=_payload(country="CO"))
        default = await api.post(f"{AUTH}/register", json=_payload(country="PE"))

        assert response.status_code == 201
        colombian = db.get(Tenant, uuid.UUID(response.json()["tenant_id"]))
        peruvian = db.get(Tenant, uuid.UUID(default.json()["tenant_id"]))
        assert colombian is not None and colombian.pais == "CO"
        assert peruvian is not None and peruvian.pais == "PE"

    async def test_trial_length_is_read_from_the_plans_row(
        self, api: AsyncClient, db: Session
    ) -> None:
        """Not a literal 14 in the code: change the row, the trial changes."""
        db.execute(update(Plan).where(Plan.id == "trial").values(trial_days=21))

        before = datetime.now(UTC)
        response = await api.post(f"{AUTH}/register", json=_payload())
        assert response.status_code == 201

        tenant = db.get(Tenant, uuid.UUID(response.json()["tenant_id"]))
        assert tenant is not None and tenant.trial_ends_at is not None
        assert tenant.trial_ends_at - before >= timedelta(days=21) - timedelta(seconds=5)
        assert tenant.trial_ends_at - before <= timedelta(days=21) + timedelta(seconds=5)

    async def test_seeded_trial_is_the_documented_fourteen_days(
        self, api: AsyncClient, db: Session
    ) -> None:
        """Clarification 001, as actually seeded."""
        before = datetime.now(UTC)
        response = await api.post(f"{AUTH}/register", json=_payload())

        tenant = db.get(Tenant, uuid.UUID(response.json()["tenant_id"]))
        assert tenant is not None and tenant.trial_ends_at is not None
        assert abs((tenant.trial_ends_at - before) - timedelta(days=14)) < timedelta(seconds=5)

    async def test_password_is_never_stored_in_the_clear(
        self, api: AsyncClient, db: Session
    ) -> None:
        body = _payload()
        await api.post(f"{AUTH}/register", json=body)

        user = _user_by_email(db, body["email"])
        assert user is not None
        assert user.password_hash != PASSWORD
        assert user.password_hash.startswith("$argon2")

    async def test_only_the_token_digest_reaches_the_database(
        self, api: AsyncClient, db: Session, mail: MockEmailProvider
    ) -> None:
        body = _payload()
        response = await api.post(f"{AUTH}/register", json=body)
        token = _token_from(mail.last)

        record = db.execute(
            select(EmailToken).where(EmailToken.user_id == uuid.UUID(response.json()["user_id"]))
        ).scalar_one()
        assert record.purpose == "verify_email"
        assert record.consumed_at is None
        assert record.token_hash != token
        assert token not in record.token_hash

    async def test_verification_email_goes_to_the_new_address(
        self, api: AsyncClient, mail: MockEmailProvider
    ) -> None:
        body = _payload()
        await api.post(f"{AUTH}/register", json=body)

        assert len(mail.sent) == 1
        assert mail.last.to == (body["email"],)
        assert "Leo" in mail.last.subject
        assert get_settings().frontend_base_url.rstrip("/") in mail.last.html

    async def test_email_expiry_matches_the_configured_window(
        self, api: AsyncClient, db: Session
    ) -> None:
        before = datetime.now(UTC)
        response = await api.post(f"{AUTH}/register", json=_payload())

        record = db.execute(
            select(EmailToken).where(EmailToken.user_id == uuid.UUID(response.json()["user_id"]))
        ).scalar_one()
        expected = timedelta(hours=get_settings().email_verification_token_ttl_hours)
        assert abs((record.expires_at - before) - expected) < timedelta(seconds=5)

    async def test_registration_without_a_phone_is_accepted(
        self, api: AsyncClient, db: Session
    ) -> None:
        """The phone is only required before a number connection request."""
        body = _payload()
        del body["phone_e164"]

        response = await api.post(f"{AUTH}/register", json=body)

        assert response.status_code == 201
        user = _user_by_email(db, body["email"])
        assert user is not None and user.phone_e164 is None

    async def test_two_accounts_without_a_phone_do_not_collide(self, api: AsyncClient) -> None:
        """A missing number is not a number every account shares."""
        first, second = _payload(), _payload()
        del first["phone_e164"]
        del second["phone_e164"]

        assert (await api.post(f"{AUTH}/register", json=first)).status_code == 201
        assert (await api.post(f"{AUTH}/register", json=second)).status_code == 201


class TestDuplicates:
    async def test_duplicate_email_is_rejected(self, api: AsyncClient, db: Session) -> None:
        first = _payload()
        assert (await api.post(f"{AUTH}/register", json=first)).status_code == 201

        tenants_before = _count(db, Tenant)
        second = _payload(email=first["email"])
        response = await api.post(f"{AUTH}/register", json=second)

        assert response.status_code == 409
        assert response.json()["error"] == "email_taken"
        assert "message" in response.json()
        # The whole second registration was rolled back, not half-written.
        assert _count(db, Tenant) == tenants_before
        # Its phone - unique to this run - reached nobody either.
        orphans = db.execute(
            select(func.count()).select_from(User).where(User.phone_e164 == second["phone_e164"])
        ).scalar_one()
        assert orphans == 0

    async def test_duplicate_email_detection_is_case_insensitive(self, api: AsyncClient) -> None:
        """`users.email` is CITEXT: Ana@ and ana@ are one account."""
        first = _payload(email="Ana.Quispe@Example.com")
        assert (await api.post(f"{AUTH}/register", json=first)).status_code == 201

        response = await api.post(f"{AUTH}/register", json=_payload(email="ana.quispe@example.com"))

        assert response.status_code == 409
        assert response.json()["error"] == "email_taken"

    async def test_duplicate_phone_is_rejected_as_a_second_live_trial(
        self, api: AsyncClient, db: Session
    ) -> None:
        """Clarification 005: one live trial per contact phone."""
        # A name unique to this run. The assertion below counts tenants by
        # business name, and two real bakeries may well share one - so with a
        # fixed name the test fails for anyone whose development database
        # happens to hold a "Panaderia Quispe" from a manual experiment,
        # which says nothing about the code.
        business = f"Panaderia {uuid.uuid4().hex[:10]}"
        first = _payload(business_name=business)
        assert (await api.post(f"{AUTH}/register", json=first)).status_code == 201

        second = _payload(phone_e164=first["phone_e164"], business_name=business)
        response = await api.post(f"{AUTH}/register", json=second)

        assert response.status_code == 409
        assert response.json()["error"] == "phone_in_active_trial"

        # Nothing from the rejected attempt survives: no orphan user, no
        # orphan tenant holding a seat.
        assert _user_by_email(db, second["email"]) is None
        surviving_tenants = db.execute(
            select(func.count()).select_from(Tenant).where(Tenant.name == second["business_name"])
        ).scalar_one()
        assert surviving_tenants == 1

    async def test_a_refused_registration_leaves_the_first_tenant_untouched(
        self, api: AsyncClient, db: Session
    ) -> None:
        first = _payload()
        created = await api.post(f"{AUTH}/register", json=first)
        first_tenant_id = uuid.UUID(created.json()["tenant_id"])

        await api.post(f"{AUTH}/register", json=_payload(phone_e164=first["phone_e164"]))

        tenant = db.get(Tenant, first_tenant_id)
        assert tenant is not None
        assert tenant.status == "trial"
        owner = _user_by_email(db, first["email"])
        assert owner is not None and owner.phone_e164 == first["phone_e164"]

    async def test_the_rejection_never_names_the_business_that_has_the_number(
        self, api: AsyncClient
    ) -> None:
        """Otherwise registration becomes a phone-to-customer lookup service.

        Anyone could type a number they suspect belongs to a competitor and
        read back who they bank with. The answer says the number is taken and
        stops there.
        """
        holder = _payload(business_name=f"Bodega {uuid.uuid4().hex[:10]}")
        created = await api.post(f"{AUTH}/register", json=holder)
        assert created.status_code == 201

        response = await api.post(
            f"{AUTH}/register", json=_payload(phone_e164=holder["phone_e164"])
        )

        assert response.status_code == 409
        assert response.json()["error"] == "phone_in_active_trial"
        disclosed = response.text.lower()
        for secret in (
            holder["business_name"],
            holder["email"],
            holder["full_name"],
            created.json()["tenant_id"],
            created.json()["user_id"],
        ):
            assert secret.lower() not in disclosed


class TestWhenTheNumberComesBack:
    """Clarification 005: "en mas de un tenant **en trial** simultaneamente".

    Taken to the letter that frees the number the moment a tenant stops being
    `trial` - but a trial that expires *becomes* `paused`, so the letter hands
    the number back at the exact instant the trial ends, and the same person
    opens the next one a second later. The number is therefore held through
    `paused` and released at `archived`, thirty days on.

    There is one test per exit state because a rule that is right for two of
    three states either locks somebody out of their own number or gives away
    unlimited trials, and neither shows up in a passing happy path.
    """

    @pytest.mark.parametrize("exit_status", ["active", "archived"])
    async def test_a_tenant_done_with_its_number_releases_it(
        self, api: AsyncClient, db: Session, exit_status: str
    ) -> None:
        """`archived` is a trial nobody came back to. `active` is a customer
        who started paying, and holding their number against them would stop a
        real business from opening a second shop - the rule is against farming
        trials, not against the people who converted."""
        first = _payload()
        created = await api.post(f"{AUTH}/register", json=first)
        assert created.status_code == 201
        _leave_trial(db, uuid.UUID(created.json()["tenant_id"]), exit_status)

        second = await api.post(f"{AUTH}/register", json=_payload(phone_e164=first["phone_e164"]))

        assert second.status_code == 201, second.text
        reused = db.get(Tenant, uuid.UUID(second.json()["tenant_id"]))
        assert reused is not None and reused.status == "trial"

    async def test_an_expired_trial_still_holds_its_number(
        self, api: AsyncClient, db: Session
    ) -> None:
        """The rule's whole purpose, and the easy way to lose it.

        `paused` is where a trial lands when it simply runs out. Releasing the
        number here is unlimited free trials on one phone, one pause at a
        time."""
        first = _payload()
        created = await api.post(f"{AUTH}/register", json=first)
        _leave_trial(db, uuid.UUID(created.json()["tenant_id"]), "paused")

        second = await api.post(f"{AUTH}/register", json=_payload(phone_e164=first["phone_e164"]))

        assert second.status_code == 409
        assert second.json()["error"] == "phone_in_active_trial"

    async def test_the_exit_states_covered_here_are_all_of_them(self) -> None:
        """A new tenant status must not quietly skip the tests above."""
        from app.models.tenant import TENANT_STATUSES

        assert set(TENANT_STATUSES) - {"trial"} == {"active", "paused", "archived"}

    async def test_a_tenant_still_in_trial_keeps_blocking(self, api: AsyncClient) -> None:
        """The other half of the rule: it is scoping, not switching off."""
        first = _payload()
        assert (await api.post(f"{AUTH}/register", json=first)).status_code == 201

        second = await api.post(f"{AUTH}/register", json=_payload(phone_e164=first["phone_e164"]))

        assert second.status_code == 409
        assert second.json()["error"] == "phone_in_active_trial"

    async def test_a_number_freed_by_an_archive_is_blocked_again_by_its_new_trial(
        self, api: AsyncClient, db: Session
    ) -> None:
        """The number moves; it does not become free forever."""
        first = _payload()
        created = await api.post(f"{AUTH}/register", json=first)
        _leave_trial(db, uuid.UUID(created.json()["tenant_id"]), "archived")
        assert (
            await api.post(f"{AUTH}/register", json=_payload(phone_e164=first["phone_e164"]))
        ).status_code == 201

        third = await api.post(f"{AUTH}/register", json=_payload(phone_e164=first["phone_e164"]))

        assert third.status_code == 409
        assert third.json()["error"] == "phone_in_active_trial"


class TestValidation:
    @pytest.mark.parametrize(
        ("field", "value"),
        [
            ("email", "not-an-email"),
            ("email", "missing@domain"),
            ("password", "short"),
            ("phone_e164", "999888777"),
            ("phone_e164", "+0999888777"),
            ("country", "peru"),
            ("full_name", ""),
            ("business_name", ""),
        ],
    )
    async def test_invalid_input_is_400_validation_error(
        self, api: AsyncClient, field: str, value: str
    ) -> None:
        """The contract says 400 with `fields`, not FastAPI's default 422."""
        response = await api.post(f"{AUTH}/register", json=_payload(**{field: value}))

        assert response.status_code == 400
        body = response.json()
        assert body["error"] == "validation_error"
        assert field in body["fields"]

    async def test_missing_body_field_is_reported_by_name(self, api: AsyncClient) -> None:
        body = _payload()
        del body["business_name"]
        response = await api.post(f"{AUTH}/register", json=body)

        assert response.status_code == 400
        assert "business_name" in response.json()["fields"]

    async def test_password_at_the_minimum_length_is_accepted(self, api: AsyncClient) -> None:
        response = await api.post(f"{AUTH}/register", json=_payload(password="0123456789"))
        assert response.status_code == 201


class TestPlanConfiguration:
    async def test_a_trial_plan_without_a_length_is_503_not_a_bad_request(
        self, api: AsyncClient, db: Session
    ) -> None:
        """A broken seed is our fault, so the customer is not told to fix it."""
        db.execute(update(Plan).where(Plan.id == "trial").values(trial_days=None))

        response = await api.post(f"{AUTH}/register", json=_payload())

        assert response.status_code == 503
        assert response.json()["error"] == "registration_unavailable"

    async def test_nothing_is_written_when_the_plan_is_unusable(
        self, api: AsyncClient, db: Session
    ) -> None:
        db.execute(update(Plan).where(Plan.id == "trial").values(trial_days=None))
        body = _payload()

        await api.post(f"{AUTH}/register", json=body)

        assert _user_by_email(db, body["email"]) is None


class TestEmailDelivery:
    async def test_account_survives_a_provider_failure(
        self, app: FastAPI, api: AsyncClient, db: Session
    ) -> None:
        """A dead mail provider must not cost the customer their account."""
        broken = MockEmailProvider(fail_with=EmailError("provider is down"))
        app.dependency_overrides[get_email_provider] = lambda: broken

        body = _payload()
        response = await api.post(f"{AUTH}/register", json=body)

        assert response.status_code == 201
        assert response.json()["verification_email_sent"] is False
        assert _user_by_email(db, body["email"]) is not None

    async def test_unconfigured_provider_reports_that_nothing_was_sent(
        self, app: FastAPI, api: AsyncClient, db: Session
    ) -> None:
        app.dependency_overrides[get_email_provider] = lambda: None

        body = _payload()
        response = await api.post(f"{AUTH}/register", json=body)

        assert response.status_code == 201
        assert response.json()["verification_email_sent"] is False
        # The token is still issued, so `resend-verification` can recover
        # once the provider is configured.
        user = _user_by_email(db, body["email"])
        assert user is not None
        issued = db.execute(
            select(func.count()).select_from(EmailToken).where(EmailToken.user_id == user.id)
        ).scalar_one()
        assert issued == 1


class TestVerifyEmail:
    async def test_the_emailed_link_verifies_the_address(
        self, api: AsyncClient, db: Session, mail: MockEmailProvider
    ) -> None:
        body = _payload()
        created = await api.post(f"{AUTH}/register", json=body)
        token = _token_from(mail.last)

        response = await api.post(f"{AUTH}/verify-email", json={"token": token})

        assert response.status_code == 204
        assert response.content == b""

        user = db.get(User, uuid.UUID(created.json()["user_id"]))
        assert user is not None and user.email_verified_at is not None

        record = db.execute(select(EmailToken).where(EmailToken.user_id == user.id)).scalar_one()
        assert record.consumed_at is not None

    async def test_the_token_is_single_use(self, api: AsyncClient, mail: MockEmailProvider) -> None:
        await api.post(f"{AUTH}/register", json=_payload())
        token = _token_from(mail.last)

        assert (await api.post(f"{AUTH}/verify-email", json={"token": token})).status_code == 204
        replay = await api.post(f"{AUTH}/verify-email", json={"token": token})

        assert replay.status_code == 400
        assert replay.json()["error"] == "invalid_or_expired_token"

    async def test_an_unknown_token_is_rejected(self, api: AsyncClient) -> None:
        response = await api.post(f"{AUTH}/verify-email", json={"token": "not-a-real-token"})

        assert response.status_code == 400
        assert response.json()["error"] == "invalid_or_expired_token"

    async def test_an_expired_token_is_rejected(
        self, api: AsyncClient, db: Session, mail: MockEmailProvider
    ) -> None:
        created = await api.post(f"{AUTH}/register", json=_payload())
        token = _token_from(mail.last)

        db.execute(
            update(EmailToken)
            .where(EmailToken.user_id == uuid.UUID(created.json()["user_id"]))
            .values(expires_at=datetime.now(UTC) - timedelta(minutes=1))
        )

        response = await api.post(f"{AUTH}/verify-email", json={"token": token})

        assert response.status_code == 400
        user = db.get(User, uuid.UUID(created.json()["user_id"]))
        assert user is not None and user.email_verified_at is None


class TestResendVerification:
    async def test_resend_issues_a_new_working_link(
        self, api: AsyncClient, mail: MockEmailProvider
    ) -> None:
        body = _payload()
        await api.post(f"{AUTH}/register", json=body)
        first_token = _token_from(mail.last)

        response = await api.post(f"{AUTH}/resend-verification", json={"email": body["email"]})

        assert response.status_code == 204
        assert len(mail.sent) == 2
        second_token = _token_from(mail.last)
        assert second_token != first_token
        assert (
            await api.post(f"{AUTH}/verify-email", json={"token": second_token})
        ).status_code == 204

    async def test_resend_retires_the_previous_link(
        self, api: AsyncClient, mail: MockEmailProvider
    ) -> None:
        """The old link must die: a resend often means it went astray."""
        body = _payload()
        await api.post(f"{AUTH}/register", json=body)
        first_token = _token_from(mail.last)

        await api.post(f"{AUTH}/resend-verification", json={"email": body["email"]})

        response = await api.post(f"{AUTH}/verify-email", json={"token": first_token})
        assert response.status_code == 400
        assert response.json()["error"] == "invalid_or_expired_token"

    async def test_unknown_address_answers_204_without_sending(
        self, api: AsyncClient, mail: MockEmailProvider
    ) -> None:
        """No account-enumeration oracle: the answer never varies."""
        response = await api.post(
            f"{AUTH}/resend-verification", json={"email": "nobody@example.com"}
        )

        assert response.status_code == 204
        assert mail.sent == []

    async def test_already_verified_address_answers_204_without_sending(
        self, api: AsyncClient, mail: MockEmailProvider
    ) -> None:
        body = _payload()
        await api.post(f"{AUTH}/register", json=body)
        await api.post(f"{AUTH}/verify-email", json={"token": _token_from(mail.last)})

        response = await api.post(f"{AUTH}/resend-verification", json={"email": body["email"]})

        assert response.status_code == 204
        assert len(mail.sent) == 1

    async def test_resend_is_case_insensitive_about_the_address(
        self, api: AsyncClient, mail: MockEmailProvider
    ) -> None:
        await api.post(f"{AUTH}/register", json=_payload(email="Ana.Quispe@Example.com"))

        response = await api.post(
            f"{AUTH}/resend-verification", json={"email": "ANA.QUISPE@example.com"}
        )

        assert response.status_code == 204
        assert len(mail.sent) == 2

    async def test_invalid_address_is_400_validation_error(self, api: AsyncClient) -> None:
        response = await api.post(f"{AUTH}/resend-verification", json={"email": "nope"})

        assert response.status_code == 400
        assert response.json()["error"] == "validation_error"
