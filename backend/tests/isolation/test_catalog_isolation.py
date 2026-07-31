"""One business never reaches another's catalogue (TASK-011).

Driven over HTTP with two signed-in owners, because that is the shape a leak
takes: a valid token and somebody else's product id.
"""

from __future__ import annotations

import re
import uuid
from typing import Any
from urllib.parse import parse_qs, urlparse

import pytest
from httpx import AsyncClient
from sqlalchemy.orm import Session

from app.api.deps import get_redis
from app.models.business import Product

AUTH = "/api/v1/auth"
PRODUCTS = "/api/v1/tenant/products"
PASSWORD = "una-clave-suficientemente-larga"
_HREF = re.compile(r'href="([^"]+)"')


class FakeRedis:
    def __init__(self) -> None:
        self.store: dict[str, str] = {}

    async def set(self, key: str, value: str, *, ex: int | None = None) -> None:
        self.store[key] = value

    async def get(self, key: str) -> str | None:
        return self.store.get(key)

    async def delete(self, key: str) -> int:
        return 1 if self.store.pop(key, None) is not None else 0


@pytest.fixture
def redis(app: Any) -> FakeRedis:
    fake = FakeRedis()
    app.dependency_overrides[get_redis] = lambda: fake
    return fake


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


class TestOneBusinessNeverReachesAnothersCatalogue:
    async def test_the_two_owners_are_distinct(self, api: AsyncClient, mail: Any) -> None:
        """Guards the fixture: one tenant twice would pass every case below."""
        a = await _owner(api, mail, "a")
        b = await _owner(api, mail, "b")
        assert a != b

    async def test_listing_is_scoped(self, api: AsyncClient, mail: Any) -> None:
        headers_a = await _owner(api, mail, "a")
        headers_b = await _owner(api, mail, "b")

        await api.post(PRODUCTS, headers=headers_b, json={"name": "Secreto de B", "price": "9.00"})

        listing = (await api.get(PRODUCTS, headers=headers_a)).json()
        assert listing["total"] == 0
        assert listing["items"] == []

    async def test_a_foreign_product_cannot_be_read(self, api: AsyncClient, mail: Any) -> None:
        headers_a = await _owner(api, mail, "a")
        headers_b = await _owner(api, mail, "b")

        created = await api.post(
            PRODUCTS, headers=headers_b, json={"name": "Secreto de B", "price": "9.00"}
        )
        response = await api.get(f"{PRODUCTS}/{created.json()['id']}", headers=headers_a)
        assert response.status_code == 404

    async def test_a_foreign_product_cannot_be_updated(
        self, api: AsyncClient, mail: Any, db: Session
    ) -> None:
        headers_a = await _owner(api, mail, "a")
        headers_b = await _owner(api, mail, "b")

        created = await api.post(
            PRODUCTS, headers=headers_b, json={"name": "Nombre de B", "price": "9.00"}
        )
        product_id = uuid.UUID(created.json()["id"])

        refused = await api.put(
            f"{PRODUCTS}/{product_id}", headers=headers_a, json={"name": "Secuestrado"}
        )
        assert refused.status_code == 404

        victim = db.get(Product, product_id)
        assert victim is not None
        db.refresh(victim)
        assert victim.name == "Nombre de B"

    async def test_a_foreign_product_cannot_be_deleted(
        self, api: AsyncClient, mail: Any, db: Session
    ) -> None:
        headers_a = await _owner(api, mail, "a")
        headers_b = await _owner(api, mail, "b")

        created = await api.post(PRODUCTS, headers=headers_b, json={"name": "B", "price": "9.00"})
        product_id = uuid.UUID(created.json()["id"])

        assert (await api.delete(f"{PRODUCTS}/{product_id}", headers=headers_a)).status_code == 404

        survivor = db.get(Product, product_id)
        assert survivor is not None
        db.refresh(survivor)
        assert survivor.deleted_at is None

    async def test_a_foreign_id_is_indistinguishable_from_a_missing_one(
        self, api: AsyncClient, mail: Any
    ) -> None:
        """A different answer would confirm the id exists somewhere."""
        headers_a = await _owner(api, mail, "a")
        headers_b = await _owner(api, mail, "b")

        created = await api.post(PRODUCTS, headers=headers_b, json={"name": "B", "price": "9.00"})
        foreign = await api.get(f"{PRODUCTS}/{created.json()['id']}", headers=headers_a)
        invented = await api.get(f"{PRODUCTS}/{uuid.uuid4()}", headers=headers_a)

        assert foreign.status_code == invented.status_code == 404
        assert foreign.json() == invented.json()

    async def test_a_foreign_import_preview_cannot_be_confirmed(
        self, api: AsyncClient, mail: Any, redis: FakeRedis
    ) -> None:
        """The preview key is namespaced by tenant, so holding the id is not
        enough - otherwise one business could load its catalogue into another."""
        headers_a = await _owner(api, mail, "a")
        headers_b = await _owner(api, mail, "b")

        preview = await api.post(
            f"{PRODUCTS}/import",
            headers=headers_b,
            files={"file": ("c.csv", b"nombre,precio\nDe B,9.00\n", "text/csv")},
        )
        preview_id = preview.json()["preview_id"]

        stolen = await api.post(f"{PRODUCTS}/import/{preview_id}/confirm", headers=headers_a)
        assert stolen.status_code == 404
        assert (await api.get(PRODUCTS, headers=headers_a)).json()["total"] == 0

        # B's own preview still works: it was not consumed by the attempt.
        assert (
            await api.post(f"{PRODUCTS}/import/{preview_id}/confirm", headers=headers_b)
        ).status_code == 201
