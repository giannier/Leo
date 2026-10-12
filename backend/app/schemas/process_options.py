"""`agent_configs.process_options` (Feature 004, data-model 0016).

The options are stored as JSON and validated here before they are saved; each
process template (TASK-409) adds its own rules on top, such as which options it
accepts at all. These are the limits that hold for every template.

Every model forbids unknown keys: a misspelt option would otherwise be stored,
ignored by every reader and never noticed.
"""

from __future__ import annotations

from typing import Annotated, Literal, get_args

from pydantic import BaseModel, ConfigDict, Field, HttpUrl, StringConstraints, model_validator

# What the customer can be asked for. `tipo_entrega`, `direccion` and
# `zona_envio` are added by the engine when an order needs delivery, so they
# are not offered here.
RequiredData = Literal[
    "nombres",
    "apellidos",
    "dni",
    "ruc",
    "correo",
    "telefono",
    "direccion",
    "distrito",
    "referencia",
    "empresa",
    "fecha_nacimiento",
]
REQUIRED_DATA_CATALOG: tuple[str, ...] = get_args(RequiredData)

MAX_PATHS = 3  # WhatsApp reply buttons
MAX_PATH_TITLE = 20
MAX_VARIABLES = 10

_Text = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=500)]
_PathKey = Annotated[str, StringConstraints(pattern=r"^[a-z0-9_]{2,40}$")]
_PathTitle = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1, max_length=MAX_PATH_TITLE)
]
_VariableName = Annotated[str, StringConstraints(pattern=r"^[a-z][a-z0-9_]{0,39}$")]
_VariableValue = Annotated[str, StringConstraints(strip_whitespace=True, max_length=300)]


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Camino(_Strict):
    clave: _PathKey
    titulo: _PathTitle
    tipo: Literal["venta", "asesoria"]
    descripcion: _Text | None = None


class Calificacion(_Strict):
    activa: bool = False
    pregunta: _Text | None = None
    caminos: list[Camino] = Field(default_factory=list, max_length=MAX_PATHS)

    @model_validator(mode="after")
    def _paths_are_coherent(self) -> Calificacion:
        keys = [camino.clave for camino in self.caminos]
        if len(set(keys)) != len(keys):
            raise ValueError("path keys must be unique")
        if self.activa and (self.pregunta is None or len(self.caminos) < 2):
            raise ValueError("an active qualification needs a question and at least two paths")
        return self


class ProcessOptions(_Strict):
    calificacion: Calificacion | None = None
    modalidad_pago: Literal["previo", "contra_entrega"] | None = None
    datos_requeridos: list[RequiredData] | None = None
    link_pago_tarjeta: HttpUrl | None = None
    variables: dict[_VariableName, _VariableValue] = Field(
        default_factory=dict, max_length=MAX_VARIABLES
    )

    @model_validator(mode="after")
    def _no_repeated_data(self) -> ProcessOptions:
        data = self.datos_requeridos or []
        if len(set(data)) != len(data):
            raise ValueError("datos_requeridos must not repeat a field")
        return self
