"""Photos, tags and copies of a product never cross businesses (Feature 018)."""

from __future__ import annotations

from typing import Any

import pytest
from fastapi import FastAPI
from httpx import AsyncClient

from app.api.deps import get_storage_provider
from app.providers.storage.mock import MockStorageProvider
from tests.isolation.test_payment_isolation import _owner

P = "/api/v1/tenant/products"
PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 100


@pytest.fixture(autouse=True)
def _storage(app: FastAPI) -> None:
    store = MockStorageProvider()
    app.dependency_overrides[get_storage_provider] = lambda: store


async def test_another_business_cannot_see_or_touch_them(api: AsyncClient, mail: Any) -> None:
    a = await _owner(api, mail, "a")
    b = await _owner(api, mail, "b")
    product = (
        await api.post(P, headers=b, json={"name": "Polo", "price": "25", "tags": ["secreto"]})
    ).json()
    photo = (
        await api.post(f"{P}/{product['id']}/photos", headers=b, files={"file": ("p.png", PNG)})
    ).json()["items"][0]

    assert (await api.get(P, headers=a, params={"tag": "secreto"})).json()["total"] == 0
    assert (await api.get(P, headers=a, params={"search": "secreto"})).json()["total"] == 0
    assert (await api.get(f"{P}/{product['id']}/photos", headers=a)).status_code == 404
    assert (
        await api.post(f"{P}/{product['id']}/photos", headers=a, files={"file": ("p.png", PNG)})
    ).status_code == 404
    assert (
        await api.put(f"{P}/{product['id']}/photos/order", headers=a, json={"ids": [photo["id"]]})
    ).status_code == 404
    assert (
        await api.delete(f"{P}/{product['id']}/photos/{photo['id']}", headers=a)
    ).status_code == 404
    assert (await api.post(f"{P}/{product['id']}/duplicate", headers=a)).status_code == 404
    assert len((await api.get(f"{P}/{product['id']}/photos", headers=b)).json()["items"]) == 1
