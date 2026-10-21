"""How a time is said and read in a conversation (feature 019, phase D)."""

from __future__ import annotations

from datetime import UTC, datetime
from zoneinfo import ZoneInfo

WEEKDAYS = ("lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo")
MONTHS = (
    "enero",
    "febrero",
    "marzo",
    "abril",
    "mayo",
    "junio",
    "julio",
    "agosto",
    "septiembre",
    "octubre",
    "noviembre",
    "diciembre",
)
SLOT_FORMAT = "%Y-%m-%dT%H:%M"


def describe(moment: datetime, zone: ZoneInfo) -> str:
    """`martes 27 de octubre, 10:00` in the business's own time."""
    local = moment.astimezone(zone)
    return f"{WEEKDAYS[local.weekday()]} {local.day} de {MONTHS[local.month - 1]}, {local:%H:%M}"


def slot_token(moment: datetime, zone: ZoneInfo) -> str:
    """The local `2026-10-27T10:00` the model passes back to book that slot."""
    return moment.astimezone(zone).strftime(SLOT_FORMAT)


def parse_slot(token: object, zone: ZoneInfo) -> datetime | None:
    """A slot token (local time, no zone) as UTC, or None if it is not one."""
    # The model writes "2026-10-21 15:00" as often as "2026-10-21T15:00": both mean the same slot.
    try:
        local = datetime.strptime(str(token).strip().replace(" ", "T", 1)[:16], SLOT_FORMAT)
    except ValueError:
        return None
    return local.replace(tzinfo=zone).astimezone(UTC)
