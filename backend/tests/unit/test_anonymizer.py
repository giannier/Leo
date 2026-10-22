"""The rules that take personal data out of a text before it is kept (017, RF-016)."""

from __future__ import annotations

import pytest

from app.learning.anonymizer import redact, still_has_data


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Mi correo es ana.perez+tienda@gmail.com gracias", "Mi correo es [correo] gracias"),
        ("escríbeme al 987 654 321", "escríbeme al [teléfono]"),
        ("mi cel 987-654-321 o +51 912 345 678", "mi cel [teléfono] o [teléfono]"),
        ("llámame al 51987654321", "llámame al [teléfono]"),
        ("mi dni es 45678912", "mi dni es [documento]"),
        ("RUC 20600695771 de la empresa", "RUC [ruc] de la empresa"),
        ("mi tarjeta 4111 1111 1111 1111", "mi tarjeta [número]"),
        ("cuenta 191-1234567-0-12", "cuenta [número]"),
        ("mira https://www.instagram.com/mitienda/ y www.tienda.pe", "mira [enlace] y [enlace]"),
        ("vivo en Av. Larco 123, Miraflores", "vivo en [dirección], Miraflores"),
        ("entrega en Jirón Los Pinos 450 por favor", "entrega en [dirección] por favor"),
        ("Mz B Lt 4 en la urbanización", "[dirección] en la urbanización"),
        ("Me llamo Ana Pérez Soto y quiero un polo", "Me llamo [nombre] y quiero un polo"),
        ("hola, soy Carlos", "hola, soy [nombre]"),
    ],
)
def test_what_has_a_shape_is_taken_out(text: str, expected: str) -> None:
    assert redact(text) == expected


@pytest.mark.parametrize(
    "text",
    [
        "El polo cuesta S/ 35.00 y la gorra 25",
        "Tienes 2 unidades y un pedido de 150 soles",
        "Entregamos en 48 horas hábiles",
        "El pedido #1234 está en camino",
        "Somos la mejor opción para ti",
        "Soy un asistente virtual de la tienda",
    ],
)
def test_prices_amounts_and_ordinary_words_are_left_alone(text: str) -> None:
    assert redact(text) == text


def test_a_second_pass_finds_nothing_more() -> None:
    once = redact("Soy Ana Pérez, mi cel 987654321, DNI 45678912, Av. Larco 123, ana@mail.com")

    assert not still_has_data(once)
    assert "Ana" not in once and "987654321" not in once and "45678912" not in once
