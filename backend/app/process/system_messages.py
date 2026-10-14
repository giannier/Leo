"""Filling the system messages of `plantillas.md` section 7.

Here, below the services, because both the operator's notices
(`services/order_notices.py`) and the handover tool (`agent/tools/handover.py`)
render them, and the tool must not import the channel service.
"""

from __future__ import annotations

import re
from collections.abc import Mapping

_PLACEHOLDER = re.compile(r"\{(\w+)\}")
_COMMA_BEFORE_NAME = re.compile(r",\s*\{nombre_cliente\}")
_COMMA_AFTER_NAME = re.compile(r"\{nombre_cliente\}\s*,\s*")


def render_system_message(template: str, values: Mapping[str, str | None]) -> str:
    """Fill a system message. A value that is missing disappears with its punctuation.

    "¡{nombre_cliente}, tu pago fue confirmado!" for a customer whose name we never
    got reads "¡Tu pago fue confirmado!", not "¡, tu pago...". Unknown placeholders are
    removed too: an owner who typed `{algo}` must not see braces reach the customer.
    """
    text = template
    for key, value in values.items():
        if value:
            text = text.replace("{" + key + "}", value)
    if not values.get("nombre_cliente"):
        text = _COMMA_BEFORE_NAME.sub("", text)
        text = _COMMA_AFTER_NAME.sub("", text)
    text = _PLACEHOLDER.sub("", text)
    text = re.sub(r"[ \t]{2,}", " ", text)
    text = re.sub(r"\s+([.,;:!?])", r"\1", text).strip()
    # The first letter after an opening mark or at the start is a capital again.
    return re.sub(r"^([¡¿]?)([a-záéíóúñ])", lambda m: m.group(1) + m.group(2).upper(), text)
