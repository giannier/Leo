"""The words of a system message and the small presenters of the orders list (TASK-430)."""

from __future__ import annotations

import pytest

from app.api.v1.orders import mask_phone
from app.process.templates.base import DEFAULT_SYSTEM_MESSAGES
from app.services.order_notices import render_system_message

NAME = "Ana"


class TestRenderingASystemMessage:
    def test_the_default_confirmation_with_everything(self) -> None:
        text = render_system_message(
            DEFAULT_SYSTEM_MESSAGES["pago_confirmado"],
            {"nombre_cliente": NAME, "numero": "42", "siguiente_paso": "Te escribimos hoy"},
        )

        assert text == "¡Ana, tu pago fue confirmado! ✅ Pedido #42. Te escribimos hoy"

    def test_without_the_next_step_there_is_no_trailing_space(self) -> None:
        text = render_system_message(
            DEFAULT_SYSTEM_MESSAGES["pago_confirmado"],
            {"nombre_cliente": NAME, "numero": "42", "siguiente_paso": None},
        )

        assert text == "¡Ana, tu pago fue confirmado! ✅ Pedido #42."

    def test_without_a_name_the_greeting_loses_its_comma_and_keeps_a_capital(self) -> None:
        text = render_system_message(
            DEFAULT_SYSTEM_MESSAGES["pago_confirmado"],
            {"nombre_cliente": None, "numero": "7", "siguiente_paso": None},
        )

        assert text == "¡Tu pago fue confirmado! ✅ Pedido #7."

    def test_the_rejection_carries_the_reason(self) -> None:
        text = render_system_message(
            DEFAULT_SYSTEM_MESSAGES["pago_rechazado"],
            {"nombre_cliente": None, "motivo": "el monto no coincide"},
        )

        assert text == (
            "Revisamos tu comprobante y hay un detalle: el monto no coincide. "
            "¿Nos ayudas a corregirlo?"
        )

    @pytest.mark.parametrize(
        ("template", "expected"),
        [
            ("Hola {nombre_cliente}, gracias", "Hola Ana, gracias"),
            ("Gracias, {nombre_cliente}.", "Gracias, Ana."),
            ("Listo {algo_que_no_existe} ya", "Listo ya"),
            ("Sin variables", "Sin variables"),
        ],
    )
    def test_owner_wording_is_filled_and_unknown_placeholders_never_reach_the_customer(
        self, template: str, expected: str
    ) -> None:
        assert render_system_message(template, {"nombre_cliente": NAME}) == expected


class TestPresenters:
    def test_a_peruvian_mobile_keeps_the_prefix_and_the_last_digits(self) -> None:
        assert mask_phone("+51987654628") == "+51 9•• ••• 628"

    def test_any_other_number_shows_only_its_last_three_digits(self) -> None:
        masked = mask_phone("+14155550123")

        assert masked == "+••••••••123"

    @pytest.mark.parametrize("phone", [None, ""])
    def test_no_phone_is_no_text(self, phone: str | None) -> None:
        assert mask_phone(phone) is None
