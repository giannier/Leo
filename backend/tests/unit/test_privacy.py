"""Masking of personal data before it reaches a trace (Feature 004, TASK-427, C-20)."""

from __future__ import annotations

import pytest

from app.agent.core import _summarise
from app.agent.privacy import MASK, mask_arguments, mask_value


class TestMaskValue:
    @pytest.mark.parametrize(
        ("field", "raw", "masked"),
        [
            ("dni", "12345678", "****5678"),
            ("ruc", "20123456789", "****6789"),
            ("telefono", "+51987654321", "****4321"),
            ("telefono", "987 654 321", "****4321"),
            ("dni", "123", "****"),
            ("correo", "ana.perez@mail.com", "a***@mail.com"),
            ("direccion", "Jr. Amazonas 123", MASK),
            ("nombres", "Ana María", MASK),
            ("apellidos", "Quispe", MASK),
            ("referencia", "frente al parque", MASK),
            ("fecha_nacimiento", "1990-05-17", MASK),
            ("empresa", "Capacita SAC", MASK),
            ("distrito", "Bagua", MASK),
        ],
    )
    def test_personal_fields_are_masked(self, field: str, raw: str, masked: str) -> None:
        assert mask_value(field, raw) == masked

    def test_an_email_without_an_at_is_masked_whole(self) -> None:
        assert mask_value("correo", "no-es-un-correo") == MASK

    def test_the_masked_form_never_contains_the_value(self) -> None:
        for field, raw in (
            ("dni", "12345678"),
            ("correo", "ana@mail.com"),
            ("direccion", "Calle 1"),
        ):
            assert raw not in str(mask_value(field, raw))

    @pytest.mark.parametrize("field", ["producto_id", "cantidad", "seleccion", "zona_envio"])
    def test_other_fields_are_left_alone(self, field: str) -> None:
        assert mask_value(field, "texto") == "texto"

    @pytest.mark.parametrize("value", [None, ""])
    def test_empty_values_stay_empty(self, value: object) -> None:
        assert mask_value("dni", value) == value


class TestMaskArguments:
    def test_the_arguments_of_a_tool_are_masked_field_by_field(self) -> None:
        masked = mask_arguments(
            {"dni": "12345678", "correo": "ana@mail.com", "zona_envio": "Jaén", "cantidad": 2}
        )

        assert masked == {
            "dni": "****5678",
            "correo": "a***@mail.com",
            "zona_envio": "Jaén",
            "cantidad": 2,
        }

    def test_nested_data_is_masked_too(self) -> None:
        masked = mask_arguments(
            {"datos": {"dni": "12345678", "extras": [{"direccion": "Calle 1"}]}}
        )

        assert masked == {"datos": {"dni": "****5678", "extras": [{"direccion": MASK}]}}

    def test_a_list_under_a_personal_name_is_masked_item_by_item(self) -> None:
        assert mask_arguments({"telefono": ["987654321", "912345678"]}) == {
            "telefono": ["****4321", "****5678"]
        }

    def test_the_original_is_not_modified(self) -> None:
        original = {"dni": "12345678"}

        mask_arguments(original)

        assert original == {"dni": "12345678"}


class TestTheTraceSummary:
    def test_the_summary_that_goes_to_the_trace_is_masked(self) -> None:
        summary = _summarise(
            {"dni": "12345678", "correo": "ana@mail.com", "direccion": "Jr. Amazonas 123"}
        )

        for secret in ("12345678", "ana@mail.com", "Amazonas"):
            assert secret not in summary
        assert "****5678" in summary

    def test_it_is_still_cut_to_the_limit_after_masking(self) -> None:
        assert len(_summarise({"consulta": "x" * 500})) == 120
