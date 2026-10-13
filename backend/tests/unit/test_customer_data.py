"""Field-by-field validation of the customer's data (Feature 004, TASK-427)."""

from __future__ import annotations

from datetime import date

import pytest

from app.orders.customer_data import (
    ORDER_FIELDS,
    PERSONAL_FIELDS,
    InvalidField,
    _birth_date,
    validate_field,
)


class TestDocuments:
    @pytest.mark.parametrize(
        ("raw", "stored"),
        [("12345678", "12345678"), (" 1234 5678 ", "12345678"), ("12-345-678", "12345678")],
    )
    def test_a_dni_has_eight_digits(self, raw: str, stored: str) -> None:
        assert validate_field("dni", raw) == stored

    @pytest.mark.parametrize("raw", ["1234567", "123456789", "abcdefgh", "1234567a"])
    def test_a_dni_that_does_not_have_eight_digits_is_invalid(self, raw: str) -> None:
        with pytest.raises(InvalidField) as raised:
            validate_field("dni", raw)

        assert "8 dígitos" in str(raised.value)

    def test_a_ruc_has_eleven_digits(self) -> None:
        assert validate_field("ruc", "20123456789") == "20123456789"
        assert validate_field("ruc", "20 123 456 789") == "20123456789"
        for bad in ("2012345678", "201234567890"):
            with pytest.raises(InvalidField):
                validate_field("ruc", bad)

    def test_the_reason_never_echoes_the_value(self) -> None:
        with pytest.raises(InvalidField) as raised:
            validate_field("dni", "99999")

        assert "99999" not in str(raised.value)


class TestEmail:
    def test_a_valid_address_is_lower_cased(self) -> None:
        assert validate_field("correo", "  Ana.Perez@Mail.COM ") == "ana.perez@mail.com"

    @pytest.mark.parametrize(
        "raw",
        [
            "sin-arroba",
            "a@b",
            "a@@b.com",
            "a b@c.com",
            "@mail.com",
            "ana@mail.",
            "x" * 130 + "@m.com",
        ],
    )
    def test_a_malformed_address_is_invalid(self, raw: str) -> None:
        with pytest.raises(InvalidField):
            validate_field("correo", raw)


class TestPhone:
    @pytest.mark.parametrize(
        ("raw", "stored"),
        [
            ("+51987654321", "+51987654321"),
            ("+51 987 654 321", "+51987654321"),
            ("987654321", "+51987654321"),
            ("987-654-321", "+51987654321"),
            ("0051987654321", "+51987654321"),
            ("+1 (415) 555-2671", "+14155552671"),
        ],
    )
    def test_the_phone_is_stored_in_e164(self, raw: str, stored: str) -> None:
        assert validate_field("telefono", raw) == stored

    @pytest.mark.parametrize("raw", ["12345", "+0123456789", "telefono", "87654321", "+51"])
    def test_a_phone_that_is_not_e164_is_invalid(self, raw: str) -> None:
        with pytest.raises(InvalidField):
            validate_field("telefono", raw)


class TestBirthDate:
    @pytest.mark.parametrize("raw", ["1990-05-17", "17/05/1990", "17-05-1990"])
    def test_the_usual_formats_are_stored_as_iso(self, raw: str) -> None:
        assert validate_field("fecha_nacimiento", raw) == "1990-05-17"

    @pytest.mark.parametrize("raw", ["ayer", "31/02/1990", "2999-01-01", "1800-01-01"])
    def test_an_impossible_date_is_invalid(self, raw: str) -> None:
        with pytest.raises(InvalidField):
            validate_field("fecha_nacimiento", raw)

    def test_today_is_the_upper_bound(self) -> None:
        today = date(2026, 10, 12)
        assert _birth_date("2026-10-12", today=today) == "2026-10-12"
        with pytest.raises(InvalidField):
            _birth_date("2026-10-13", today=today)


class TestFreeText:
    def test_text_is_put_on_one_line(self) -> None:
        assert validate_field("nombres", "  Ana   María\n") == "Ana María"

    @pytest.mark.parametrize(
        ("field", "raw"),
        [
            ("nombres", "A"),
            ("nombres", "x" * 101),
            ("direccion", "Jr."),
            ("direccion", "12345 678"),
            ("empresa", "1234"),
        ],
    )
    def test_text_out_of_bounds_or_without_letters_is_invalid(self, field: str, raw: str) -> None:
        with pytest.raises(InvalidField):
            validate_field(field, raw)

    def test_an_address_may_hold_digits_next_to_letters(self) -> None:
        assert validate_field("direccion", "Jr. Amazonas 123, Bagua") == "Jr. Amazonas 123, Bagua"


class TestWhatIsNotAField:
    @pytest.mark.parametrize("raw", [None, "", "   ", True, ["a"], {"a": 1}])
    def test_an_empty_or_non_text_value_is_invalid(self, raw: object) -> None:
        with pytest.raises(InvalidField):
            validate_field("dni", raw)

    def test_a_number_sent_as_json_is_read_as_text(self) -> None:
        assert validate_field("dni", 12345678) == "12345678"
        assert validate_field("telefono", 987654321) == "+51987654321"

    def test_an_unknown_field_is_not_asked(self) -> None:
        with pytest.raises(InvalidField):
            validate_field("color_favorito", "azul")

    def test_the_order_fields_are_not_personal(self) -> None:
        assert not ORDER_FIELDS & PERSONAL_FIELDS
