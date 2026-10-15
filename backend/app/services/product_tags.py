"""Product tags as the business writes them, cleaned once (Feature 018, RF-001).

Up to ten, each 1 to 30 characters, no repeats: "Oferta" and "oferta " are the same tag, and so are
"Gestión" and "gestion". The first spelling written is the one kept.
"""

from __future__ import annotations

import unicodedata
from collections.abc import Iterable

MAX_TAGS = 10
MAX_TAG_CHARS = 30


class TagsInvalid(ValueError):
    """More tags than allowed, or one that is too long."""


def tag_key(tag: str) -> str:
    """The form two tags are compared in: no case, no accents, single spaces."""
    decomposed = unicodedata.normalize("NFKD", tag.casefold())
    return " ".join("".join(c for c in decomposed if not unicodedata.combining(c)).split())


def clean_tags(raw: Iterable[str]) -> list[str]:
    tags: list[str] = []
    seen: set[str] = set()
    for value in raw:
        tag = " ".join(value.split()).lstrip("#")
        if not tag:
            continue
        if len(tag) > MAX_TAG_CHARS:
            raise TagsInvalid(f"Cada etiqueta puede tener hasta {MAX_TAG_CHARS} caracteres.")
        key = tag_key(tag)
        if key in seen:
            continue
        seen.add(key)
        tags.append(tag)
    if len(tags) > MAX_TAGS:
        raise TagsInvalid(f"Un producto puede tener hasta {MAX_TAGS} etiquetas.")
    return tags


def split_tags(cell: str) -> list[str]:
    """A spreadsheet cell "oferta, verano; SIAF" as its tags."""
    return [part for chunk in cell.split(";") for part in chunk.split(",")]
