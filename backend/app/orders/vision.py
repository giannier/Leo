"""Reading a payment receipt from its photo (Feature 004, TASK-429, C-08).

One call to a vision-capable model with a structured answer: is this a payment
receipt, and if so what does it say. The model is asked for strings and numbers as
text so an amount never passes through a float; `Decimal` is built here.

Nothing here decides anything about the order. The reading is data for
`ProofPipeline`, which compares it with the order and flags what looks off. And it
never raises: a model that cannot see, a timeout, an unreadable answer all come
back as a `ProofReading` with `legible=False`, because the customer's receipt must
be recorded whether or not a machine could read it (the operator reads the photo).
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Any

from app.providers.llm.base import LLMProvider, VisionProvider

logger = logging.getLogger(__name__)

# `plan.md`: "Tiempo máximo de 8 s; si se pasa, se guarda sin datos y el turno sigue".
VISION_TIMEOUT_SECONDS = 8.0

# The image types WhatsApp delivers and the vision endpoints accept.
IMAGE_TYPES = ("image/jpeg", "image/png", "image/webp")
MAX_IMAGE_BYTES = 5 * 1024 * 1024

_MAX_TEXT = 120
_MAX_AMOUNT = Decimal("9999999999.99")

INSTRUCTION = (
    "Look at this image. Decide whether it is a payment receipt or transfer "
    "confirmation (Yape, Plin, a bank app or a deposit slip). If it is, read what it "
    "says: the amount, its currency (ISO 4217, for example PEN), the date of the "
    "operation (YYYY-MM-DD), the operation code, the bank or wallet, and the name of "
    "who received the money. Use null for anything you cannot read; never guess. "
    "'confianza' is how sure you are that the amounts and the code were read "
    "correctly, from 0 to 1. Write numbers as plain text with a dot, no symbols."
)

_NULLABLE_TEXT = {"type": ["string", "null"]}

SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": [
        "es_comprobante",
        "monto",
        "moneda",
        "fecha",
        "codigo",
        "entidad",
        "destinatario",
        "confianza",
    ],
    "properties": {
        "es_comprobante": {"type": "boolean"},
        "monto": _NULLABLE_TEXT,
        "moneda": _NULLABLE_TEXT,
        "fecha": _NULLABLE_TEXT,
        "codigo": _NULLABLE_TEXT,
        "entidad": _NULLABLE_TEXT,
        "destinatario": _NULLABLE_TEXT,
        "confianza": {"type": ["number", "null"]},
    },
}


@dataclass(frozen=True)
class ProofReading:
    """What the vision step made of an image.

    `legible` is False when nothing could be read at all (no vision, a timeout, a
    broken answer): `es_comprobante` is then unknown, and the pipeline keeps the
    image as a proof without data. With `legible=True` the model has spoken, and
    `es_comprobante` says whether the image is a receipt.
    """

    legible: bool
    es_comprobante: bool = False
    monto: Decimal | None = None
    moneda: str | None = None
    fecha: date | None = None
    codigo: str | None = None
    entidad: str | None = None
    destinatario: str | None = None
    confianza: Decimal | None = None
    model: str | None = None
    # Why nothing was read: `sin_vision`, `timeout`, `error`, `respuesta_invalida`.
    reason: str | None = None
    raw: dict[str, Any] = field(default_factory=dict)

    @property
    def has_data(self) -> bool:
        """Whether the amount or the operation code could be read."""
        return self.monto is not None or self.codigo is not None


def unreadable(reason: str) -> ProofReading:
    return ProofReading(legible=False, reason=reason)


async def read_proof(
    llm: LLMProvider,
    image: bytes,
    mime_type: str,
    *,
    timeout_seconds: float | None = None,
) -> ProofReading:
    """Read a receipt photo. Never raises: see the module docstring."""
    timeout_seconds = VISION_TIMEOUT_SECONDS if timeout_seconds is None else timeout_seconds
    if not isinstance(llm, VisionProvider):
        return unreadable("sin_vision")
    try:
        reading = await asyncio.wait_for(
            llm.read_image(
                image,
                mime_type,
                instruction=INSTRUCTION,
                schema=SCHEMA,
                timeout_seconds=timeout_seconds,
            ),
            timeout=timeout_seconds,
        )
    except TimeoutError:
        logger.warning("reading a receipt timed out", extra={"seconds": timeout_seconds})
        return unreadable("timeout")
    except Exception as exc:
        # Any provider failure: the receipt is still recorded, without data.
        logger.warning("reading a receipt failed", extra={"error": type(exc).__name__})
        return unreadable("error")
    return parse_reading(reading.data, model=reading.model)


def parse_reading(data: dict[str, Any], *, model: str | None = None) -> ProofReading:
    """The model's object as a `ProofReading`; anything malformed becomes unreadable."""
    if not isinstance(data.get("es_comprobante"), bool):
        return unreadable("respuesta_invalida")
    if not data["es_comprobante"]:
        return ProofReading(legible=True, es_comprobante=False, model=model, raw=_safe(data))
    return ProofReading(
        legible=True,
        es_comprobante=True,
        monto=_amount(data.get("monto")),
        moneda=_currency(data.get("moneda")),
        fecha=_day(data.get("fecha")),
        codigo=_text(data.get("codigo")),
        entidad=_text(data.get("entidad")),
        destinatario=_text(data.get("destinatario")),
        confianza=_confidence(data.get("confianza")),
        model=model,
        raw=_safe(data),
    )


# ----------------------------------------------------------------- coercion


def _safe(data: dict[str, Any]) -> dict[str, Any]:
    """The raw answer, bounded, for `payment_proofs.extraccion` (no image in it)."""
    return {str(key): value for key, value in list(data.items())[:20]}


def _text(value: object) -> str | None:
    if value is None or isinstance(value, bool):
        return None
    cleaned = " ".join(str(value).split())[:_MAX_TEXT]
    return cleaned or None


def _amount(value: object) -> Decimal | None:
    """`"S/ 1,250.50"` or `"1250.5"` -> Decimal('1250.50'); None if unreadable."""
    text = _text(value)
    if text is None:
        return None
    kept = "".join(char for char in text if char.isdigit() or char in ".,")
    if not kept:
        return None
    # A comma followed by exactly two digits at the end is a decimal comma.
    if "," in kept and "." not in kept and len(kept) - kept.rfind(",") == 3:
        kept = kept.replace(",", ".")
    kept = kept.replace(",", "")
    try:
        amount = Decimal(kept).quantize(Decimal("0.01"))
    except InvalidOperation:
        return None
    if amount < 0 or amount > _MAX_AMOUNT:
        return None
    return amount


def _currency(value: object) -> str | None:
    text = _text(value)
    if text is None:
        return None
    code = text.upper()
    if code in ("S/", "S/.", "SOLES"):
        return "PEN"
    if code in ("$", "US$", "DOLARES", "DÓLARES"):
        return "USD"
    return code if len(code) == 3 and code.isalpha() else None


def _day(value: object) -> date | None:
    text = _text(value)
    if text is None:
        return None
    try:
        return date.fromisoformat(text[:10])
    except ValueError:
        return None


def _confidence(value: object) -> Decimal | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        number = Decimal(str(value))
    except InvalidOperation:
        return None
    if not Decimal(0) <= number <= Decimal(1):
        return None
    return number.quantize(Decimal("0.01"))
