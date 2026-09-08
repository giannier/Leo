"""The golden set's own assertions, checked without a model.

An eval check only earns its place if it fails on the answer it was written
for and stays quiet on the answers it was not. Proving that against a live
provider costs money and can go either way per run, so the checks are pinned
here instead: synthetic replies, no network, and part of `make test`.

The one under the microscope is the product-name check. It exists because the
catalogue said "Leche" and the agent offered "Leche pausterizada" - a name
that is *almost* right, which is a product the business does not sell.

That check no longer owns its detector. The detector is production code in
`app/agent/catalog_guard.py`, because `AgentCore` runs it over every reply
before sending it; the golden set only supplies the demo fixture's names. So
these tests pin the *check*, and `tests/unit/test_catalog_guard.py` pins the
detector. Neither can drift from the other, which is the point: a private copy
here would let the eval certify something that is not what guards customers.
"""

from __future__ import annotations

import pytest
from evals.dataset import DEMO_PRODUCTS, DemoProduct, _check_names_products_verbatim

from app.agent.core import AgentReply


def _reply(content: str) -> AgentReply:
    return AgentReply(content=content, model="test-model")


class TestNamesFromTheCatalogue:
    @pytest.mark.parametrize("product", DEMO_PRODUCTS, ids=lambda p: p.name)
    def test_every_catalogue_name_passes_verbatim(self, product: DemoProduct) -> None:
        """Whatever the fixture holds must be sayable. A check that fails on
        the catalogue itself would fail every run."""
        answer = f"Te recomiendo el {product.name}, muy rico."
        assert _check_names_products_verbatim(_reply(answer)) is None

    def test_a_shortened_name_is_somebody_being_brief_not_inventing(self) -> None:
        answer = "La Inca Kola esta S/ 5.50, es la mas pedida."
        assert _check_names_products_verbatim(_reply(answer)) is None

    def test_a_name_described_in_prose_is_not_a_rename(self) -> None:
        """The catalogue's own description, relayed as a sentence."""
        answer = "El Lomo saltado viene con papas fritas y arroz, cuesta S/ 32.00."
        assert _check_names_products_verbatim(_reply(answer)) is None


class TestNamesTheCatalogueDoesNotHave:
    def test_the_variant_that_actually_reached_a_customer(self) -> None:
        """The reported failure, transposed onto this fixture: the catalogue
        word is there and a descriptor nobody wrote is bolted onto it."""
        answer = "Si, tenemos Inca Kola helada a S/ 5.50."

        detail = _check_names_products_verbatim(_reply(answer))

        assert detail is not None
        assert "inca kola helada" in detail

    def test_a_descriptor_on_a_dish_is_caught_too(self) -> None:
        answer = "El Ceviche mixto especial sale S/ 38.50."

        detail = _check_names_products_verbatim(_reply(answer))

        assert detail is not None
        assert "ceviche mixto especial" in detail

    def test_a_product_that_is_not_in_the_catalogue_at_all_is_caught(self) -> None:
        """The expensive one: a customer arrives at a criollo restaurant
        expecting the sushi the agent offered them."""
        answer = "Claro, tenemos Sushi variado a S/ 20.00."

        detail = _check_names_products_verbatim(_reply(answer))

        assert detail is not None
        assert "sushi variado" in detail

    @pytest.mark.parametrize(
        ("answer", "expected"),
        [
            ("Si, tenemos Inca Kola fresca a S/ 5.50.", "inca kola fresca"),
            ("Si, tenemos Inca Kola entera a S/ 5.50.", "inca kola entera"),
            ("Si, tenemos Inca Kola light a S/ 5.50.", "inca kola light"),
            ("si, tenemos inca kola helada a S/ 5.50.", "inca kola helada"),
            ("si, tenemos inca kola pausterizada a S/ 5.50.", "inca kola pausterizada"),
        ],
    )
    def test_the_shapes_the_old_ending_whitelist_let_through(
        self, answer: str, expected: str
    ) -> None:
        """Measured before the detector moved into `app/agent/`: "helada" and
        "pausterizada" were caught because they end in `-ada`, while "fresca",
        "entera" and "light" walked straight past. Same bug, different suffix.

        `tests/unit/test_catalog_guard.py` holds the full table. These are
        here because the golden set is what a prompt change is judged by, and
        this is the check that judges it."""
        detail = _check_names_products_verbatim(_reply(answer))

        assert detail is not None
        assert expected in detail


class TestRepliesThatNameNoProduct:
    @pytest.mark.parametrize(
        "answer",
        [
            "¡Hola! ¿En que te puedo ayudar hoy?",
            "Atendemos de lunes a sabado de 12:00 a 22:00. Los domingos cerramos.",
            "Hacemos delivery solo dentro de Miraflores, cuesta S/ 6.00 y demora 40 minutos.",
            "Lamento mucho lo ocurrido. Te comunico ahora con una persona del equipo.",
            "No manejamos ese producto, disculpa. ¿Te muestro lo que si tenemos?",
            "Soy Leo, el asistente del Restaurante Demo. Dime que se te antoja.",
        ],
    )
    def test_ordinary_spanish_prose_does_not_trip_the_check(self, answer: str) -> None:
        """A false alarm is worse than no check: it teaches everybody to
        re-run the evals until they pass."""
        assert _check_names_products_verbatim(_reply(answer)) is None

    def test_an_enumeration_is_read_as_separate_names(self) -> None:
        """A comma ends a name. Without that, a list reads as one long
        invented product."""
        answer = "Tenemos Lomo saltado, Ceviche mixto y Inca Kola 500ml."
        assert _check_names_products_verbatim(_reply(answer)) is None
