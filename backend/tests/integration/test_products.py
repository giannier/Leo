"""Product catalogue CRUD and the two-step import (TASK-011)."""

from __future__ import annotations

import re
import uuid
from typing import Any
from urllib.parse import parse_qs, urlparse

import pytest
from httpx import AsyncClient

from app.api.deps import get_redis

AUTH = "/api/v1/auth"
PRODUCTS = "/api/v1/tenant/products"
PASSWORD = "una-clave-suficientemente-larga"
_HREF = re.compile(r'href="([^"]+)"')


class FakeRedis:
    """Enough Redis for the preview: set, get, delete, and a TTL we record.

    A real client would need a running server for a test whose subject is the
    import, not the cache. The TTL is captured so the one-hour expiry the
    contract specifies is still asserted.
    """

    def __init__(self) -> None:
        self.store: dict[str, str] = {}
        self.ttls: dict[str, int | None] = {}

    async def set(self, key: str, value: str, *, ex: int | None = None) -> None:
        self.store[key] = value
        self.ttls[key] = ex

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


async def _owner(api: AsyncClient, mail: Any) -> dict[str, str]:
    body = {
        "full_name": "Ana Quispe",
        "email": f"ana-{uuid.uuid4().hex[:12]}@example.com",
        "password": PASSWORD,
        "business_name": "Panaderia Quispe",
    }
    assert (await api.post(f"{AUTH}/register", json=body)).status_code == 201
    await api.post(f"{AUTH}/verify-email", json={"token": _link_token(mail.last)})
    session = await api.post(f"{AUTH}/login", json={"email": body["email"], "password": PASSWORD})
    return {"Authorization": f"Bearer {session.json()['access_token']}"}


class TestCrud:
    async def test_create_and_read_back(self, api: AsyncClient, mail: Any) -> None:
        headers = await _owner(api, mail)
        created = await api.post(
            PRODUCTS,
            headers=headers,
            json={"name": "Lomo saltado", "price": "32.00", "category": "Platos"},
        )
        assert created.status_code == 201
        product_id = created.json()["id"]

        fetched = await api.get(f"{PRODUCTS}/{product_id}", headers=headers)
        assert fetched.status_code == 200
        assert fetched.json()["price"] == "32.00"

    async def test_price_keeps_its_decimals(self, api: AsyncClient, mail: Any) -> None:
        """Money is Decimal end to end. A float would return 19.989999999999998."""
        headers = await _owner(api, mail)
        created = await api.post(PRODUCTS, headers=headers, json={"name": "X", "price": "19.99"})
        assert created.json()["price"] == "19.99"

    async def test_a_negative_price_is_rejected(self, api: AsyncClient, mail: Any) -> None:
        headers = await _owner(api, mail)
        response = await api.post(PRODUCTS, headers=headers, json={"name": "X", "price": "-1.00"})
        assert response.status_code == 400

    async def test_a_partial_update_keeps_other_fields(self, api: AsyncClient, mail: Any) -> None:
        headers = await _owner(api, mail)
        created = await api.post(
            PRODUCTS, headers=headers, json={"name": "Lomo", "price": "32.00", "stock": 5}
        )
        product_id = created.json()["id"]

        updated = await api.put(f"{PRODUCTS}/{product_id}", headers=headers, json={"stock": 3})
        assert updated.json()["name"] == "Lomo"
        assert updated.json()["price"] == "32.00"
        assert updated.json()["stock"] == 3

    async def test_delete_is_soft_and_hides_the_product(self, api: AsyncClient, mail: Any) -> None:
        """Past conversations reference products by id; a hard delete would
        rewrite what the agent said last week."""
        headers = await _owner(api, mail)
        created = await api.post(PRODUCTS, headers=headers, json={"name": "X", "price": "1.00"})
        product_id = created.json()["id"]

        assert (await api.delete(f"{PRODUCTS}/{product_id}", headers=headers)).status_code == 204
        assert (await api.get(f"{PRODUCTS}/{product_id}", headers=headers)).status_code == 404
        assert (await api.get(PRODUCTS, headers=headers)).json()["total"] == 0

    async def test_an_unknown_id_is_404(self, api: AsyncClient, mail: Any) -> None:
        headers = await _owner(api, mail)
        assert (await api.get(f"{PRODUCTS}/{uuid.uuid4()}", headers=headers)).status_code == 404

    async def test_anonymous_callers_are_refused(self, api: AsyncClient) -> None:
        assert (await api.get(PRODUCTS)).status_code == 401


class TestListing:
    async def test_search_and_filters(self, api: AsyncClient, mail: Any) -> None:
        headers = await _owner(api, mail)
        for name, category in [("Lomo saltado", "Platos"), ("Inca Kola", "Bebidas")]:
            await api.post(
                PRODUCTS,
                headers=headers,
                json={"name": name, "price": "5.00", "category": category},
            )

        by_search = await api.get(PRODUCTS, headers=headers, params={"search": "lomo"})
        assert by_search.json()["total"] == 1

        by_category = await api.get(PRODUCTS, headers=headers, params={"category": "Bebidas"})
        assert by_category.json()["total"] == 1

    async def test_the_total_is_the_full_match_not_the_page(
        self, api: AsyncClient, mail: Any
    ) -> None:
        """What lets a client know another page exists."""
        headers = await _owner(api, mail)
        for index in range(3):
            await api.post(PRODUCTS, headers=headers, json={"name": f"P{index}", "price": "1.00"})

        page = await api.get(PRODUCTS, headers=headers, params={"limit": 2})
        assert len(page.json()["items"]) == 2
        assert page.json()["total"] == 3


class TestTemplate:
    async def test_the_template_downloads_as_a_file(self, api: AsyncClient, mail: Any) -> None:
        """The literal path must win over `/{product_id}`, or this 422s."""
        headers = await _owner(api, mail)
        response = await api.get(f"{PRODUCTS}/template.csv", headers=headers)
        assert response.status_code == 200
        assert "attachment" in response.headers["content-disposition"]
        assert "nombre" in response.text


class TestImport:
    async def test_preview_reports_without_creating(
        self, api: AsyncClient, mail: Any, redis: FakeRedis
    ) -> None:
        headers = await _owner(api, mail)
        csv = b"nombre,precio\nLomo,32.00\nMalo,gratis\n"
        preview = await api.post(
            f"{PRODUCTS}/import", headers=headers, files={"file": ("catalogo.csv", csv, "text/csv")}
        )
        assert preview.status_code == 200
        assert preview.json()["valid_rows"] == 1
        assert preview.json()["invalid_rows"] == [{"row": 3, "reason": "precio_invalido"}]

        # Nothing written yet.
        assert (await api.get(PRODUCTS, headers=headers)).json()["total"] == 0

    async def test_confirm_creates_the_valid_rows(
        self, api: AsyncClient, mail: Any, redis: FakeRedis
    ) -> None:
        headers = await _owner(api, mail)
        csv = b"nombre,precio,categoria\nLomo,32.00,Platos\nInca Kola,5.50,Bebidas\n"
        preview = await api.post(
            f"{PRODUCTS}/import", headers=headers, files={"file": ("c.csv", csv, "text/csv")}
        )
        preview_id = preview.json()["preview_id"]

        confirmed = await api.post(f"{PRODUCTS}/import/{preview_id}/confirm", headers=headers)
        assert confirmed.status_code == 201
        assert confirmed.json()["created"] == 2
        assert (await api.get(PRODUCTS, headers=headers)).json()["total"] == 2

    async def test_the_preview_expires_in_an_hour(
        self, api: AsyncClient, mail: Any, redis: FakeRedis
    ) -> None:
        headers = await _owner(api, mail)
        await api.post(
            f"{PRODUCTS}/import",
            headers=headers,
            files={"file": ("c.csv", b"nombre,precio\nX,1.00\n", "text/csv")},
        )
        assert set(redis.ttls.values()) == {3600}

    async def test_confirming_twice_creates_once(
        self, api: AsyncClient, mail: Any, redis: FakeRedis
    ) -> None:
        """The preview is consumed, so a double click cannot import twice."""
        headers = await _owner(api, mail)
        preview = await api.post(
            f"{PRODUCTS}/import",
            headers=headers,
            files={"file": ("c.csv", b"nombre,precio\nX,1.00\n", "text/csv")},
        )
        preview_id = preview.json()["preview_id"]

        assert (
            await api.post(f"{PRODUCTS}/import/{preview_id}/confirm", headers=headers)
        ).status_code == 201
        second = await api.post(f"{PRODUCTS}/import/{preview_id}/confirm", headers=headers)
        assert second.status_code == 404
        assert (await api.get(PRODUCTS, headers=headers)).json()["total"] == 1

    async def test_an_unknown_preview_is_404(
        self, api: AsyncClient, mail: Any, redis: FakeRedis
    ) -> None:
        headers = await _owner(api, mail)
        response = await api.post(f"{PRODUCTS}/import/{uuid.uuid4()}/confirm", headers=headers)
        assert response.status_code == 404

    async def test_a_file_without_required_columns_is_rejected(
        self, api: AsyncClient, mail: Any, redis: FakeRedis
    ) -> None:
        headers = await _owner(api, mail)
        response = await api.post(
            f"{PRODUCTS}/import",
            headers=headers,
            files={"file": ("c.csv", b"nombre,categoria\nX,Y\n", "text/csv")},
        )
        assert response.status_code == 400
        assert response.json()["error"] == "missing_columns"

    async def test_an_imported_formula_is_stored_as_text(
        self, api: AsyncClient, mail: Any, redis: FakeRedis
    ) -> None:
        """End to end: the payload must not survive to the database intact."""
        headers = await _owner(api, mail)
        csv = b"nombre,precio\n=cmd|'/c calc'!A1,10.00\n"
        preview = await api.post(
            f"{PRODUCTS}/import", headers=headers, files={"file": ("c.csv", csv, "text/csv")}
        )
        await api.post(f"{PRODUCTS}/import/{preview.json()['preview_id']}/confirm", headers=headers)

        stored = (await api.get(PRODUCTS, headers=headers)).json()["items"][0]["name"]
        assert stored.startswith("'")


class TestSaleColumns:
    """Feature 004, TASK-406: products fit for sale."""

    async def test_a_new_product_defaults_to_a_plain_fixed_price_item(
        self, api: AsyncClient, mail: Any
    ) -> None:
        headers = await _owner(api, mail)
        created = await api.post(PRODUCTS, headers=headers, json={"name": "X", "price": "1.00"})

        body = created.json()
        assert body["modo_venta"] == "precio_fijo"
        assert body["es_digital"] is False
        assert body["bundle_size"] is None
        assert body["bundle_permite_libres"] is False
        assert body["destacado"] is False

    async def test_create_a_bundle_and_read_it_back(self, api: AsyncClient, mail: Any) -> None:
        headers = await _owner(api, mail)
        created = await api.post(
            PRODUCTS,
            headers=headers,
            json={
                "name": "Paquete 5",
                "price": "500.00",
                "modo_venta": "precio_fijo",
                "es_digital": True,
                "bundle_size": 5,
                "bundle_permite_libres": True,
                "destacado": True,
            },
        )
        assert created.status_code == 201

        fetched = (await api.get(f"{PRODUCTS}/{created.json()['id']}", headers=headers)).json()
        assert (fetched["es_digital"], fetched["bundle_size"], fetched["destacado"]) == (
            True,
            5,
            True,
        )
        assert fetched["bundle_permite_libres"] is True

    @pytest.mark.parametrize(
        "extra",
        [{"modo_venta": "regalo"}, {"bundle_size": 1}, {"bundle_size": 51}],
    )
    async def test_invalid_sale_values_are_rejected(
        self, api: AsyncClient, mail: Any, extra: dict[str, Any]
    ) -> None:
        headers = await _owner(api, mail)
        response = await api.post(
            PRODUCTS, headers=headers, json={"name": "X", "price": "1.00", **extra}
        )

        assert response.status_code == 400

    async def test_an_update_changes_only_what_it_sends(self, api: AsyncClient, mail: Any) -> None:
        headers = await _owner(api, mail)
        created = await api.post(
            PRODUCTS,
            headers=headers,
            json={"name": "Hosting", "price": "99.00", "destacado": True, "bundle_size": 3},
        )
        product_id = created.json()["id"]

        updated = await api.put(
            f"{PRODUCTS}/{product_id}", headers=headers, json={"modo_venta": "cotizacion"}
        )

        body = updated.json()
        assert body["modo_venta"] == "cotizacion"
        assert body["destacado"] is True
        assert body["bundle_size"] == 3

    async def test_a_package_can_become_a_single_product_again(
        self, api: AsyncClient, mail: Any
    ) -> None:
        headers = await _owner(api, mail)
        created = await api.post(
            PRODUCTS, headers=headers, json={"name": "P", "price": "1.00", "bundle_size": 4}
        )

        updated = await api.put(
            f"{PRODUCTS}/{created.json()['id']}", headers=headers, json={"bundle_size": None}
        )

        assert updated.json()["bundle_size"] is None

    async def test_a_null_for_a_required_column_is_rejected_not_a_server_error(
        self, api: AsyncClient, mail: Any
    ) -> None:
        headers = await _owner(api, mail)
        created = await api.post(PRODUCTS, headers=headers, json={"name": "P", "price": "1.00"})

        response = await api.put(
            f"{PRODUCTS}/{created.json()['id']}", headers=headers, json={"modo_venta": None}
        )

        assert response.status_code == 400

    async def test_the_import_accepts_the_new_columns(
        self, api: AsyncClient, mail: Any, redis: FakeRedis
    ) -> None:
        headers = await _owner(api, mail)
        csv = (
            b"nombre,precio,modo_venta,es_digital,paquete,libres,destacado\n"
            b"Paquete 5,500.00,precio_fijo,si,5,si,si\n"
            b"Hosting,0,cotizacion,,,,\n"
        )
        preview = await api.post(
            f"{PRODUCTS}/import", headers=headers, files={"file": ("c.csv", csv, "text/csv")}
        )
        assert preview.json()["valid_rows"] == 2
        await api.post(f"{PRODUCTS}/import/{preview.json()['preview_id']}/confirm", headers=headers)

        items = {
            item["name"]: item
            for item in (await api.get(PRODUCTS, headers=headers)).json()["items"]
        }
        assert items["Paquete 5"]["bundle_size"] == 5
        assert items["Paquete 5"]["es_digital"] is True
        assert items["Paquete 5"]["bundle_permite_libres"] is True
        assert items["Paquete 5"]["destacado"] is True
        assert items["Hosting"]["modo_venta"] == "cotizacion"
        assert items["Hosting"]["bundle_size"] is None

    async def test_the_import_still_works_without_them(
        self, api: AsyncClient, mail: Any, redis: FakeRedis
    ) -> None:
        headers = await _owner(api, mail)
        preview = await api.post(
            f"{PRODUCTS}/import",
            headers=headers,
            files={"file": ("c.csv", b"nombre,precio\nX,1.00\n", "text/csv")},
        )
        await api.post(f"{PRODUCTS}/import/{preview.json()['preview_id']}/confirm", headers=headers)

        item = (await api.get(PRODUCTS, headers=headers)).json()["items"][0]
        assert item["modo_venta"] == "precio_fijo"
        assert item["destacado"] is False


class TestProductsAndServices:
    """Feature 020: what an item is, «solo informar», and the category a package chooses from."""

    async def test_a_new_item_is_a_product_unless_told(self, api: AsyncClient, mail: Any) -> None:
        headers = await _owner(api, mail)
        created = await api.post(PRODUCTS, headers=headers, json={"name": "X", "price": "1.00"})

        assert created.json()["tipo"] == "producto"
        assert created.json()["bundle_categoria"] is None

    async def test_a_service_that_only_informs(self, api: AsyncClient, mail: Any) -> None:
        headers = await _owner(api, mail)
        created = await api.post(
            PRODUCTS,
            headers=headers,
            json={
                "name": "Programa a medida",
                "price": "0",
                "tipo": "servicio",
                "modo_venta": "informar",
            },
        )

        assert created.status_code == 201
        body = (await api.get(f"{PRODUCTS}/{created.json()['id']}", headers=headers)).json()
        assert (body["tipo"], body["modo_venta"]) == ("servicio", "informar")

    async def test_a_package_that_chooses_from_a_category(
        self, api: AsyncClient, mail: Any
    ) -> None:
        headers = await _owner(api, mail)
        created = await api.post(
            PRODUCTS,
            headers=headers,
            json={
                "name": "Kit de 3",
                "price": "80",
                "bundle_size": 3,
                "bundle_categoria": " Herramientas ",
            },
        )

        assert created.json()["bundle_categoria"] == "Herramientas"

    async def test_a_category_to_choose_from_needs_a_package(
        self, api: AsyncClient, mail: Any
    ) -> None:
        headers = await _owner(api, mail)
        refused = await api.post(
            PRODUCTS,
            headers=headers,
            json={"name": "X", "price": "1", "bundle_categoria": "Herramientas"},
        )
        created = await api.post(PRODUCTS, headers=headers, json={"name": "Y", "price": "1"})
        patched = await api.put(
            f"{PRODUCTS}/{created.json()['id']}",
            headers=headers,
            json={"bundle_categoria": "Herramientas"},
        )

        assert refused.status_code == 400
        assert patched.status_code == 400

    async def test_a_package_turned_single_forgets_its_category(
        self, api: AsyncClient, mail: Any
    ) -> None:
        headers = await _owner(api, mail)
        created = await api.post(
            PRODUCTS,
            headers=headers,
            json={"name": "Kit", "price": "80", "bundle_size": 3, "bundle_categoria": "Herr"},
        )

        updated = await api.put(
            f"{PRODUCTS}/{created.json()['id']}", headers=headers, json={"bundle_size": None}
        )

        assert updated.status_code == 200
        assert (updated.json()["bundle_size"], updated.json()["bundle_categoria"]) == (None, None)

    async def test_the_import_reads_the_type_and_the_package_category(
        self, api: AsyncClient, mail: Any, redis: FakeRedis
    ) -> None:
        headers = await _owner(api, mail)
        csv = (
            "nombre,precio,tipo,modo,paquete,elegir de\n"
            "Kit de 3,80,producto,,3,Herramientas\n"
            "Asesoría,0,servicio,solo informar,,\n"
            "Raro,1,objeto,,,\n"
            "Suelto,1,,,,Herramientas\n"
        ).encode()
        preview = await api.post(
            f"{PRODUCTS}/import", headers=headers, files={"file": ("c.csv", csv, "text/csv")}
        )
        body = preview.json()
        assert body["valid_rows"] == 2
        reasons = {row["reason"] for row in body["invalid_rows"]}
        assert reasons == {"tipo_invalido", "categoria_de_paquete_sin_paquete"}
        await api.post(f"{PRODUCTS}/import/{body['preview_id']}/confirm", headers=headers)

        items = {
            item["name"]: item
            for item in (await api.get(PRODUCTS, headers=headers)).json()["items"]
        }
        assert items["Kit de 3"]["bundle_categoria"] == "Herramientas"
        assert (items["Asesoría"]["tipo"], items["Asesoría"]["modo_venta"]) == (
            "servicio",
            "informar",
        )

    async def test_a_duplicate_keeps_what_the_item_is(self, api: AsyncClient, mail: Any) -> None:
        headers = await _owner(api, mail)
        created = await api.post(
            PRODUCTS,
            headers=headers,
            json={
                "name": "Kit",
                "price": "80",
                "tipo": "producto",
                "bundle_size": 3,
                "bundle_categoria": "Herramientas",
            },
        )

        copy = await api.post(f"{PRODUCTS}/{created.json()['id']}/duplicate", headers=headers)

        assert copy.json()["bundle_categoria"] == "Herramientas"
