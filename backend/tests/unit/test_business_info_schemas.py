"""The five `business_info` sections accept what a business would write and
refuse what would mislead a customer (Feature 004, TASK-403)."""

from __future__ import annotations

from decimal import Decimal
from typing import Any

import pytest
from pydantic import ValidationError

from app.models.sales_config import BUSINESS_INFO_SECTIONS
from app.schemas.business_info import (
    SECTION_SCHEMAS,
    Contacto,
    Devoluciones,
    Entregas,
    Locales,
    TiposCliente,
    check_pickup_has_a_place,
    validate_section,
)

LOCAL: dict[str, Any] = {
    "nombre": "Sede Miraflores",
    "direccion": "Av. Larco 123",
    "referencia": "Frente al parque",
    "maps_url": "https://maps.example.com/abc",
    "horarios": {"mon": [["09:00", "18:00"]], "sat": [["09:00", "13:00"]]},
}


def test_every_section_has_a_schema_and_only_those() -> None:
    assert set(SECTION_SCHEMAS) == set(BUSINESS_INFO_SECTIONS)


class TestLocales:
    def test_a_complete_section(self) -> None:
        parsed = Locales.model_validate({"atiende_presencial": True, "locales": [LOCAL]})

        assert parsed.locales[0].nombre == "Sede Miraflores"
        assert str(parsed.locales[0].maps_url) == "https://maps.example.com/abc"

    def test_a_business_without_premises_is_valid(self) -> None:
        parsed = Locales.model_validate({"atiende_presencial": False})

        assert parsed.locales == []

    def test_at_most_twenty_locations(self) -> None:
        with pytest.raises(ValidationError):
            Locales.model_validate({"atiende_presencial": True, "locales": [LOCAL] * 21})
        Locales.model_validate({"atiende_presencial": True, "locales": [LOCAL] * 20})

    @pytest.mark.parametrize(
        "change",
        [
            {"nombre": ""},
            {"direccion": "   "},
            {"maps_url": "not a url"},
            {"horarios": {"monday": [["09:00", "18:00"]]}},
            {"horarios": {"mon": [["9:00", "18:00"]]}},
            {"horarios": {"mon": [["09:00", "25:00"]]}},
            {"horarios": {"mon": [["18:00", "09:00"]]}},
            {"horarios": {"mon": [["09:00"]]}},
            {"horarios": {"mon": [["09:00", "12:00", "13:00"]]}},
            {"sorpresa": 1},
        ],
    )
    def test_an_invalid_location_is_refused(self, change: dict[str, Any]) -> None:
        with pytest.raises(ValidationError):
            Locales.model_validate({"atiende_presencial": True, "locales": [{**LOCAL, **change}]})

    def test_the_hours_shape_is_the_one_business_profiles_uses(self) -> None:
        """Hours are the same `{"mon": [["09:00", "18:00"]]}` dict as
        `business_profiles.opening_hours`, so one renderer can print both."""
        parsed = Locales.model_validate({"atiende_presencial": True, "locales": [LOCAL]})

        assert parsed.locales[0].horarios == LOCAL["horarios"]


class TestEntregas:
    VALID: dict[str, Any] = {
        "hace_delivery": True,
        "zonas": [
            {"nombre": "Miraflores", "costo": "5.00", "tiempo": "24 a 48 h"},
            {"nombre": "Callao", "costo": 12, "tiempo": "2 días"},
        ],
        "envio_gratis_desde": "150",
        "recojo_en_tienda": True,
        "notas": "No entregamos domingos",
    }

    def test_a_complete_section_keeps_money_as_decimal(self) -> None:
        parsed = Entregas.model_validate(self.VALID)

        assert parsed.zonas[0].costo == Decimal("5.00")
        assert isinstance(parsed.zonas[1].costo, Decimal)
        assert parsed.envio_gratis_desde == Decimal("150")

    def test_no_zones_means_the_cost_is_arranged_later(self) -> None:
        parsed = Entregas.model_validate({"hace_delivery": True, "recojo_en_tienda": False})

        assert parsed.zonas == []
        assert parsed.envio_gratis_desde is None

    def test_storage_form_is_json_and_keeps_the_cents(self) -> None:
        stored = validate_section("entregas", self.VALID)

        assert stored["zonas"][0]["costo"] == "5.00"
        assert stored["envio_gratis_desde"] == "150"

    @pytest.mark.parametrize(
        "change",
        [
            {"zonas": [{"nombre": "Centro", "costo": "-1", "tiempo": "1 día"}]},
            {"zonas": [{"nombre": "Centro", "costo": "1.005", "tiempo": "1 día"}]},
            {"zonas": [{"nombre": "Centro", "costo": "abc", "tiempo": "1 día"}]},
            {"zonas": [{"nombre": "Centro", "costo": "1", "tiempo": ""}]},
            {"zonas": [{"nombre": "", "costo": "1", "tiempo": "1 día"}]},
            {
                "zonas": [
                    {"nombre": "Centro", "costo": "1", "tiempo": "1 día"},
                    {"nombre": "CENTRO", "costo": "2", "tiempo": "2 días"},
                ]
            },
            {"zonas": [{"nombre": f"Z{i}", "costo": "1", "tiempo": "1 d"} for i in range(51)]},
            {"envio_gratis_desde": "0"},
            {"envio_gratis_desde": "-10"},
            {"notas": "x" * 501},
            {"hace_delivery": "quizás"},
        ],
    )
    def test_an_invalid_section_is_refused(self, change: dict[str, Any]) -> None:
        with pytest.raises(ValidationError):
            Entregas.model_validate({**self.VALID, **change})

    def test_a_float_is_not_taken_for_money_silently(self) -> None:
        """`0.1 + 0.2` style noise must not reach a price. Two places or fewer
        is accepted; a float with more is rejected."""
        with pytest.raises(ValidationError):
            Entregas.model_validate(
                {
                    **self.VALID,
                    "zonas": [{"nombre": "Centro", "costo": 0.1 + 0.2, "tiempo": "1 día"}],
                }
            )


class TestPickupNeedsAPlace:
    def test_pickup_without_locations_is_refused(self) -> None:
        entregas = Entregas.model_validate({"hace_delivery": False, "recojo_en_tienda": True})

        with pytest.raises(ValueError, match="recojo_en_tienda"):
            check_pickup_has_a_place(entregas, None)
        with pytest.raises(ValueError, match="recojo_en_tienda"):
            check_pickup_has_a_place(
                entregas, Locales.model_validate({"atiende_presencial": False})
            )

    def test_pickup_with_a_location_is_fine(self) -> None:
        entregas = Entregas.model_validate({"hace_delivery": False, "recojo_en_tienda": True})
        locales = Locales.model_validate({"atiende_presencial": True, "locales": [LOCAL]})

        check_pickup_has_a_place(entregas, locales)

    def test_no_pickup_needs_nothing(self) -> None:
        entregas = Entregas.model_validate({"hace_delivery": True, "recojo_en_tienda": False})

        check_pickup_has_a_place(entregas, None)


class TestTiposCliente:
    TIPO = {
        "nombre": "Mayorista",
        "como_reconocerlo": "Pide más de 12 unidades",
        "trato": "Precio por mayor",
    }

    def test_valid(self) -> None:
        parsed = TiposCliente.model_validate({"tipos": [self.TIPO]})

        assert parsed.tipos[0].nombre == "Mayorista"

    def test_empty_is_valid(self) -> None:
        assert TiposCliente.model_validate({}).tipos == []

    def test_at_most_six_types(self) -> None:
        TiposCliente.model_validate({"tipos": [self.TIPO] * 6})
        with pytest.raises(ValidationError):
            TiposCliente.model_validate({"tipos": [self.TIPO] * 7})

    @pytest.mark.parametrize("missing", ["nombre", "como_reconocerlo", "trato"])
    def test_every_field_is_required(self, missing: str) -> None:
        tipo = {key: value for key, value in self.TIPO.items() if key != missing}

        with pytest.raises(ValidationError):
            TiposCliente.model_validate({"tipos": [tipo]})


class TestDevoluciones:
    def test_valid(self) -> None:
        parsed = Devoluciones.model_validate(
            {
                "acepta_cambios": True,
                "acepta_devoluciones": False,
                "plazo_dias": 7,
                "condiciones": "Con boleta",
            }
        )

        assert parsed.plazo_dias == 7

    def test_a_policy_without_a_deadline_is_valid(self) -> None:
        parsed = Devoluciones.model_validate(
            {"acepta_cambios": False, "acepta_devoluciones": False}
        )

        assert parsed.plazo_dias is None

    @pytest.mark.parametrize(
        "change",
        [
            {"plazo_dias": 0},
            {"plazo_dias": 366},
            {"plazo_dias": "pronto"},
            {"condiciones": "x" * 801},
            {"acepta_cambios": None},
        ],
    )
    def test_invalid(self, change: dict[str, Any]) -> None:
        base = {"acepta_cambios": True, "acepta_devoluciones": True}

        with pytest.raises(ValidationError):
            Devoluciones.model_validate({**base, **change})


class TestContacto:
    def test_valid(self) -> None:
        parsed = Contacto.model_validate(
            {
                "telefono_humano": "+51987654321",
                "correo": "ventas@negocio.pe",
                "web": "https://negocio.pe",
                "redes": {"instagram": "https://instagram.com/negocio"},
            }
        )

        assert parsed.telefono_humano == "+51987654321"
        assert "instagram" in parsed.redes

    def test_everything_is_optional(self) -> None:
        parsed = Contacto.model_validate({})

        assert parsed.redes == {}
        assert parsed.correo is None

    @pytest.mark.parametrize(
        "change",
        [
            {"telefono_humano": "987654321"},
            {"telefono_humano": "+0123456789"},
            {"telefono_humano": "+51 987 654 321"},
            {"correo": "sin-arroba"},
            {"correo": "a@b"},
            {"correo": "con espacio@x.pe"},
            {"web": "negocio.pe"},
            {"redes": {"myspace": "https://myspace.com/x"}},
            {"redes": {"instagram": "no es url"}},
        ],
    )
    def test_invalid(self, change: dict[str, Any]) -> None:
        with pytest.raises(ValidationError):
            Contacto.model_validate(change)


class TestValidateSection:
    def test_an_unknown_section_is_a_key_error(self) -> None:
        with pytest.raises(KeyError):
            validate_section("horarios", {})

    def test_unknown_keys_are_refused_in_every_section(self) -> None:
        """A typo in a stored section would otherwise be accepted and ignored
        by every reader."""
        valid: dict[str, dict[str, Any]] = {
            "locales": {"atiende_presencial": False},
            "entregas": {"hace_delivery": False, "recojo_en_tienda": False},
            "tipos_cliente": {},
            "devoluciones": {"acepta_cambios": False, "acepta_devoluciones": False},
            "contacto": {},
        }
        for section, data in valid.items():
            validate_section(section, data)
            with pytest.raises(ValidationError):
                validate_section(section, {**data, "campo_inventado": 1})

    def test_the_stored_form_round_trips_through_the_schema(self) -> None:
        stored = validate_section("locales", {"atiende_presencial": True, "locales": [LOCAL]})

        assert Locales.model_validate(stored).model_dump(mode="json") == stored
