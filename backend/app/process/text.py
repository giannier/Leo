"""Text helpers of the process package that import nothing from it (so any module may use them)."""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Sequence
from typing import Protocol

# How many letters of a word name a product: «martillo» and «martillos» are the same word.
NAME_WORD = 4
# Words that say nothing about which product: «el martillo», «uno de los alicates».
FILLER_WORDS = frozenset(
    [
        "el",
        "la",
        "los",
        "las",
        "lo",
        "un",
        "una",
        "unos",
        "unas",
        "de",
        "del",
        "al",
        "a",
        "y",
        "o",
        "con",
        "para",
        "en",
        "por",
        "mi",
        "su",
        "uno",
    ]
)


def plain(text: str) -> str:
    """Lower case, no accents, single spaces: how two spellings of a datum are compared."""
    stripped = "".join(
        c for c in unicodedata.normalize("NFD", text.lower()) if unicodedata.category(c) != "Mn"
    )
    return " ".join(stripped.split())


class _Named(Protocol):
    @property
    def name(self) -> str: ...


def name_tokens(text: str) -> set[str]:
    """The stems of a product name: 4 letters of a word, or a number («20»)."""
    spaced = re.sub(r"(\d)([a-z])", r"\1 \2", re.sub(r"([a-z])(\d)", r"\1 \2", plain(text)))
    return {
        w[:NAME_WORD] if w.isalpha() else w
        for w in re.findall(r"[a-z0-9]+", spaced)
        if (w.isalpha() and len(w) >= NAME_WORD) or w.isdigit()
    }


def match_name[Named: _Named](value: str, items: Sequence[Named]) -> Named | None:
    """The one item `value` names, if it names exactly one of `items`.

    Only the words that tell the items apart count: «bidón de 20 L» names the Bidón when the
    other one is the Caja de 20 L (both have «agua» and «20»), «agua de 20 L» names neither.
    """
    if not items:
        return None
    names = [name_tokens(item.name) for item in items]
    shared = set.intersection(*names) if len(names) > 1 else set()
    said = name_tokens(value)
    scores = [len(said & (tokens - shared)) for tokens in names]
    best = max(scores)
    winners = [item for item, score in zip(items, scores, strict=True) if score == best]
    return winners[0] if best > 0 and len(winners) == 1 else None
