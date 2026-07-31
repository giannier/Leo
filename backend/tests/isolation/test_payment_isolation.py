"""One business never reaches another's payment instructions (TASK-013).

Payment methods carry the text a customer is told to send money to. A leak
here is not an information disclosure, it is a route for one business to
redirect another's payments.
"""

from __future__ import annotations

import re
import uuid
from typing import Any
from urllib.parse import parse_qs, urlparse

from httpx import AsyncClient
from sqlalchemy.orm import Session

from app.models.business import PaymentMethodBusiness

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


async def _owner(api: AsyncClient, mail: Any, label: str) -> dict[str, str]:
    body = {
        "full_name": f"Owner {label}",
        "email": f"{label}-{uuid.uuid4().hex[:12]}@example.com",
        "password": PASSWORD,
        "business_name": f"Negocio {label}",
    }
    assert (await api.post(f"{AUTH}/register", json=body)).status_code == 201
    await api.post(f"{AUTH}/verify-email", json={"token": _link_token(mail.last)})
    session = await api.post(f"{AUTH}/login", json={"email": body["email"], "password": PASSWORD})
    return {"Authorization": f"Bearer {session.json()['access_token']}"}


class TestOneBusinessNeverReachesAnothersPaymentDetails:
    async def test_listing_is_scoped(self, api: AsyncClient, mail: Any) -> None:
        headers_a = await _owner(api, mail, "a")
        headers_b = await _owner(api, mail, "b")

        await api.post(
            f"{TENANT}/payment-methods",
            headers=headers_b,
            json={"method": "yape", "instructions": "Yape al 999 de B"},
        )
        assert (await api.get(f"{TENANT}/payment-methods", headers=headers_a)).json() == []

    async def test_a_foreign_method_cannot_be_edited(
        self, api: AsyncClient, mail: Any, db: Session
    ) -> None:
        """The attack this blocks: rewriting somebody else's Yape number so
        their customers pay you instead."""
        headers_a = await _owner(api, mail, "a")
        headers_b = await _owner(api, mail, "b")

        created = await api.post(
            f"{TENANT}/payment-methods",
            headers=headers_b,
            json={"method": "yape", "instructions": "Yape al 999 de B"},
        )
        method_id = uuid.UUID(created.json()["id"])

        refused = await api.put(
            f"{TENANT}/payment-methods/{method_id}",
            headers=headers_a,
            json={"instructions": "Yape al 111 del atacante"},
        )
        assert refused.status_code == 404

        victim = db.get(PaymentMethodBusiness, method_id)
        assert victim is not None
        db.refresh(victim)
        assert victim.instructions == "Yape al 999 de B"

    async def test_a_foreign_method_cannot_be_deleted(
        self, api: AsyncClient, mail: Any, db: Session
    ) -> None:
        headers_a = await _owner(api, mail, "a")
        headers_b = await _owner(api, mail, "b")

        created = await api.post(
            f"{TENANT}/payment-methods",
            headers=headers_b,
            json={"method": "plin", "instructions": "Plin al 999 de B"},
        )
        method_id = uuid.UUID(created.json()["id"])

        assert (
            await api.delete(f"{TENANT}/payment-methods/{method_id}", headers=headers_a)
        ).status_code == 404
        assert db.get(PaymentMethodBusiness, method_id) is not None

    async def test_the_same_method_is_allowed_in_different_businesses(
        self, api: AsyncClient, mail: Any
    ) -> None:
        """The unique constraint is per tenant. Two bakeries both using Yape is
        the normal case, not a conflict."""
        headers_a = await _owner(api, mail, "a")
        headers_b = await _owner(api, mail, "b")

        for headers in (headers_a, headers_b):
            response = await api.post(
                f"{TENANT}/payment-methods",
                headers=headers,
                json={"method": "yape", "instructions": "Yape al 999"},
            )
            assert response.status_code == 201

    async def test_the_checklist_reads_only_its_own_tenant(
        self, api: AsyncClient, mail: Any
    ) -> None:
        """B satisfying an item must never mark it done for A - the failure
        that would let one business activate on another's setup."""
        headers_a = await _owner(api, mail, "a")
        headers_b = await _owner(api, mail, "b")

        for path, payload in (
            ("/business-profile", {"business_name": "B", "industry": "retail"}),
            ("/agent-config", {"agent_name": "Bot", "objective": "vender"}),
        ):
            await api.put(f"{TENANT}{path}", headers=headers_b, json=payload)
        await api.post(f"{TENANT}/products", headers=headers_b, json={"name": "X", "price": "1.00"})
        await api.post(
            f"{TENANT}/payment-methods",
            headers=headers_b,
            json={"method": "yape", "instructions": "Yape al 999"},
        )

        checklist_b = (await api.get(f"{TENANT}/activation-checklist", headers=headers_b)).json()
        checklist_a = (await api.get(f"{TENANT}/activation-checklist", headers=headers_a)).json()

        assert checklist_b["sandbox_ready"] is True
        assert checklist_a["sandbox_ready"] is False

    async def test_a_foreign_id_is_indistinguishable_from_a_missing_one(
        self, api: AsyncClient, mail: Any
    ) -> None:
        headers_a = await _owner(api, mail, "a")
        headers_b = await _owner(api, mail, "b")

        created = await api.post(
            f"{TENANT}/payment-methods",
            headers=headers_b,
            json={"method": "yape", "instructions": "Yape al 999"},
        )
        foreign = await api.delete(
            f"{TENANT}/payment-methods/{created.json()['id']}", headers=headers_a
        )
        invented = await api.delete(f"{TENANT}/payment-methods/{uuid.uuid4()}", headers=headers_a)

        assert foreign.status_code == invented.status_code == 404
        assert foreign.json() == invented.json()
