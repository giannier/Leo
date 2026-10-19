"""Reading the package a customer names early (the "paquete de 5 con 2 temas" case)."""

from __future__ import annotations

import pytest

from app.process.bundle_request import Bundle, requested_bundle

CAPACITA = [
    Bundle("Paquete 3 certificaciones", 3),
    Bundle("Paquete 5 certificaciones", 5),
    Bundle("Paquete 10 certificaciones", 10),
]


@pytest.mark.parametrize(
    "said",
    [
        "Quiero el paquete de 5 certificaciones",
        "quiero el PAQUETE de 5",
        "me interesa el pack de 5",
        "el paquete de cinco por favor",
        "Paquete 5 certificaciones",
        "hola, quiero el paquete 5",
    ],
)
def test_the_package_of_five(said: str) -> None:
    assert requested_bundle(said, CAPACITA) == CAPACITA[1]


@pytest.mark.parametrize(
    "said",
    [
        "Solo quiero certificar mi experiencia",
        "quiero 5",  # a number alone could be units of anything
        "cuánto cuesta el paquete?",
        "el paquete de 4",  # no package holds 4
        "dudo entre el paquete de 3 y el paquete de 5",  # two sizes: ambiguous
        "Por ahora SIAF y Excel avanzado",
    ],
)
def test_nothing_or_ambiguous_is_not_a_package(said: str) -> None:
    assert requested_bundle(said, CAPACITA) is None


def test_two_packages_of_the_same_size_are_ambiguous() -> None:
    twins = [Bundle("Paquete 5 Excel", 5), Bundle("Paquete 5 Gestión", 5)]
    assert requested_bundle("quiero el paquete de 5", twins) is None
    assert requested_bundle("quiero el paquete 5 excel", twins) == twins[0]


def test_no_packages_in_the_catalogue() -> None:
    assert requested_bundle("quiero el paquete de 5", []) is None
