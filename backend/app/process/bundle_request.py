"""The package a customer names before the agent can sell it (Feature 004).

A customer often says which package they want while the conversation is still
qualifying ("solo quiero certificar", "quiero el paquete de 5") and names the
topics a message later. Nothing kept the first half: the agent had to remember it
from the history, and one time in two it built a package of the topics it heard
instead (a 2-pack for "SIAF y Excel"), the flow eval's "paquete de 5 con 2 temas".

So the package is read here, deterministically, from the customer's own words
against the business's live packages, and kept as a fact (`paquete_pedido`) the
state block shows on every turn until the order exists.

What counts as naming one, conservatively (a wrong package is worse than none):
* "paquete/pack/promo de N" (or "el de N" after one of those words) where exactly
  one live package holds N selections; or
* the package's full name.
A number alone ("quiero 5") is not enough: it could be units of anything.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Sequence
from dataclasses import dataclass

_WORDS = r"(?:paquete|pack|promo|combo)"
_SIZED = re.compile(rf"\b{_WORDS}s?\b(?:\s+\w+){{0,2}}?\s+(?:de\s+)?(\d{{1,2}})\b")
_NUMBERS = {
    "dos": 2, "tres": 3, "cuatro": 4, "cinco": 5, "seis": 6, "siete": 7, "ocho": 8,
    "nueve": 9, "diez": 10,
}  # fmt: skip
_SIZED_WORD = re.compile(rf"\b{_WORDS}s?\b(?:\s+\w+){{0,2}}?\s+(?:de\s+)?({'|'.join(_NUMBERS)})\b")


@dataclass(frozen=True)
class Bundle:
    name: str
    size: int


def _plain(text: str) -> str:
    decomposed = unicodedata.normalize("NFKD", text.casefold())
    return " ".join("".join(c for c in decomposed if not unicodedata.combining(c)).split())


def requested_bundle(text: str, bundles: Sequence[Bundle]) -> Bundle | None:
    """The one live package the text names, or None when it names none or is ambiguous."""
    if not text or not bundles:
        return None
    said = _plain(text)
    by_name = [b for b in bundles if _plain(b.name) and _plain(b.name) in said]
    if len(by_name) == 1:
        return by_name[0]
    sizes = {int(m) for m in _SIZED.findall(said)}
    sizes |= {_NUMBERS[w] for w in _SIZED_WORD.findall(said)}
    if len(sizes) != 1:
        return None
    (size,) = sizes
    matching = [b for b in bundles if b.size == size]
    return matching[0] if len(matching) == 1 else None
