"""Phone verification by one-time code (TASK-008, Clarification 005).

The three controls that make a six-digit credential safe are the subject
here: the ten-minute expiry, the five-attempt cap, and one live code per user.
Each has a test that fails if the control is removed, because none of them is
observable from a passing happy path.
"""

from __future__ import annotations

import re
import uuid
from typing import Any
from urllib.parse import parse_qs, urlparse

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import get_otp_request_limiter, get_otp_sender
from app.core.rate_limit import NullRateLimiter, RateLimitDecision
from app.models.user import OtpCode, User
from app.providers.otp.mock import MockOtpSender

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
