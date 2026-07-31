"""Connection requests and their three hard rules (TASK-017).

Each rule is enforced by a database constraint, not by a check in front of
one, because all three are races otherwise. These prove the named error
arrives, and the isolation suite proves the constraint is what decides.
"""

from __future__ import annotations

import re
import uuid
from datetime import UTC, datetime
from typing import Any
from urllib.parse import parse_qs, urlparse

from httpx import AsyncClient
from sqlalchemy.orm import Session

from app.models.user import User
from app.models.whatsapp import WhatsAppNumber

AUTH = "/api/v1/auth"
TENANT = "/api/v1/tenant"
PASSWORD = "una-clave-suficientemente-larga"
_HREF = re.compile(r'href="([^"]+)"')


def _link_token(message: Any) -> str:
    for href in _HREF.findall(message.html):
        query = parse_qs(urlparse(href).query)
        if "token" in query:
            return str(query["token"][0])
    raise AssertionError("no tokenised link in the email body")


def _unique_number() -> str:
    return f"+519{uuid.uuid4().int % 10**8:08d}"


async def _owner(
    api: AsyncClient, mail: Any, db: Session, *, phone_verified: bool = True
) -> tuple[dict[str, str], uuid.UUID]:
    body = {
        "full_name": "Ana Quispe",
        "email": f"ana-{uuid.uuid4().hex[:12]}@example.com",
        "password": PASSWORD,
        "phone_e164": _unique_number(),
        "business_name": "Panaderia Quispe",
    }
    created = await api.post(f"{AUTH}/register", json=body)
    assert created.status_code == 201
    await api.post(f"{AUTH}/verify-email", json={"token": _link_token(mail.last)})

    user_id = uuid.UUID(created.json()["user_id"])
    if phone_verified:
        # Set directly: the OTP round trip is TASK-008's subject, not this
        # file's, and driving it here would couple two suites.
        user = db.get(User, user_id)
        assert user is not None
        user.phone_verified_at = datetime.now(UTC)
        db.commit()

    session = await api.post(f"{AUTH}/login", json={"email": body["email"], "password": PASSWORD})
    return {"Authorization": f"Bearer {session.json()['access_token']}"}, user_id


def _payload(**overrides: Any) -> dict[str, Any]:
    body: dict[str, Any] = {
        "phone_e164": _unique_number(),
        "display_name": "Panaderia Leo",
        "business_description": "Panaderia artesanal en Miraflores",
    }
    body.update(overrides)
    return body


class TestPhoneVerificationGate:
    async def test_an_unverified_phone_cannot_request(
        self, api: AsyncClient, mail: Any, db: Session
    ) -> None:
        """Clarification 005's anti-abuse gate: without it a throwaway account
        can queue work for a human being."""
        headers, _ = await _owner(api, mail, db, phone_verified=False)
        response = await api.post(f"{TENANT}/connection-requests", headers=headers, json=_payload())
        assert response.status_code == 403
        assert response.json()["error"] == "phone_not_verified"

    async def test_a_verified_phone_can(self, api: AsyncClient, mail: Any, db: Session) -> None:
        headers, _ = await _owner(api, mail, db)
        response = await api.post(f"{TENANT}/connection-requests", headers=headers, json=_payload())
        assert response.status_code == 201
        assert response.json()["status"] == "requested"


class TestGlobalUniqueness:
    async def test_a_connected_number_cannot_be_requested_by_anyone(
        self, api: AsyncClient, mail: Any, db: Session
    ) -> None:
        """RF-008. Two businesses answering on one number is not a conflict to
        resolve later - it is a customer talking to the wrong company."""
        headers_a, _ = await _owner(api, mail, db)
        headers_b, _ = await _owner(api, mail, db)

        taken = _unique_number()
        tenant_b = uuid.UUID((await api.get(TENANT, headers=headers_b)).json()["id"])
        db.add(
            WhatsAppNumber(
                tenant_id=tenant_b,
                phone_e164=taken,
                display_name="Negocio B",
                status="connected",
            )
        )
        db.commit()

        response = await api.post(
            f"{TENANT}/connection-requests", headers=headers_a, json=_payload(phone_e164=taken)
        )
        assert response.status_code == 409
        assert response.json()["error"] == "phone_already_connected"

    async def test_an_open_request_blocks_a_second_one_from_another_tenant(
        self, api: AsyncClient, mail: Any, db: Session
    ) -> None:
        headers_a, _ = await _owner(api, mail, db)
        headers_b, _ = await _owner(api, mail, db)

        contested = _unique_number()
        first = await api.post(
            f"{TENANT}/connection-requests",
            headers=headers_a,
            json=_payload(phone_e164=contested),
        )
        assert first.status_code == 201

        second = await api.post(
            f"{TENANT}/connection-requests",
            headers=headers_b,
            json=_payload(phone_e164=contested),
        )
        assert second.status_code == 409
        assert second.json()["error"] == "open_request_exists"

    async def test_the_same_tenant_cannot_file_twice(
        self, api: AsyncClient, mail: Any, db: Session
    ) -> None:
        headers, _ = await _owner(api, mail, db)
        number = _unique_number()
        assert (
            await api.post(
                f"{TENANT}/connection-requests", headers=headers, json=_payload(phone_e164=number)
            )
        ).status_code == 201
        again = await api.post(
            f"{TENANT}/connection-requests", headers=headers, json=_payload(phone_e164=number)
        )
        assert again.status_code == 409


class TestRetryAfterRejection:
    async def test_a_rejected_request_frees_the_number(
        self, api: AsyncClient, mail: Any, db: Session
    ) -> None:
        """Clarification 012: the partial index covers only open statuses, so
        a customer can correct their details and file again."""
        from app.models.whatsapp import ConnectionRequest

        headers, _ = await _owner(api, mail, db)
        number = _unique_number()
        first = await api.post(
            f"{TENANT}/connection-requests", headers=headers, json=_payload(phone_e164=number)
        )
        request_id = uuid.UUID(first.json()["id"])

        rejected = db.get(ConnectionRequest, request_id)
        assert rejected is not None
        rejected.status = "rejected"
        rejected.rejection_reason = "El nombre no cumple las politicas de Meta"
        db.commit()

        retry = await api.post(
            f"{TENANT}/connection-requests",
            headers=headers,
            json=_payload(phone_e164=number, previous_request_id=str(request_id)),
        )
        assert retry.status_code == 201

    async def test_the_history_shows_the_rejection_reason(
        self, api: AsyncClient, mail: Any, db: Session
    ) -> None:
        """What tells the customer what to fix. A bare status would not."""
        from app.models.whatsapp import ConnectionRequest

        headers, _ = await _owner(api, mail, db)
        created = await api.post(f"{TENANT}/connection-requests", headers=headers, json=_payload())
        row = db.get(ConnectionRequest, uuid.UUID(created.json()["id"]))
        assert row is not None
        row.status = "rejected"
        row.rejection_reason = "El numero ya tiene una WABA activa"
        db.commit()

        history = (await api.get(f"{TENANT}/connection-requests", headers=headers)).json()
        assert history[0]["rejection_reason"] == "El numero ya tiene una WABA activa"


class TestValidation:
    async def test_a_malformed_number_is_rejected(
        self, api: AsyncClient, mail: Any, db: Session
    ) -> None:
        headers, _ = await _owner(api, mail, db)
        response = await api.post(
            f"{TENANT}/connection-requests", headers=headers, json=_payload(phone_e164="999888777")
        )
        assert response.status_code == 400

    async def test_a_display_name_over_whatsapps_limit_is_rejected(
        self, api: AsyncClient, mail: Any, db: Session
    ) -> None:
        """25 characters. Accepting more would mean a request the admin cannot
        actually fulfil."""
        headers, _ = await _owner(api, mail, db)
        response = await api.post(
            f"{TENANT}/connection-requests", headers=headers, json=_payload(display_name="x" * 30)
        )
        assert response.status_code == 400


class TestNumberEndpoint:
    async def test_no_number_yet_is_404(self, api: AsyncClient, mail: Any, db: Session) -> None:
        headers, _ = await _owner(api, mail, db)
        response = await api.get(f"{TENANT}/whatsapp-number", headers=headers)
        assert response.status_code == 404
        assert response.json()["error"] == "no_number"

    async def test_a_connected_number_is_returned_without_credentials(
        self, api: AsyncClient, mail: Any, db: Session
    ) -> None:
        """A tenant has no use for its own provider tokens, and returning them
        would put them in browser history, logs and screenshots."""
        headers, _ = await _owner(api, mail, db)
        tenant_id = uuid.UUID((await api.get(TENANT, headers=headers)).json()["id"])
        db.add(
            WhatsAppNumber(
                tenant_id=tenant_id,
                phone_e164=_unique_number(),
                display_name="Panaderia Leo",
                status="connected",
                credentials_enc=b"encrypted-blob",
            )
        )
        db.commit()

        body = (await api.get(f"{TENANT}/whatsapp-number", headers=headers)).json()
        assert body["status"] == "connected"
        assert "credentials_enc" not in body
        assert "credentials" not in body

    async def test_a_disconnected_number_is_not_returned(
        self, api: AsyncClient, mail: Any, db: Session
    ) -> None:
        headers, _ = await _owner(api, mail, db)
        tenant_id = uuid.UUID((await api.get(TENANT, headers=headers)).json()["id"])
        db.add(
            WhatsAppNumber(
                tenant_id=tenant_id,
                phone_e164=_unique_number(),
                display_name="Panaderia Leo",
                status="disconnected",
            )
        )
        db.commit()
        assert (await api.get(f"{TENANT}/whatsapp-number", headers=headers)).status_code == 404
