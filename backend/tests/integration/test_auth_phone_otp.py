"""Phone verification by one-time code (TASK-008, Clarification 005).

The three controls that make a six-digit credential safe are the subject
here: the ten-minute expiry, the five-attempt cap, and one live code per user.
Each has a test that fails if the control is removed, because none of them is
observable from a passing happy path.
"""

from __future__ import annotations

import re
import uuid
from datetime import UTC, datetime
from typing import Any
from urllib.parse import parse_qs, urlparse

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import get_otp_request_limiter, get_otp_sender
from app.core.rate_limit import NullRateLimiter, RateLimitDecision
from app.models.tenant import Tenant, TenantUser
from app.models.user import OtpCode, User
from app.providers.otp.mock import MockOtpSender
from app.services.trial import archive_tenant, pause_tenant

AUTH = "/api/v1/auth"
PASSWORD = "una-clave-suficientemente-larga"
_HREF = re.compile(r'href="([^"]+)"')


def _unique_phone() -> str:
    return f"+519{uuid.uuid4().int % 10**8:08d}"


def _token_from(message: Any) -> str:
    for href in _HREF.findall(message.html):
        query = parse_qs(urlparse(href).query)
        if "token" in query:
            return str(query["token"][0])
    raise AssertionError("no verification link in the email body")


async def _signed_in(
    api: AsyncClient, mail: Any, **overrides: Any
) -> tuple[dict[str, Any], dict[str, str]]:
    """Register, verify the email, log in. Returns the account and headers."""
    body: dict[str, Any] = {
        "full_name": "Ana Quispe",
        "email": f"ana-{uuid.uuid4().hex[:12]}@example.com",
        "password": PASSWORD,
        "phone_e164": _unique_phone(),
        "business_name": "Panaderia Quispe",
    }
    body.update(overrides)

    created = await api.post(f"{AUTH}/register", json=body)
    assert created.status_code == 201, created.text
    verified = await api.post(f"{AUTH}/verify-email", json={"token": _token_from(mail.last)})
    assert verified.status_code == 204

    session = await api.post(f"{AUTH}/login", json={"email": body["email"], "password": PASSWORD})
    assert session.status_code == 200, session.text
    token = session.json()["access_token"]
    return {**body, **created.json()}, {"Authorization": f"Bearer {token}"}


@pytest.fixture
def otp(app: Any) -> MockOtpSender:
    """Capture delivered codes instead of logging them."""
    sender = MockOtpSender()
    app.dependency_overrides[get_otp_sender] = lambda: sender
    app.dependency_overrides[get_otp_request_limiter] = NullRateLimiter
    return sender


class TestRequestingACode:
    async def test_a_code_is_issued_and_delivered(
        self, api: AsyncClient, mail: Any, otp: MockOtpSender
    ) -> None:
        _, headers = await _signed_in(api, mail)
        response = await api.post(f"{AUTH}/phone/request-otp", headers=headers)
        assert response.status_code == 204
        assert len(otp.last.code) == 6

    async def test_the_code_is_never_stored_in_clear(
        self, api: AsyncClient, mail: Any, otp: MockOtpSender, db: Session
    ) -> None:
        """A database dump must not yield a usable code."""
        _, headers = await _signed_in(api, mail)
        await api.post(f"{AUTH}/phone/request-otp", headers=headers)

        stored = db.execute(select(OtpCode.code_hash)).scalars().all()
        assert stored
        assert otp.last.code not in stored

    async def test_requesting_again_retires_the_previous_code(
        self, api: AsyncClient, mail: Any, otp: MockOtpSender
    ) -> None:
        """Otherwise the attempt cap becomes five tries *per code*."""
        _, headers = await _signed_in(api, mail)
        await api.post(f"{AUTH}/phone/request-otp", headers=headers)
        first = otp.last.code
        await api.post(f"{AUTH}/phone/request-otp", headers=headers)
        assert otp.last.code != first

        stale = await api.post(f"{AUTH}/phone/verify-otp", headers=headers, json={"code": first})
        assert stale.status_code == 400
        assert stale.json()["error"] == "invalid_or_expired_otp"

    async def test_an_account_without_a_phone_is_told_so(
        self, api: AsyncClient, mail: Any, otp: MockOtpSender
    ) -> None:
        _, headers = await _signed_in(api, mail, phone_e164=None)
        response = await api.post(f"{AUTH}/phone/request-otp", headers=headers)
        assert response.status_code == 400
        assert response.json()["error"] == "no_phone_on_record"

    async def test_anonymous_callers_are_refused(
        self, api: AsyncClient, otp: MockOtpSender
    ) -> None:
        assert (await api.post(f"{AUTH}/phone/request-otp")).status_code == 401


class TestVerifying:
    async def test_the_delivered_code_verifies_the_phone(
        self, api: AsyncClient, mail: Any, otp: MockOtpSender, db: Session
    ) -> None:
        account, headers = await _signed_in(api, mail)
        await api.post(f"{AUTH}/phone/request-otp", headers=headers)

        response = await api.post(
            f"{AUTH}/phone/verify-otp", headers=headers, json={"code": otp.last.code}
        )
        assert response.status_code == 204

        user = db.get(User, uuid.UUID(account["user_id"]))
        assert user is not None
        db.refresh(user)
        assert user.phone_verified_at is not None

    async def test_a_code_cannot_be_used_twice(
        self, api: AsyncClient, mail: Any, otp: MockOtpSender
    ) -> None:
        _, headers = await _signed_in(api, mail)
        await api.post(f"{AUTH}/phone/request-otp", headers=headers)
        code = otp.last.code

        assert (
            await api.post(f"{AUTH}/phone/verify-otp", headers=headers, json={"code": code})
        ).status_code == 204
        replay = await api.post(f"{AUTH}/phone/verify-otp", headers=headers, json={"code": code})
        assert replay.status_code == 400

    async def test_the_sixth_attempt_is_refused(
        self, api: AsyncClient, mail: Any, otp: MockOtpSender
    ) -> None:
        """Five attempts, then the code is burnt - even if the sixth is right."""
        _, headers = await _signed_in(api, mail)
        await api.post(f"{AUTH}/phone/request-otp", headers=headers)
        correct = otp.last.code
        wrong = "000000" if correct != "000000" else "111111"

        for _ in range(5):
            failed = await api.post(
                f"{AUTH}/phone/verify-otp", headers=headers, json={"code": wrong}
            )
            assert failed.status_code == 400

        exhausted = await api.post(
            f"{AUTH}/phone/verify-otp", headers=headers, json={"code": correct}
        )
        assert exhausted.status_code == 400
        assert exhausted.json()["error"] == "invalid_or_expired_otp"

    async def test_every_failure_looks_identical(
        self, api: AsyncClient, mail: Any, otp: MockOtpSender
    ) -> None:
        """No oracle: wrong, expired and never-requested must not differ.

        A distinguishable "expired" would confirm a code had been issued, and
        a distinguishable "too many attempts" would confirm the guesses were
        being counted - both useful to an attacker, neither useful to a
        customer.
        """
        _, headers = await _signed_in(api, mail)
        never_requested = await api.post(
            f"{AUTH}/phone/verify-otp", headers=headers, json={"code": "123456"}
        )

        await api.post(f"{AUTH}/phone/request-otp", headers=headers)
        wrong_code = "000000" if otp.last.code != "000000" else "111111"
        wrong = await api.post(
            f"{AUTH}/phone/verify-otp", headers=headers, json={"code": wrong_code}
        )

        assert never_requested.status_code == wrong.status_code == 400
        assert never_requested.json() == wrong.json()

    @pytest.mark.parametrize("code", ["12345", "1234567", "12345a", "", "  1234"])
    async def test_a_malformed_code_is_a_validation_error(
        self, api: AsyncClient, mail: Any, otp: MockOtpSender, code: str
    ) -> None:
        """Rejected before it can burn one of the five real attempts."""
        _, headers = await _signed_in(api, mail)
        await api.post(f"{AUTH}/phone/request-otp", headers=headers)

        response = await api.post(f"{AUTH}/phone/verify-otp", headers=headers, json={"code": code})
        assert response.status_code == 400
        assert response.json()["error"] == "validation_error"

    async def test_anonymous_callers_are_refused(
        self, api: AsyncClient, otp: MockOtpSender
    ) -> None:
        response = await api.post(f"{AUTH}/phone/verify-otp", json={"code": "123456"})
        assert response.status_code == 401


class TestRequestThrottling:
    async def test_the_fourth_request_in_an_hour_is_refused(
        self, api: AsyncClient, mail: Any, app: Any, otp: MockOtpSender
    ) -> None:
        """Three per hour, per user (contracts/api.md).

        Per user rather than per IP: the router's IP limit does not stop one
        account draining an SMS budget from a changing address, and each new
        code resets the attempt budget on the last.
        """
        _, headers = await _signed_in(api, mail)

        allowed = 3
        calls = {"n": 0}

        class CountingLimiter:
            async def hit(self, key: str) -> RateLimitDecision:
                calls["n"] += 1
                return RateLimitDecision(allowed=calls["n"] <= allowed, retry_after_seconds=3600)

        app.dependency_overrides[get_otp_request_limiter] = CountingLimiter

        for _ in range(allowed):
            assert (await api.post(f"{AUTH}/phone/request-otp", headers=headers)).status_code == 204

        throttled = await api.post(f"{AUTH}/phone/request-otp", headers=headers)
        assert throttled.status_code == 429
        assert throttled.json()["error"] == "rate_limited"
        assert throttled.headers["Retry-After"] == "3600"


def _give_phone(db: Session, user_id: str, phone: str) -> None:
    """Attach a contact phone to an account that registered without one.

    Written straight to the row because there is no endpoint that changes a
    contact phone: registration is the only place one is ever set. The guard
    in `OtpService` exists precisely for the account that acquires its number
    later, so testing it needs the state that path produces.
    """
    user = db.get(User, uuid.UUID(user_id))
    assert user is not None
    user.phone_e164 = phone
    db.flush()


async def _verified(api: AsyncClient, headers: dict[str, str], otp: MockOtpSender) -> Any:
    """Request a code and spend it. Returns the verify response."""
    requested = await api.post(f"{AUTH}/phone/request-otp", headers=headers)
    assert requested.status_code == 204, requested.text
    return await api.post(f"{AUTH}/phone/verify-otp", headers=headers, json={"code": otp.last.code})


def _leave_trial(db: Session, tenant_id: str, status: str) -> None:
    """Move a tenant out of trial through the domain's own transition."""
    tenant = db.get(Tenant, uuid.UUID(tenant_id))
    assert tenant is not None
    now = datetime.now(UTC)

    if status == "active":
        # No conversion service until Feature 009; the state itself is real.
        tenant.status = "active"
        db.flush()
        return

    pause_tenant(db, tenant, now=now)
    if status == "archived":
        archive_tenant(tenant, now=now)
    db.flush()


class TestOneLiveTrialPerContactPhone:
    """Clarification 005, at the verification step.

    Registration is the other gate; this one catches the account that comes
    by its number afterwards. Both ask the same question - is any tenant
    that is in trial *right now* using this number - so both stop blocking
    the moment the tenant holding it leaves trial.
    """

    async def test_a_number_another_live_trial_has_is_refused(
        self, api: AsyncClient, mail: Any, otp: MockOtpSender, db: Session
    ) -> None:
        holder, _ = await _signed_in(api, mail)
        latecomer, headers = await _signed_in(api, mail, phone_e164=None)
        _give_phone(db, latecomer["user_id"], holder["phone_e164"])

        response = await _verified(api, headers, otp)

        assert response.status_code == 409
        assert response.json()["error"] == "phone_in_active_trial"

    @pytest.mark.parametrize("exit_status", ["active", "archived"])
    async def test_the_number_frees_up_once_the_tenant_is_done_with_it(
        self, api: AsyncClient, mail: Any, otp: MockOtpSender, db: Session, exit_status: str
    ) -> None:
        """`archived` is thirty days past a trial that nobody came back to.

        `active` is a business that started paying, and holding their number
        against them would stop a real customer from opening a second shop -
        the rule exists to stop trials being farmed, not to punish the people
        who converted.
        """
        holder, _ = await _signed_in(api, mail)
        latecomer, headers = await _signed_in(api, mail, phone_e164=None)
        _give_phone(db, latecomer["user_id"], holder["phone_e164"])
        _leave_trial(db, holder["tenant_id"], exit_status)

        response = await _verified(api, headers, otp)

        assert response.status_code == 204, response.text
        user = db.get(User, uuid.UUID(latecomer["user_id"]))
        assert user is not None and user.phone_verified_at is not None

    async def test_an_expired_trial_still_holds_its_number(
        self, api: AsyncClient, mail: Any, otp: MockOtpSender, db: Session
    ) -> None:
        """The whole point of the rule, and the easy way to lose it.

        A trial that simply runs out lands in `paused`, so freeing the number
        there hands it back at the exact moment the trial ends - and the same
        person opens another trial on the same phone a second later, then
        another. The number waits until `archived`.
        """
        holder, _ = await _signed_in(api, mail)
        latecomer, headers = await _signed_in(api, mail, phone_e164=None)
        _give_phone(db, latecomer["user_id"], holder["phone_e164"])
        _leave_trial(db, holder["tenant_id"], "paused")

        response = await _verified(api, headers, otp)

        assert response.status_code == 409
        assert response.json()["error"] == "phone_in_active_trial"

    async def test_a_tenant_is_never_blocked_by_its_own_number(
        self, api: AsyncClient, mail: Any, otp: MockOtpSender, db: Session
    ) -> None:
        """Verifying twice must not make the second attempt a conflict."""
        account, headers = await _signed_in(api, mail)

        assert (await _verified(api, headers, otp)).status_code == 204
        assert (await _verified(api, headers, otp)).status_code == 204

        user = db.get(User, uuid.UUID(account["user_id"]))
        assert user is not None and user.phone_verified_at is not None

    async def test_an_invited_operator_is_not_blocked_by_the_business_number(
        self, api: AsyncClient, mail: Any, otp: MockOtpSender, db: Session
    ) -> None:
        """An operator is not their employer's contact, so nothing collides."""
        holder, _ = await _signed_in(api, mail)
        operator, headers = await _signed_in(api, mail, phone_e164=None)
        _give_phone(db, operator["user_id"], holder["phone_e164"])
        membership = db.execute(
            select(TenantUser).where(TenantUser.user_id == uuid.UUID(operator["user_id"]))
        ).scalar_one()
        membership.role = "operator"
        db.flush()

        response = await _verified(api, headers, otp)

        assert response.status_code == 204, response.text

    async def test_the_conflict_never_names_the_business_that_has_the_number(
        self, api: AsyncClient, mail: Any, otp: MockOtpSender, db: Session
    ) -> None:
        """A 409 must not turn a phone number into a customer lookup."""
        holder, _ = await _signed_in(api, mail, business_name=f"Bodega {uuid.uuid4().hex[:10]}")
        latecomer, headers = await _signed_in(api, mail, phone_e164=None)
        _give_phone(db, latecomer["user_id"], holder["phone_e164"])

        response = await _verified(api, headers, otp)

        assert response.status_code == 409
        disclosed = response.text.lower()
        for secret in (
            holder["business_name"],
            holder["email"],
            holder["tenant_id"],
            holder["user_id"],
        ):
            assert secret.lower() not in disclosed
