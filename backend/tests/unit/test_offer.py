"""How each item closes (feature 020): one source, read from `modo_venta` and `agendable`."""

from __future__ import annotations

from dataclasses import dataclass

import pytest

from app.process.offer import closing_of, closings_of


@dataclass
class Item:
    modo_venta: str = "precio_fijo"
    agendable: bool = False


@pytest.mark.parametrize(
    ("item", "closing"),
    [
        (Item(), "pedido"),
        (Item(modo_venta="cotizacion"), "cotizacion"),
        (Item(agendable=True), "cita"),
        # A bookable item closes with an appointment whatever its price.
        (Item(modo_venta="cotizacion", agendable=True), "cita"),
        (Item(modo_venta="informar"), "informar"),
        # Nothing to book either: a person puts it together.
        (Item(modo_venta="informar", agendable=True), "informar"),
    ],
)
def test_each_item_says_how_it_closes(item: Item, closing: str) -> None:
    assert closing_of(item) == closing


def test_a_catalogue_lists_the_closings_it_uses_once_and_in_order() -> None:
    items = [Item(modo_venta="cotizacion"), Item(), Item(), Item(agendable=True)]

    assert closings_of(items) == ("pedido", "cotizacion", "cita")


def test_an_empty_catalogue_uses_none() -> None:
    assert closings_of([]) == ()


class TestWhatTheAgentReadsOfAnItem:
    """Feature 020: what is new shows only when set; the three templates read the same as before."""

    @staticmethod
    def _line(**values: object) -> str:
        from decimal import Decimal

        from app.agent.tools.catalog import _product_data, _product_line
        from app.models.business import Product

        base: dict[str, object] = {
            "name": "Ítem",
            "price": Decimal("80"),
            "modo_venta": "precio_fijo",
            "periodo": "unico",
            "es_digital": False,
            "destacado": False,
            "tags": [],
            "datos_a_pedir": [],
        }
        return _product_line(_product_data(Product(**{**base, **values})))

    def test_a_package_that_chooses_from_a_category_says_which(self) -> None:
        line = self._line(name="Kit de 3", bundle_size=3, bundle_categoria="Herramientas")

        assert "[paquete de 3 selecciones]" in line
        assert "[se eligen de la categoría Herramientas]" in line

    def test_a_package_without_a_category_reads_as_before(self) -> None:
        line = self._line(name="Paquete 5", bundle_size=5)

        assert "[paquete de 5 selecciones]" in line and "categoría Herramientas" not in line
        assert "se eligen de" not in line

    def test_an_item_a_person_puts_together_goes_to_an_advisor(self) -> None:
        line = self._line(name="Programa a medida", modo_venta="informar")

        assert 'derivar_a_humano(motivo="asesoria")' in line and "no va al pedido" in line

    def test_a_fixed_price_product_reads_as_before(self) -> None:
        line = self._line(name="Polo", tipo="producto")

        assert "servicio" not in line and "asesor" not in line
