"""The period a price covers (feature 019): once, per month or per year."""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from httpx import AsyncClient

from app.agent.tools.shared import price_text
from tests.integration.test_product_catalog_v2 import _product
from tests.integration.test_products import PRODUCTS, _owner, redis  # noqa: F401


class TestThePeriodInTheApi:
    async def test_it_defaults_to_once_and_can_be_set_and_changed(
        self, api: AsyncClient, mail: Any
    ) -> None:
        headers = await _owner(api, mail)

        plain = await _product(api, headers)
        yearly = await _product(api, headers, name="Hosting", periodo="ano")
        changed = await api.put(
            f"{PRODUCTS}/{plain['id']}", headers=headers, json={"periodo": "mes"}
        )
        invalid = await api.post(
            PRODUCTS, headers=headers, json={"name": "X", "price": "1", "periodo": "semana"}
        )

        assert plain["periodo"] == "unico"
        assert yearly["periodo"] == "ano"
        assert changed.json()["periodo"] == "mes"
        assert invalid.status_code == 400

    async def test_a_copy_keeps_the_period(self, api: AsyncClient, mail: Any) -> None:
        headers = await _owner(api, mail)
        product = await _product(api, headers, periodo="mes")

        copy = await api.post(f"{PRODUCTS}/{product['id']}/duplicate", headers=headers)

        assert copy.json()["periodo"] == "mes"


class TestHowAPriceIsSpoken:
    def test_a_fixed_price_with_and_without_period(self) -> None:
        assert price_text(Decimal("110"), "S/") == "S/ 110.00"
        assert price_text(Decimal("110"), "S/", periodo="ano") == "S/ 110.00 al año"
        assert price_text(Decimal("40"), "S/", periodo="mes") == "S/ 40.00 al mes"

    def test_a_reference_price_is_desde_and_no_price_is_to_quote(self) -> None:
        assert price_text(Decimal("800"), "S/", quoted=True) == "desde S/ 800.00"
        assert price_text(Decimal("0"), "S/", quoted=True) == "precio a cotizar"
