"""Rules a file must pass before it enters the media library (data-model 0017).

WhatsApp rejects files outside its limits, so they are checked at upload and the
tenant hears about it then, not when a customer is waiting for the file.

Limits (data-model.md, section 0017): JPG/PNG image up to 5 MB, MP4 video up to
16 MB, audio up to 16 MB, PDF document up to 100 MB.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from app.models.media import CLAVE_PATTERN, MEDIA_TYPES

MB = 1024 * 1024


@dataclass(frozen=True)
class MediaLimit:
    mime_types: frozenset[str]
    max_bytes: int


MEDIA_LIMITS: dict[str, MediaLimit] = {
    "imagen": MediaLimit(frozenset({"image/jpeg", "image/png"}), 5 * MB),
    "video": MediaLimit(frozenset({"video/mp4"}), 16 * MB),
    # The spec fixes the size only; these are the audio formats WhatsApp accepts.
    # List and limit ratified by Giannier (Milestone B, decision 5).
    "audio": MediaLimit(
        frozenset({"audio/aac", "audio/mp4", "audio/mpeg", "audio/amr", "audio/ogg"}), 16 * MB
    ),
    "documento": MediaLimit(frozenset({"application/pdf"}), 100 * MB),
}

_CLAVE = re.compile(CLAVE_PATTERN)


def validate_clave(clave: str) -> str:
    """Return `clave` if it is a valid library key, else raise `ValueError`."""
    if not _CLAVE.fullmatch(clave):
        raise ValueError("clave must be 2 to 40 characters: lowercase letters, digits and _")
    return clave


def validate_upload(tipo: str, mime_type: str, size_bytes: int) -> None:
    """Raise `ValueError` when a file does not fit WhatsApp's limits for its type."""
    if tipo not in MEDIA_TYPES:
        raise ValueError(f"unknown media type {tipo!r}; expected one of {', '.join(MEDIA_TYPES)}")
    limit = MEDIA_LIMITS[tipo]
    if mime_type not in limit.mime_types:
        allowed = ", ".join(sorted(limit.mime_types))
        raise ValueError(f"{mime_type!r} is not allowed for {tipo}; expected one of {allowed}")
    if size_bytes <= 0:
        raise ValueError("the file is empty")
    if size_bytes > limit.max_bytes:
        raise ValueError(
            f"the file is larger than {limit.max_bytes // MB} MB, the limit for {tipo}"
        )
