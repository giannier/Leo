"""Personal data never reaches a trace or a log (Feature 004, TASK-427, C-20).

The customer's DNI, address and e-mail live in `contacts.custom_data` and, in the
order, in `datos_cliente`. They must not also end up in `agent_traces.tools_called`
(which the panel shows and which stays for as long as the conversation), nor in a
log line. `mask_arguments` is the one redactor: the agent core runs every tool's
arguments through it before they are summarised for the trace.

What is masked is decided by the *name of the field*, not by the tool, so a new
tool that takes a `dni` is covered the day it is written. A masked value keeps
just enough to be recognisable to the person who owns it:

    dni 12345678          ->  ****5678
    telefono +51987654321 ->  ****4321
    correo ana@mail.com   ->  a***@mail.com
    direccion, nombres... ->  ***
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any

from app.orders.customer_data import PERSONAL_FIELDS

MASK = "***"
# Fields identified by a number: the last four digits are kept.
_NUMERIC = frozenset({"dni", "ruc", "telefono"})
_KEEP = 4


def mask_value(field: str, value: Any) -> Any:
    """The masked form of one personal value; anything else is returned as is."""
    if field not in PERSONAL_FIELDS:
        return value
    if value is None or value == "":
        return value
    text = str(value)
    if field in _NUMERIC:
        digits = "".join(char for char in text if char.isdigit())
        return f"****{digits[-_KEEP:]}" if len(digits) > _KEEP else "****"
    if field == "correo" and "@" in text:
        local, _, domain = text.partition("@")
        return f"{local[:1]}***@{domain}"
    return MASK


def mask_arguments(arguments: Mapping[str, Any]) -> dict[str, Any]:
    """A copy of a tool's arguments with every personal field masked.

    Recurses into nested dictionaries and lists, so a model that wraps the data
    (`{"datos": {"dni": ...}}`) is covered as well.
    """
    return {key: _mask_any(str(key), value) for key, value in arguments.items()}


def _mask_any(field: str, value: Any) -> Any:
    if isinstance(value, Mapping):
        return mask_arguments(value)
    if isinstance(value, list | tuple):
        return [_mask_any(field, item) for item in value]
    return mask_value(field, value)


# What replaces a registered document in the history the model reads (decision 18).
REGISTERED_LABELS = {"dni": "[DNI registrado]", "ruc": "[RUC registrado]"}


def redact_registered(text: str, registered: Mapping[str, Any]) -> str:
    """Replace the DNI or RUC the customer already registered with a label.

    Milestone B, decision 18: once `registrar_datos_cliente` stored the number,
    the history that goes back to the model says `[DNI registrado]` instead. The
    message itself is untouched (the operator still sees it). The number is found
    however the customer typed it: `12345678`, `12 345 678` or `12-345-678`.
    """
    for field, label in REGISTERED_LABELS.items():
        digits = "".join(char for char in str(registered.get(field) or "") if char.isdigit())
        if not digits:
            continue
        pattern = r"(?<!\d)" + r"[\s.-]?".join(digits) + r"(?!\d)"
        text = re.sub(pattern, label, text)
    return text
