"""Feature 018 catalogue: tags, photos, duplicate, the agent finding by tag (TASK-1801)."""

from __future__ import annotations

import uuid
from decimal import Decimal
from typing import Any

import pytest
from fastapi import FastAPI
from httpx import AsyncClient
from sqlalchemy.orm import Session

from app.api.deps import get_storage_provider
from app.models.business import Product
from app.models.tenant import Tenant
from app.providers.storage.mock import MockStorageProvider
from app.repositories.media import MediaAssetRepository
from app.repositories.product import ProductRepository
from tests.integration.test_products import PRODUCTS, _owner, redis  # noqa: F401

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 100
PDF = b"%PDF-1.4\n" + b"0" * 100


@pytest.fixture(autouse=True)
def storage(app: FastAPI) -> MockStorageProvider:
    store = MockStorageProvider()
    app.dependency_overrides[get_storage_provider] = lambda: store
    return store


async def _product(api: AsyncClient, headers: dict[str, str], **extra: Any) -> dict[str, Any]:
    body = {"name": "Certificado SIAF", "price": "120.00", **extra}
    response = await api.post(PRODUCTS, headers=headers, json=body)
    assert response.status_code == 201, response.text
    return dict(response.json())


class TestTags:
    async def test_create_cleans_and_reads_back(self, api: AsyncClient, mail: Any) -> None:
        headers = await _owner(api, mail)
        product = await _product(api, headers, tags=["Oferta", "oferta", " #Gestión "])
        assert product["tags"] == ["Oferta", "Gestión"]
        assert product["foto_url"] is None

    async def test_too_many_or_too_long_is_a_validation_error(
        self, api: AsyncClient, mail: Any
    ) -> None:
        headers = await _owner(api, mail)
        many = await api.post(
            PRODUCTS, headers=headers, json={"name": "X", "price": "1", "tags": list("abcdefghijk")}
        )
        long = await api.post(
            PRODUCTS, headers=headers, json={"name": "X", "price": "1", "tags": ["x" * 31]}
        )
        assert many.status_code == 400 and long.status_code == 400

    async def test_patch_replaces_and_null_is_refused(self, api: AsyncClient, mail: Any) -> None:
        headers = await _owner(api, mail)
        product = await _product(api, headers, tags=["a1"])
        url = f"{PRODUCTS}/{product['id']}"
        changed = await api.put(url, headers=headers, json={"tags": ["verano"]})
        assert changed.json()["tags"] == ["verano"]
        assert (await api.put(url, headers=headers, json={"tags": None})).status_code == 400
        kept = await api.put(url, headers=headers, json={"price": "130.00"})
        assert kept.json()["tags"] == ["verano"]

    async def test_list_filters_by_tag_and_searches_tags(self, api: AsyncClient, mail: Any) -> None:
        headers = await _owner(api, mail)
        await _product(api, headers, name="Excel avanzado", tags=["Ofimática", "oferta"])
        await _product(api, headers, name="Word", tags=["Ofimática"])
        await _product(api, headers, name="SIAF")

        tagged = await api.get(PRODUCTS, headers=headers, params={"tag": "OFERTA"})
        assert [p["name"] for p in tagged.json()["items"]] == ["Excel avanzado"]
        searched = await api.get(PRODUCTS, headers=headers, params={"search": "ofimá"})
        assert searched.json()["total"] == 2


class TestDuplicate:
    async def test_a_copy_is_inactive_and_keeps_the_sale_fields(
        self, api: AsyncClient, mail: Any
    ) -> None:
        headers = await _owner(api, mail)
        original = await _product(
            api, headers, bundle_size=5, bundle_permite_libres=True, destacado=True, tags=["pack"]
        )
        copy = await api.post(f"{PRODUCTS}/{original['id']}/duplicate", headers=headers)
        assert copy.status_code == 201
        body = copy.json()
        assert body["name"] == "Certificado SIAF (copia)"
        assert body["is_active"] is False and body["destacado"] is False
        assert body["bundle_size"] == 5 and body["tags"] == ["pack"]
        assert body["price"] == "120.00"

    async def test_unknown_is_404(self, api: AsyncClient, mail: Any) -> None:
        headers = await _owner(api, mail)
        missing = await api.post(f"{PRODUCTS}/{uuid.uuid4()}/duplicate", headers=headers)
        assert missing.status_code == 404


class TestPhotos:
    async def test_add_order_and_remove(self, api: AsyncClient, mail: Any) -> None:
        headers = await _owner(api, mail)
        product = await _product(api, headers)
        url = f"{PRODUCTS}/{product['id']}/photos"

        first = await api.post(url, headers=headers, files={"file": ("a.png", PNG)})
        second = await api.post(url, headers=headers, files={"file": ("b.png", PNG)})
        assert first.status_code == 201 and second.status_code == 201
        ids = [p["id"] for p in second.json()["items"]]
        assert len(ids) == 2

        reordered = await api.put(f"{url}/order", headers=headers, json={"ids": [ids[1]]})
        assert [p["id"] for p in reordered.json()["items"]] == [ids[1], ids[0]]

        listing = await api.get(PRODUCTS, headers=headers)
        assert listing.json()["items"][0]["foto_url"]

        assert (await api.delete(f"{url}/{ids[1]}", headers=headers)).status_code == 204
        left = await api.get(url, headers=headers)
        assert [p["id"] for p in left.json()["items"]] == [ids[0]]

    async def test_photos_are_resources_the_agent_already_offers(
        self, api: AsyncClient, mail: Any, db: Session
    ) -> None:
        headers = await _owner(api, mail)
        product = await _product(api, headers, name="Polo Básico")
        await api.post(
            f"{PRODUCTS}/{product['id']}/photos", headers=headers, files={"file": ("p.png", PNG)}
        )
        row = db.get(Product, uuid.UUID(product["id"]))
        assert row is not None
        assets = MediaAssetRepository(db, row.tenant_id).for_product(row.id)
        assert [a.clave for a in assets] == ["foto_polo_basico_1"]
        assert assets[0].cuando_usar.startswith("Cuando quiera ver Polo Básico")

    async def test_not_an_image_and_the_limit(self, api: AsyncClient, mail: Any) -> None:
        headers = await _owner(api, mail)
        product = await _product(api, headers)
        url = f"{PRODUCTS}/{product['id']}/photos"
        pdf = await api.post(url, headers=headers, files={"file": ("a.pdf", PDF)})
        assert pdf.status_code == 422 and pdf.json()["error"] == "not_an_image"
        for _ in range(10):
            assert (
                await api.post(url, headers=headers, files={"file": ("a.png", PNG)})
            ).status_code == 201
        eleventh = await api.post(url, headers=headers, files={"file": ("a.png", PNG)})
        assert eleventh.status_code == 422 and eleventh.json()["error"] == "too_many_photos"


class TestTheAgentFindsByTag:
    def test_by_tag_and_by_a_tag_inside_the_words(self, db: Session) -> None:
        tenant = Tenant(name="Capacita", status="trial", plan_id="trial")
        db.add(tenant)
        db.flush()
        repo = ProductRepository(db, tenant.id)
        repo.add(name="Certificado A", price=Decimal("120"), tags=["SIAF", "gestión pública"])
        repo.add(name="Certificado B", price=Decimal("90"), tags=["tv"])
        repo.add(name="Excel", price=Decimal("80"))
        db.flush()

        def names(query: str) -> list[str]:
            return [p.name for p in repo.search_active(query, limit=10)]

        assert names("siaf") == ["Certificado A"]
        assert names("quiero el certificado de siaf") == ["Certificado A"]
        assert names("pública") == ["Certificado A"]
        assert names("excel") == ["Excel"]
        # A two-letter tag never matches by being inside other words.
        assert names("tvs") == []
        assert names("50%_") == []


class TestImport:
    async def test_the_import_reads_etiquetas(
        self,
        api: AsyncClient,
        mail: Any,
        redis: Any,  # noqa: F811
    ) -> None:
        headers = await _owner(api, mail)
        csv = 'nombre,precio,etiquetas\nPolo,25.00,"verano, oferta; Verano"\nGorra,15,\n'
        preview = await api.post(
            f"{PRODUCTS}/import", headers=headers, files={"file": ("c.csv", csv.encode())}
        )
        assert preview.status_code == 200, preview.text
        assert preview.json()["valid_rows"] == 2
        await api.post(f"{PRODUCTS}/import/{preview.json()['preview_id']}/confirm", headers=headers)
        items = {
            p["name"]: p["tags"] for p in (await api.get(PRODUCTS, headers=headers)).json()["items"]
        }
        assert items == {"Polo": ["verano", "oferta"], "Gorra": []}
