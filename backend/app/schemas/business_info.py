"""The five sections of `business_info.data` (Feature 004, data-model 0015).

The API validates a section against its schema before storing it; the prompt
builder reads it back with the same schema, so what the assistant says about the
business is always something that passed validation.

Every schema forbids unknown keys. A typo in a stored section would otherwise be
accepted, silently ignored by every reader and never noticed.

Money is `Decimal` with at most two places, never `float` (Constitution).
"""

from __future__ import annotations

import re
from decimal import Decimal
from typing import Annotated, Any, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    HttpUrl,
    StringConstraints,
    field_validator,
    model_validator,
)

# Same pattern the registration and connection endpoints use for phone numbers.
E164_PATTERN = r"^\+[1-9]\d{7,14}$"
# A deliberately plain address check. `pydantic.EmailStr` would need the
# `email-validator` package, which is not a dependency of this project.
EMAIL_PATTERN = r"^[^@\s]+@[^@\s]+\.[^@\s]{2,}$"

_Name = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=120)]
_Line = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=300)]
_Phone = Annotated[str, StringConstraints(strip_whitespace=True, pattern=E164_PATTERN)]
_Email = Annotated[
    str, StringConstraints(strip_whitespace=True, max_length=254, pattern=EMAIL_PATTERN)
]
# Two decimal places at most: `Decimal("10.505")` is not an amount of money.
_Money = Annotated[Decimal, Field(ge=0, max_digits=12, decimal_places=2)]

_DAYS = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")
_CLOCK = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")

Horarios = dict[str, list[list[str]]]
"""Opening hours in the shape `business_profiles.opening_hours` already uses:
`{"mon": [["09:00", "18:00"]], "sat": [["09:00", "13:00"]]}`."""


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


def _check_hours(value: Horarios) -> Horarios:
    for day, ranges in value.items():
        if day not in _DAYS:
            raise ValueError(f"unknown day {day!r}; expected one of {', '.join(_DAYS)}")
        for span in ranges:
            if len(span) != 2 or not all(_CLOCK.match(moment) for moment in span):
                raise ValueError(f"{day}: each range must be two HH:MM times, got {span!r}")
            if span[0] >= span[1]:
                raise ValueError(f"{day}: a range must end after it starts, got {span!r}")
    return value


class Local(_Strict):
    nombre: _Name
    direccion: _Line
    referencia: _Line | None = None
    maps_url: HttpUrl | None = None
    horarios: Horarios = Field(default_factory=dict)

    _hours = field_validator("horarios")(_check_hours)


class Locales(_Strict):  # section = 'locales'
    atiende_presencial: bool
    locales: list[Local] = Field(default_factory=list, max_length=20)


class Zona(_Strict):
    nombre: _Name
    costo: _Money
    tiempo: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=60)]


class Entregas(_Strict):  # section = 'entregas'
    """The only source of truth for delivery (C-22): the process template has
    no delivery option, so the two can never disagree."""

    hace_delivery: bool
    # No zones means the cost is "a coordinar" and the shipping charge is 0.
    zonas: list[Zona] = Field(default_factory=list, max_length=50)
    envio_gratis_desde: Annotated[Decimal, Field(gt=0, max_digits=12, decimal_places=2)] | None = (
        None
    )
    recojo_en_tienda: bool
    notas: Annotated[str, StringConstraints(strip_whitespace=True, max_length=500)] | None = None

    @model_validator(mode="after")
    def _zone_names_are_unique(self) -> Entregas:
        seen: set[str] = set()
        for zona in self.zonas:
            key = zona.nombre.casefold()
            if key in seen:
                raise ValueError(f"zone {zona.nombre!r} is listed twice")
            seen.add(key)
        return self


class TipoCliente(_Strict):
    nombre: _Name
    # "Pide más de 12 unidades"
    como_reconocerlo: Annotated[
        str, StringConstraints(strip_whitespace=True, min_length=1, max_length=300)
    ]
    # "Precio por mayor"
    trato: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=500)]


class TiposCliente(_Strict):  # section = 'tipos_cliente'
    tipos: list[TipoCliente] = Field(default_factory=list, max_length=6)


class Devoluciones(_Strict):  # section = 'devoluciones'
    acepta_cambios: bool
    acepta_devoluciones: bool
    plazo_dias: Annotated[int, Field(ge=1, le=365)] | None = None
    condiciones: Annotated[str, StringConstraints(strip_whitespace=True, max_length=800)] | None = (
        None
    )


Network = Literal["instagram", "facebook", "tiktok", "youtube", "linkedin"]


class Contacto(_Strict):  # section = 'contacto'
    telefono_humano: _Phone | None = None
    correo: _Email | None = None
    web: HttpUrl | None = None
    redes: dict[Network, HttpUrl] = Field(default_factory=dict)


SECTION_SCHEMAS: dict[str, type[BaseModel]] = {
    "locales": Locales,
    "entregas": Entregas,
    "tipos_cliente": TiposCliente,
    "devoluciones": Devoluciones,
    "contacto": Contacto,
}


def validate_section(section: str, data: dict[str, Any]) -> dict[str, Any]:
    """Validate `data` for `section` and return its storable JSON form.

    `mode="json"` turns `Decimal` into a string and `HttpUrl` into text, so the
    result can go straight into the JSONB column without losing precision.

    Raises `KeyError` for an unknown section and `pydantic.ValidationError` for
    data that does not fit it.
    """
    schema = SECTION_SCHEMAS[section]
    return schema.model_validate(data).model_dump(mode="json")


def check_pickup_has_a_place(entregas: Entregas, locales: Locales | None) -> None:
    """A business that offers pickup must have somewhere to pick up from.

    This spans two sections, so no single schema can enforce it; the API calls
    it when either section is saved.
    """
    if entregas.recojo_en_tienda and (locales is None or not locales.locales):
        raise ValueError("recojo_en_tienda requires at least one local in the 'locales' section")
