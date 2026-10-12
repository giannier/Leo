"""`process_options` accepts what a business would configure and refuses what
the WhatsApp buttons or the engine could not honour (Feature 004, TASK-404)."""

from __future__ import annotations

from typing import Any

import pytest
from pydantic import ValidationError

from app.schemas.process_options import REQUIRED_DATA_CATALOG, ProcessOptions

CAPACITA: dict[str, Any] = {
    "calificacion": {
        "activa": True,
        "pregunta": "¿Quieres solo certificar la experiencia o llevar un programa?",
        "caminos": [
            {
                "clave": "certificacion",
                "titulo": "Solo certificar",
                "tipo": "venta",
                "descripcion": "Ya tiene la experiencia",
            },
            {"clave": "programa", "titulo": "Programa completo", "tipo": "asesoria"},
        ],
    },
    "modalidad_pago": "previo",
    "datos_requeridos": ["dni", "nombres", "apellidos", "correo", "telefono"],
    "link_pago_tarjeta": "https://tienda.capacita.pe",
    "variables": {"temas_destacados": "inglés o PMBOK"},
}


def _with_paths(paths: list[dict[str, Any]], **extra: Any) -> dict[str, Any]:
    return {"calificacion": {"activa": True, "pregunta": "¿Cuál?", "caminos": paths, **extra}}


def _path(key: str, **overrides: Any) -> dict[str, Any]:
    return {"clave": key, "titulo": key.upper(), "tipo": "venta", **overrides}


def test_the_documented_example_is_valid() -> None:
    parsed = ProcessOptions.model_validate(CAPACITA)

    assert parsed.modalidad_pago == "previo"
    assert parsed.calificacion is not None
    assert [camino.clave for camino in parsed.calificacion.caminos] == [
        "certificacion",
        "programa",
    ]


def test_empty_options_are_valid() -> None:
    parsed = ProcessOptions.model_validate({})

    assert parsed.calificacion is None
    assert parsed.variables == {}


def test_the_catalog_is_the_eleven_documented_fields() -> None:
    assert len(REQUIRED_DATA_CATALOG) == 11
    assert "dni" in REQUIRED_DATA_CATALOG
    assert "zona_envio" not in REQUIRED_DATA_CATALOG


class TestQualificationPaths:
    def test_three_paths_fit_the_three_whatsapp_buttons(self) -> None:
        ProcessOptions.model_validate(_with_paths([_path("uno"), _path("dos"), _path("tres")]))

    def test_a_fourth_path_is_refused(self) -> None:
        paths = [_path("uno"), _path("dos"), _path("tres"), _path("cuatro")]

        with pytest.raises(ValidationError):
            ProcessOptions.model_validate(_with_paths(paths))

    def test_a_title_of_twenty_characters_is_accepted_and_twenty_one_is_not(self) -> None:
        ProcessOptions.model_validate(_with_paths([_path("uno", titulo="x" * 20), _path("dos")]))

        with pytest.raises(ValidationError):
            ProcessOptions.model_validate(
                _with_paths([_path("uno", titulo="x" * 21), _path("dos")])
            )

    def test_repeated_keys_are_refused(self) -> None:
        with pytest.raises(ValidationError, match="unique"):
            ProcessOptions.model_validate(_with_paths([_path("uno"), _path("uno")]))

    @pytest.mark.parametrize("key", ["A", "con espacio", "x", "ñandú", "a" * 41])
    def test_a_malformed_key_is_refused(self, key: str) -> None:
        with pytest.raises(ValidationError):
            ProcessOptions.model_validate(_with_paths([_path(key), _path("dos")]))

    def test_an_unknown_kind_is_refused(self) -> None:
        with pytest.raises(ValidationError):
            ProcessOptions.model_validate(_with_paths([_path("uno", tipo="regalo"), _path("dos")]))

    def test_an_active_qualification_needs_two_paths(self) -> None:
        with pytest.raises(ValidationError, match="at least two"):
            ProcessOptions.model_validate(_with_paths([_path("uno")]))

    def test_an_active_qualification_needs_a_question(self) -> None:
        with pytest.raises(ValidationError, match="question"):
            ProcessOptions.model_validate(
                {"calificacion": {"activa": True, "caminos": [_path("uno"), _path("dos")]}}
            )

    def test_an_inactive_qualification_may_be_left_unfinished(self) -> None:
        ProcessOptions.model_validate({"calificacion": {"activa": False}})


class TestRequiredData:
    def test_a_field_outside_the_catalog_is_refused(self) -> None:
        with pytest.raises(ValidationError):
            ProcessOptions.model_validate({"datos_requeridos": ["dni", "zona_envio"]})

    def test_a_repeated_field_is_refused(self) -> None:
        with pytest.raises(ValidationError, match="repeat"):
            ProcessOptions.model_validate({"datos_requeridos": ["dni", "dni"]})

    def test_every_catalog_field_is_accepted(self) -> None:
        parsed = ProcessOptions.model_validate({"datos_requeridos": list(REQUIRED_DATA_CATALOG)})

        assert parsed.datos_requeridos == list(REQUIRED_DATA_CATALOG)


class TestOtherOptions:
    def test_an_unknown_payment_mode_is_refused(self) -> None:
        with pytest.raises(ValidationError):
            ProcessOptions.model_validate({"modalidad_pago": "fiado"})

    def test_the_card_link_must_be_a_url(self) -> None:
        with pytest.raises(ValidationError):
            ProcessOptions.model_validate({"link_pago_tarjeta": "no es un enlace"})

    def test_ten_variables_are_accepted_and_eleven_are_not(self) -> None:
        ProcessOptions.model_validate({"variables": {f"v{i}": "x" for i in range(10)}})

        with pytest.raises(ValidationError):
            ProcessOptions.model_validate({"variables": {f"v{i}": "x" for i in range(11)}})

    def test_a_variable_name_must_be_a_plain_identifier(self) -> None:
        with pytest.raises(ValidationError):
            ProcessOptions.model_validate({"variables": {"con espacio": "x"}})

    def test_a_misspelt_option_is_refused_instead_of_ignored(self) -> None:
        with pytest.raises(ValidationError):
            ProcessOptions.model_validate({"modalidad_pagos": "previo"})
