"""One business never reaches another's connection requests (TASK-017).

There is a deliberate exception here worth stating: number uniqueness *is*
global. A tenant may discover that a number is taken - that is RF-008 working.
What it must not learn is by whom, or anything else about that business.
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
from app.models.whatsapp import ConnectionRequest, WhatsAppNumber

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


async def _owner(api: AsyncClient, mail: Any, db: Session, label: str) -> dict[str, str]:
    body = {
        "full_name": f"Owner {label}",
        "email": f"{label}-{uuid.uuid4().hex[:12]}@example.com",
        "password": PASSWORD,
        "phone_e164": _unique_number(),
        "business_name": f"Negocio {label}",
    }
    created = await api.post(f"{AUTH}/register", json=body)
    assert created.status_code == 201
    await api.post(f"{AUTH}/verify-email", json={"token": _link_token(mail.last)})

    user = db.get(User, uuid.UUID(created.json()["user_id"]))
    assert user is not None
    user.phone_verified_at = datetime.now(UTC)
    db.commit()

    session = await api.post(f"{AUTH}/login", json={"email": body["email"], "password": PASSWORD})
    return {"Authorization": f"Bearer {session.json()['access_token']}"}


class TestOneBusinessNeverSeesAnothersRequests:
    async def test_the_history_is_scoped(self, api: AsyncClient, mail: Any, db: Session) -> None:
        headers_a = await _owner(api, mail, db, "a")
        headers_b = await _owner(api, mail, db, "b")

        await api.post(
            f"{TENANT}/connection-requests",
            headers=headers_b,
            json={"phone_e164": _unique_number(), "display_name": "Negocio B"},
        )
        assert (await api.get(f"{TENANT}/connection-requests", headers=headers_a)).json() == []

    async def test_a_foreign_connected_number_is_not_returned(
        self, api: AsyncClient, mail: Any, db: Session
    ) -> None:
        headers_a = await _owner(api, mail, db, "a")
        headers_b = await _owner(api, mail, db, "b")

        tenant_b = uuid.UUID((await api.get(TENANT, headers=headers_b)).json()["id"])
        db.add(
            WhatsAppNumber(
                tenant_id=tenant_b,
                phone_e164=_unique_number(),
                display_name="Negocio B",
                status="connected",
            )
        )
        db.commit()

        assert (await api.get(f"{TENANT}/whatsapp-number", headers=headers_a)).status_code == 404

    async def test_the_conflict_reveals_nothing_about_the_holder(
        self, api: AsyncClient, mail: Any, db: Session
    ) -> None:
        """RF-008 means a tenant *does* learn the number is taken. It must not
        learn by whom - that would map numbers to competitors."""
        headers_a = await _owner(api, mail, db, "a")
        headers_b = await _owner(api, mail, db, "b")

        taken = _unique_number()
        tenant_b = uuid.UUID((await api.get(TENANT, headers=headers_b)).json()["id"])
        db.add(
            WhatsAppNumber(
                tenant_id=tenant_b,
                phone_e164=taken,
                display_name="Panaderia Secreta de B",
                status="connected",
            )
        )
        db.commit()

        conflict = await api.post(
            f"{TENANT}/connection-requests",
            headers=headers_a,
            json={"phone_e164": taken, "display_name": "Negocio A"},
        )
        assert conflict.status_code == 409
        body = conflict.text
        assert "Panaderia Secreta" not in body
        assert str(tenant_b) not in body

    async def test_a_foreign_request_cannot_be_chained_as_a_retry(
        self, api: AsyncClient, mail: Any, db: Session
    ) -> None:
        """`previous_request_id` is caller-supplied. Pointing it at another
        business's request must not attach their history to yours."""
        headers_a = await _owner(api, mail, db, "a")
        headers_b = await _owner(api, mail, db, "b")

        created_b = await api.post(
            f"{TENANT}/connection-requests",
            headers=headers_b,
            json={"phone_e164": _unique_number(), "display_name": "Negocio B"},
        )
        foreign_id = uuid.UUID(created_b.json()["id"])
        row = db.get(ConnectionRequest, foreign_id)
        assert row is not None
        row.status = "rejected"
        db.commit()

        response = await api.post(
            f"{TENANT}/connection-requests",
            headers=headers_a,
            json={
                "phone_e164": _unique_number(),
                "display_name": "Negocio A",
                "previous_request_id": str(foreign_id),
            },
        )
        # Whether it is refused or accepted, A must never end up able to read
        # B's request through the chain.
        history_a = (await api.get(f"{TENANT}/connection-requests", headers=headers_a)).json()
        assert all(entry["id"] != str(foreign_id) for entry in history_a)
        assert response.status_code in (201, 400, 404, 409)
