"""A registered DNI or RUC does not go back to the model (Milestone B, decision 18)."""

from __future__ import annotations

import pytest

from app.agent.privacy import redact_registered


@pytest.mark.parametrize(
    "typed",
    ["Mi DNI es 12345678", "dni: 12 345 678", "12-345-678 es mi dni", "DNI 12.345.678."],
)
def test_the_dni_is_replaced_however_it_was_typed(typed: str) -> None:
    assert "[DNI registrado]" in redact_registered(typed, {"dni": "12345678"})
    assert "345" not in redact_registered(typed, {"dni": "12345678"})


def test_the_ruc_is_replaced_too() -> None:
    text = redact_registered("RUC 20123456789, factura por favor", {"ruc": "20123456789"})

    assert text == "RUC [RUC registrado], factura por favor"


def test_other_numbers_are_left_alone() -> None:
    """A longer number that contains the DNI is not the DNI."""
    text = "Pedido 123456789 y teléfono 987654321"

    assert redact_registered(text, {"dni": "12345678"}) == text


def test_nothing_registered_changes_nothing() -> None:
    assert redact_registered("Mi DNI es 12345678", {}) == "Mi DNI es 12345678"
