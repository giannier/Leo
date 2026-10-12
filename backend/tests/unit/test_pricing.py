"""Pricing and promotions (Feature 004, TASK-424).

The verification of the task: table-driven tests for percentage, fixed amount,
2x1, 3x2, price by quantity, minimum amount, validity, a closing promotion that
was not offered, a closing promotion that expired, and free shipping. `price()`
is pure, so every case is a value in and a value out.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace
from typing import Any

import pytest

from app.models.sales_config import Promotion
from app.orders.pricing import (
    Delivery,
    Line,
    Rule,
    Totales,
    Zone,
    applies_to_order,
    cents,
    offer_is_valid,
    price,
    saving,
    shipping,
)

NOW = datetime(2026, 10, 12, 15, 0, tzinfo=UTC)
D = Decimal

COURSE = uuid.uuid4()
OTHER = uuid.uuid4()


def line(
    price_: str = "100.00",
    quantity: int = 1,
    *,
    product: uuid.UUID | None = COURSE,
    category: str | None = "Cursos",
) -> Line:
    return Line(
        product_id=product, categoria=category, precio_unitario=D(price_), cantidad=quantity
    )


def rule(**overrides: Any) -> Rule:
    values: dict[str, Any] = {
        "id": str(uuid.uuid4()),
        "nombre": "Promo",
        "uso": "automatica",
        "tipo": "porcentaje",
        "valor": D("10"),
    }
    values.update(overrides)
    return Rule(**values)


def totals(items: list[Line], rules: list[Rule], offered: dict[str, Any] | None = None) -> Totales:
    return price(items, rules, offered or {}, NOW)


class TestWithoutPromotions:
    def test_the_subtotal_is_price_times_quantity_to_the_cent(self) -> None:
        result = totals([line("33.33", 3), line("10.00", 2)], [])

        assert result.subtotal == D("119.99")
        assert result.descuento == D("0.00")
        assert result.envio == D("0.00")
        assert result.total == D("119.99")
        assert result.promocion is None
        assert result.candidatas == ()

    def test_an_empty_order_costs_nothing(self) -> None:
        result = totals([], [rule()])

        assert result.total == D("0.00")
        assert result.promocion is None

    def test_money_is_decimal_all_the_way(self) -> None:
        result = totals([line("19.99", 7)], [rule(valor=D("12.5"))])

        for amount in (result.subtotal, result.descuento, result.envio, result.total):
            assert isinstance(amount, Decimal)

    def test_cents_round_half_up(self) -> None:
        assert cents(D("0.125")) == D("0.13")
        assert cents(D("0.124")) == D("0.12")


# (case, rule values, lines, expected discount)
KINDS = [
    ("percentage", {"tipo": "porcentaje", "valor": D("20")}, [line("500.00")], "100.00"),
    ("percentage rounds", {"tipo": "porcentaje", "valor": D("15")}, [line("33.33")], "5.00"),
    ("fixed amount", {"tipo": "monto_fijo", "valor": D("30")}, [line("100.00", 2)], "30.00"),
    (
        "fixed amount never exceeds the goods",
        {"tipo": "monto_fijo", "valor": D("80")},
        [line("50.00")],
        "50.00",
    ),
    ("2x1", {"tipo": "nxm", "n": 2, "m": 1}, [line("40.00", 2)], "40.00"),
    ("2x1 with an odd quantity", {"tipo": "nxm", "n": 2, "m": 1}, [line("40.00", 3)], "40.00"),
    ("3x2", {"tipo": "nxm", "n": 3, "m": 2}, [line("90.00", 3)], "90.00"),
    ("3x2 twice", {"tipo": "nxm", "n": 3, "m": 2}, [line("90.00", 7)], "180.00"),
    ("3x2 without a full group", {"tipo": "nxm", "n": 3, "m": 2}, [line("90.00", 2)], "0.00"),
    (
        "3x2 never mixes two products",
        {"tipo": "nxm", "n": 3, "m": 2},
        [line("90.00", 2), line("50.00", 1, product=OTHER)],
        "0.00",
    ),
    (
        "price by quantity",
        {"tipo": "precio_cantidad", "valor": D("400"), "m": 5},
        [line("100.00", 5)],
        "100.00",
    ),
    (
        "price by quantity, two groups and a rest",
        {"tipo": "precio_cantidad", "valor": D("400"), "m": 5},
        [line("100.00", 11)],
        "200.00",
    ),
    (
        "price by quantity without a full group",
        {"tipo": "precio_cantidad", "valor": D("400"), "m": 5},
        [line("100.00", 4)],
        "0.00",
    ),
    (
        "price by quantity that is not cheaper",
        {"tipo": "precio_cantidad", "valor": D("600"), "m": 5},
        [line("100.00", 5)],
        "0.00",
    ),
    (
        "minimum amount reached",
        {"tipo": "porcentaje", "valor": D("10"), "monto_minimo": D("200")},
        [line("100.00", 2)],
        "20.00",
    ),
    (
        "minimum amount not reached",
        {"tipo": "porcentaje", "valor": D("10"), "monto_minimo": D("200")},
        [line("100.00", 1)],
        "0.00",
    ),
]


@pytest.mark.parametrize(
    ("values", "items", "expected"),
    [(values, items, expected) for _, values, items, expected in KINDS],
    ids=[case[0] for case in KINDS],
)
def test_each_kind_of_promotion(values: dict[str, Any], items: list[Line], expected: str) -> None:
    result = totals(items, [rule(**values)])

    assert result.descuento == D(expected)
    assert result.total == result.subtotal - D(expected)
    assert (result.promocion is not None) is (D(expected) > 0)


class TestIncompleteRules:
    """The database CHECKs forbid these, but a rule is read, not trusted."""

    @pytest.mark.parametrize(
        "values",
        [
            {"tipo": "porcentaje", "valor": None},
            {"tipo": "monto_fijo", "valor": None},
            {"tipo": "nxm", "n": None, "m": None},
            {"tipo": "nxm", "n": 2, "m": 2},
            {"tipo": "precio_cantidad", "valor": None, "m": 5},
            {"tipo": "precio_cantidad", "valor": D("10"), "m": 1},
        ],
    )
    def test_a_rule_missing_its_numbers_is_worth_nothing(self, values: dict[str, Any]) -> None:
        assert totals([line("100.00", 10)], [rule(**values)]).descuento == 0


class TestWhichLinesAPromotionWorksOn:
    def test_a_promotion_for_some_products_leaves_the_others_alone(self) -> None:
        promo = rule(aplica_a="productos", product_ids=frozenset({COURSE}), valor=D("50"))

        result = totals([line("100.00", 1), line("200.00", 1, product=OTHER)], [promo])

        assert result.descuento == D("50.00")

    def test_a_promotion_for_a_product_the_order_lacks_is_worth_nothing(self) -> None:
        promo = rule(aplica_a="productos", product_ids=frozenset({OTHER}))

        assert totals([line()], [promo]).descuento == D("0.00")

    def test_a_line_whose_product_is_gone_matches_no_product_promotion(self) -> None:
        promo = rule(aplica_a="productos", product_ids=frozenset({COURSE}))

        assert totals([line(product=None)], [promo]).descuento == D("0.00")

    def test_a_category_promotion_ignores_case(self) -> None:
        promo = rule(aplica_a="categorias", categorias=frozenset({"cursos"}), valor=D("10"))

        result = totals(
            [line("100.00", category="CURSOS"), line("100.00", category="Otros")], [promo]
        )

        assert result.descuento == D("10.00")

    def test_a_line_without_category_matches_no_category_promotion(self) -> None:
        promo = rule(aplica_a="categorias", categorias=frozenset({"cursos"}))

        assert totals([line(category=None)], [promo]).descuento == D("0.00")

    def test_the_minimum_amount_counts_only_the_lines_in_scope(self) -> None:
        """A coupon on courses with a S/ 150 minimum asks for S/ 150 of courses."""
        promo = rule(
            aplica_a="productos",
            product_ids=frozenset({COURSE}),
            monto_minimo=D("150"),
            valor=D("10"),
        )

        assert totals([line("100.00"), line("500.00", product=OTHER)], [promo]).descuento == 0
        assert totals([line("160.00"), line("500.00", product=OTHER)], [promo]).descuento == D(
            "16.00"
        )

    def test_a_free_item_does_not_get_a_discount(self) -> None:
        assert totals([line("0.00")], [rule()]).descuento == D("0.00")


class TestNeverAccumulate:
    def test_the_promotion_that_saves_the_most_wins(self) -> None:
        small = rule(nombre="Pequeña", valor=D("5"))
        big = rule(nombre="Grande", valor=D("25"))

        result = totals([line("200.00")], [small, big])

        assert result.promocion is not None
        assert result.promocion.nombre == "Grande"
        assert result.descuento == D("50.00")
        assert [c.nombre for c in result.candidatas] == ["Grande", "Pequeña"]

    def test_the_order_of_the_input_does_not_matter(self) -> None:
        a = rule(nombre="A", valor=D("10"))
        b = rule(nombre="B", tipo="monto_fijo", valor=D("30"))

        assert (
            totals([line("200.00")], [a, b]).promocion == totals([line("200.00")], [b, a]).promocion
        )

    def test_a_tie_goes_to_the_automatic_one_then_to_the_name(self) -> None:
        closing = rule(nombre="A cupón", uso="cierre", vigencia_horas=24)
        automatic = rule(nombre="B auto")
        offered = {closing.id: NOW + timedelta(hours=1)}

        assert totals([line()], [closing, automatic], offered).promocion.nombre == "B auto"  # type: ignore[union-attr]

        first = rule(nombre="Alfa")
        second = rule(nombre="Beta")
        assert totals([line()], [second, first]).promocion.nombre == "Alfa"  # type: ignore[union-attr]

    def test_the_snapshot_is_json_safe(self) -> None:
        result = totals([line("200.00")], [rule(nombre="Diez", valor=D("10"))])

        assert result.promocion is not None
        assert result.promocion.snapshot() == {
            "nombre": "Diez",
            "tipo": "porcentaje",
            "valor": "10",
            "uso": "automatica",
            "ahorro": "20.00",
        }

    def test_the_snapshot_of_a_promotion_without_value(self) -> None:
        result = totals([line("40.00", 2)], [rule(tipo="nxm", n=2, m=1, valor=None)])

        assert result.promocion is not None
        assert result.promocion.snapshot()["valor"] is None


class TestValidity:
    def test_a_promotion_that_has_not_started_does_not_apply(self) -> None:
        promo = rule(inicia_en=NOW + timedelta(minutes=1))

        assert totals([line()], [promo]).descuento == 0

    def test_a_promotion_that_already_ended_does_not_apply(self) -> None:
        promo = rule(termina_en=NOW)

        assert totals([line()], [promo]).descuento == 0

    def test_a_promotion_inside_its_dates_applies(self) -> None:
        promo = rule(inicia_en=NOW - timedelta(days=1), termina_en=NOW + timedelta(days=1))

        assert totals([line()], [promo]).descuento == D("10.00")

    def test_a_deactivated_promotion_does_not_apply(self) -> None:
        assert totals([line()], [rule(activa=False)]).descuento == 0


class TestClosingPromotions:
    def closing(self) -> Rule:
        return rule(nombre="Cupón", uso="cierre", valor=D("20"), vigencia_horas=24)

    def test_one_that_was_not_offered_does_not_apply(self) -> None:
        assert totals([line("100.00")], [self.closing()]).descuento == 0

    def test_one_that_was_offered_applies(self) -> None:
        promo = self.closing()
        offered = {promo.id: NOW + timedelta(hours=2)}

        result = totals([line("100.00")], [promo], offered)

        assert result.descuento == D("20.00")
        assert result.promocion is not None
        assert result.promocion.uso == "cierre"

    def test_one_whose_offer_expired_does_not_apply(self) -> None:
        promo = self.closing()
        offered = {promo.id: NOW - timedelta(seconds=1)}

        assert totals([line("100.00")], [promo], offered).descuento == 0

    def test_an_offer_that_expires_right_now_is_over(self) -> None:
        promo = self.closing()

        assert totals([line()], [promo], {promo.id: NOW}).descuento == 0

    def test_the_offer_may_come_from_the_stored_json(self) -> None:
        promo = self.closing()
        offered = {promo.id: (NOW + timedelta(hours=1)).isoformat()}

        assert totals([line("100.00")], [promo], offered).descuento == D("20.00")

    def test_a_garbled_offer_counts_as_never_made(self) -> None:
        promo = self.closing()

        assert offer_is_valid({promo.id: "no es una fecha"}, promo.id, NOW) is False
        assert offer_is_valid({promo.id: 42}, promo.id, NOW) is False
        assert offer_is_valid({}, promo.id, NOW) is False

    def test_an_offer_for_another_promotion_does_not_count(self) -> None:
        promo = self.closing()
        offered = {str(uuid.uuid4()): NOW + timedelta(hours=1)}

        assert totals([line("100.00")], [promo], offered).descuento == 0

    def test_a_closing_promotion_competes_with_an_automatic_one(self) -> None:
        promo = self.closing()
        auto = rule(nombre="Auto", valor=D("30"))
        offered = {promo.id: NOW + timedelta(hours=1)}

        assert totals([line("100.00")], [promo, auto], offered).promocion.nombre == "Auto"  # type: ignore[union-attr]


class TestApplicability:
    def test_it_needs_the_dates_the_scope_the_amount_and_the_quantity(self) -> None:
        promo = rule(tipo="nxm", n=3, m=2, aplica_a="productos", product_ids=frozenset({COURSE}))

        assert applies_to_order(promo, [line("90.00", 3)], NOW) is True
        assert applies_to_order(promo, [line("90.00", 2)], NOW) is False
        assert applies_to_order(promo, [line("90.00", 3, product=OTHER)], NOW) is False
        assert applies_to_order(rule(termina_en=NOW), [line()], NOW) is False

    def test_saving_is_zero_when_it_does_not_qualify(self) -> None:
        assert saving(rule(monto_minimo=D("999")), [line()]) == 0


class TestFromTheRow:
    def test_a_row_becomes_a_rule(self) -> None:
        row = Promotion(
            id=uuid.uuid4(),
            nombre="Cupón",
            uso="cierre",
            tipo="porcentaje",
            valor=D("20"),
            aplica_a="categorias",
            categorias=["Cursos", "Talleres"],
            product_ids=[COURSE],
            monto_minimo=D("50"),
            vigencia_horas=24,
            is_active=True,
        )

        converted = Rule.from_promotion(row)

        assert converted.id == str(row.id)
        assert converted.uso == "cierre"
        assert converted.categorias == frozenset({"cursos", "talleres"})
        assert converted.product_ids == frozenset({COURSE})
        assert converted.activa is True

    def test_columns_left_to_their_database_default_read_as_the_default(self) -> None:
        """A row that was never flushed has None where the database would put its default."""
        row = SimpleNamespace(
            id=uuid.uuid4(),
            nombre="Auto",
            uso=None,
            tipo="porcentaje",
            valor=D("5"),
            n=None,
            m=None,
            aplica_a=None,
            product_ids=None,
            categorias=None,
            monto_minimo=None,
            vigencia_horas=None,
            inicia_en=None,
            termina_en=None,
            is_active=None,
        )

        converted = Rule.from_promotion(row)

        assert converted.uso == "automatica"
        assert converted.aplica_a == "todo"
        assert converted.activa is True
        assert converted.product_ids == frozenset()

    def test_a_deactivated_row_stays_deactivated(self) -> None:
        row = SimpleNamespace(
            id=uuid.uuid4(),
            nombre="x",
            uso="automatica",
            tipo="porcentaje",
            valor=D("5"),
            n=None,
            m=None,
            aplica_a="todo",
            product_ids=[],
            categorias=[],
            monto_minimo=None,
            vigencia_horas=None,
            inicia_en=None,
            termina_en=None,
            is_active=False,
        )

        assert Rule.from_promotion(row).activa is False


ZONES = (Zone("Bagua Grande", D("8.00")), Zone("Jaén", D("15.50")))


def delivery(**overrides: Any) -> Delivery:
    values: dict[str, Any] = {"tipo": "delivery", "zona": "Jaén", "zonas": ZONES}
    values.update(overrides)
    return Delivery(**values)


class TestShipping:
    def test_no_delivery_choice_costs_nothing(self) -> None:
        assert totals([line()], []).envio == 0
        assert price([line()], [], {}, NOW, entrega=Delivery()).envio == 0

    def test_pickup_costs_nothing(self) -> None:
        result = price([line()], [], {}, NOW, entrega=delivery(tipo="recojo"))

        assert result.envio == 0

    def test_a_zone_costs_what_the_business_set(self) -> None:
        result = price([line("100.00")], [], {}, NOW, entrega=delivery())

        assert result.envio == D("15.50")
        assert result.total == D("115.50")

    def test_the_zone_is_found_ignoring_case(self) -> None:
        result = price([line()], [], {}, NOW, entrega=delivery(zona="bagua grande"))

        assert result.envio == D("8.00")

    def test_shipping_is_added_after_the_discount(self) -> None:
        result = price([line("100.00")], [rule(valor=D("10"))], {}, NOW, entrega=delivery())

        assert result.total == D("90.00") + D("15.50")

    def test_free_shipping_from_an_amount(self) -> None:
        result = price([line("200.00")], [], {}, NOW, entrega=delivery(gratis_desde=D("200.00")))

        assert result.envio == 0
        assert result.envio_gratis is True
        assert result.total == D("200.00")

    def test_free_shipping_counts_the_goods_after_the_discount(self) -> None:
        result = price(
            [line("200.00")],
            [rule(valor=D("10"))],
            {},
            NOW,
            entrega=delivery(gratis_desde=D("200.00")),
        )

        assert result.envio == D("15.50")
        assert result.envio_gratis is False

    def test_below_the_threshold_shipping_is_charged(self) -> None:
        result = price([line("50.00")], [], {}, NOW, entrega=delivery(gratis_desde=D("200")))

        assert result.envio == D("15.50")

    def test_a_zone_that_costs_nothing_is_not_reported_as_waived(self) -> None:
        free_zone = delivery(zonas=(Zone("Jaén", D("0.00")),), gratis_desde=D("10"))

        result = price([line("50.00")], [], {}, NOW, entrega=free_zone)

        assert result.envio == 0
        assert result.envio_gratis is False

    def test_delivery_without_a_zone_yet_costs_nothing_and_is_not_to_coordinate(self) -> None:
        result = price([line()], [], {}, NOW, entrega=delivery(zona=None))

        assert result.envio == 0
        assert result.envio_a_coordinar is False

    def test_an_unknown_zone_costs_nothing(self) -> None:
        result = price([line()], [], {}, NOW, entrega=delivery(zona="Marte"))

        assert result.envio == 0
        assert result.envio_a_coordinar is False

    def test_a_business_without_zones_coordinates_the_price(self) -> None:
        result = price([line()], [], {}, NOW, entrega=Delivery(tipo="delivery", zonas=()))

        assert result.envio == 0
        assert result.envio_a_coordinar is True

    def test_the_function_returns_the_three_facts(self) -> None:
        assert shipping(None, D("10")) == (D("0.00"), False, False)
        assert shipping(delivery(), D("10")) == (D("15.50"), False, False)
