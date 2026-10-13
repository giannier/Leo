"""Validation of the customer's data, field by field (Feature 004, TASK-427).

`registrar_datos_cliente` accepts partial data (one field now, another later), so
each field is judged on its own: the valid ones are saved and the others come
back with a reason the model can turn into a question. The rules are the ones of
`data-model.md` 0020: a DNI has 8 digits, a RUC 11, the e-mail and the phone are
well formed (the phone in E.164), plus sane bounds for the free-text fields.

The messages are addressed to the model ("pide el DNI de 8 dígitos"), not to the
customer, and never contain the value they reject: the value is personal data
(C-20) and a reason must be safe to log.
"""

from __future__ import annotations

import re
from datetime import UTC, date, datetime

# Data that identify or locate the customer. Anything saved in `custom_data`, masked
# in traces and logs (C-20). `zona_envio` and `tipo_entrega` are about the order, not
# the person, and live on it.
PERSONAL_FIELDS = frozenset(
    {
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
    }
)
# The fields that go to the order instead of the contact.
ORDER_FIELDS = frozenset({"tipo_entrega", "zona_envio"})

_SPACES = re.compile(r"\s+")
_DIGITS = re.compile(r"\D")
_EMAIL = re.compile(r"^[^@\s]{1,64}@[^@\s]+\.[^@\s.]{2,}$")
_E164 = re.compile(r"^\+[1-9]\d{7,14}$")
_PERU_COUNTRY_CODE = "+51"
_MAX_AGE_YEARS = 120

_TEXT_BOUNDS: dict[str, tuple[int, int]] = {
    "nombres": (2, 100),
    "apellidos": (2, 100),
    "empresa": (2, 120),
    "distrito": (2, 80),
    "direccion": (5, 200),
    "referencia": (2, 200),
}
_TEXT_NAMES = {
    "nombres": "los nombres",
    "apellidos": "los apellidos",
    "empresa": "la empresa",
    "distrito": "el distrito",
    "direccion": "la dirección",
    "referencia": "la referencia",
}


class InvalidField(ValueError):
    """A value that does not pass. The message says why, without echoing it."""


def _one_line(raw: str) -> str:
    return _SPACES.sub(" ", raw).strip()


def _text(field: str, raw: str) -> str:
    value = _one_line(raw)
    low, high = _TEXT_BOUNDS[field]
    if len(value) < low:
        raise InvalidField(f"{_TEXT_NAMES[field]} es demasiado corto; pídelo completo.")
    if len(value) > high:
        raise InvalidField(f"{_TEXT_NAMES[field]} es demasiado largo (máximo {high} caracteres).")
    if not any(char.isalpha() for char in value):
        raise InvalidField(f"{_TEXT_NAMES[field]} debe tener letras.")
    return value


def _dni(raw: str) -> str:
    digits = _DIGITS.sub("", raw)
    if len(digits) != 8:
        raise InvalidField("el DNI debe tener exactamente 8 dígitos; vuelve a pedirlo.")
    return digits


def _ruc(raw: str) -> str:
    digits = _DIGITS.sub("", raw)
    if len(digits) != 11:
        raise InvalidField("el RUC debe tener exactamente 11 dígitos; vuelve a pedirlo.")
    return digits


def _email(raw: str) -> str:
    value = _one_line(raw).lower()
    if not _EMAIL.match(value) or len(value) > 120:
        raise InvalidField("el correo no parece válido; pide que lo escriba de nuevo.")
    return value


def _phone(raw: str, pais: str = "PE") -> str:
    """E.164. A number typed without its prefix takes the business's country's.

    Only Peru has a rule today (a bare 9-digit number starting with 9 is a
    mobile); anywhere else the customer is asked for the full number, which is
    the conservative reading of decision 10 until a country needs its own rule.
    """
    compact = re.sub(r"[\s\-().]", "", raw)
    if compact.startswith("00"):
        compact = "+" + compact[2:]
    if pais == "PE" and re.fullmatch(r"9\d{8}", compact):
        compact = _PERU_COUNTRY_CODE + compact
    if not _E164.match(compact):
        raise InvalidField(
            "el teléfono no es válido; pídelo completo (con código de país si no es peruano)."
        )
    return compact


def _birth_date(raw: str, *, today: date | None = None) -> str:
    value = _one_line(raw)
    parsed: date | None = None
    for pattern in ("%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y"):
        try:
            parsed = datetime.strptime(value, pattern).date()
            break
        except ValueError:
            continue
    if parsed is None:
        raise InvalidField("la fecha de nacimiento debe ser día/mes/año; pídela de nuevo.")
    today = today or datetime.now(UTC).date()
    if parsed > today or today.year - parsed.year > _MAX_AGE_YEARS:
        raise InvalidField("la fecha de nacimiento no es posible; pídela de nuevo.")
    return parsed.isoformat()


def validate_field(field: str, raw: object, *, pais: str = "PE") -> str:
    """The normalised value of one customer field, or `InvalidField`.

    Normalised means what gets stored: digits only for the DNI and the RUC, the
    e-mail in lower case, the phone in E.164, text on one line.
    """
    if isinstance(raw, int) and not isinstance(raw, bool):
        # A model sometimes sends a DNI or a phone as a JSON number.
        raw = str(raw)
    if not isinstance(raw, str) or not raw.strip():
        raise InvalidField("el valor está vacío.")
    if field == "dni":
        return _dni(raw)
    if field == "ruc":
        return _ruc(raw)
    if field == "correo":
        return _email(raw)
    if field == "telefono":
        return _phone(raw, pais)
    if field == "fecha_nacimiento":
        return _birth_date(raw)
    if field in _TEXT_BOUNDS:
        return _text(field, raw)
    raise InvalidField("ese dato no se pide.")
