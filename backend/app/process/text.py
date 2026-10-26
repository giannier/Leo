"""Text helpers of the process package that import nothing from it (so any module may use them)."""

from __future__ import annotations

import unicodedata


def plain(text: str) -> str:
    """Lower case, no accents, single spaces: how two spellings of a datum are compared."""
    stripped = "".join(
        c for c in unicodedata.normalize("NFD", text.lower()) if unicodedata.category(c) != "Mn"
    )
    return " ".join(stripped.split())
