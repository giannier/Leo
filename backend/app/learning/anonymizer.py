"""Taking the customers' personal data out of a text before it is kept (017, RF-016; Ley 29733).

Two passes. This module is the first: rules for what has a shape (an e-mail, a phone, an identity
number, a card or account number, a link, an address with a number, «me llamo …»). The second pass,
for proper names and addresses without a number, is a model's (`learning/redactor.py`) and runs only
on what the rules left. Neither pass is perfect, so what is kept is a *suggestion* a person reads
before it takes effect, and the original of an import is never stored.

Each placeholder says what was there, never the value.
"""

from __future__ import annotations

import re

# The street words of an address, with and without dots, in Spanish as written in Peru.
_STREET = (
    r"(?:avenida|av\.?|jir[oó]n|jr\.?|calle|ca\.?|pasaje|psje\.?|pje\.?|urbanizaci[oó]n|urb\.?"
    r"|manzana|mz\.?|lote|lt\.?|carretera|prolongaci[oó]n|prol\.?|alameda|malec[oó]n|plaza)"
)
_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+"), "[correo]"),
    (re.compile(r"(?:https?://|www\.)\S+", re.IGNORECASE), "[enlace]"),
    # A card or an account: 12 to 19 digits, in groups.
    (re.compile(r"(?<!\d)(?:\d[\s-]?){11,18}\d(?!\d)"), "[número]"),
    # A RUC: 11 digits starting with 10, 15, 16, 17 or 20.
    (re.compile(r"(?<!\d)(?:10|15|16|17|20)\d{9}(?!\d)"), "[ruc]"),
    # A phone: +51 or 51 and a mobile (a 9 and eight digits), a mobile alone, or any +country.
    (
        re.compile(r"(?<![\d])(?:\+?51[\s.-]?)?9(?:[\s.-]?\d){8}(?!\d)"),
        "[teléfono]",
    ),
    (re.compile(r"\+\d{1,3}(?:[\s.-]?\d){7,12}"), "[teléfono]"),
    # A DNI: eight digits on their own.
    (re.compile(r"(?<![\d])\d{8}(?![\d])"), "[documento]"),
    # «Av. Larco 123», «Jr. Los Pinos Mz B Lt 4».
    (
        re.compile(
            rf"\b{_STREET}\s+[^\n,;]{{2,40}}?\s+(?:n[°ºo]?\.?\s*)?\d{{1,5}}[a-z]?\b",
            re.IGNORECASE,
        ),
        "[dirección]",
    ),
    (
        re.compile(
            r"\b(?:mz|manzana)\.?\s*[a-z0-9]{1,3}\s*(?:,\s*)?(?:lt|lote)\.?\s*\d{1,3}\b",
            re.IGNORECASE,
        ),
        "[dirección]",
    ),
    # «me llamo Ana Pérez», «soy Carlos»: the name after the words that introduce one. Only the
    # introducer ignores case; a name is a capitalised word, so «soy un asistente» is left alone.
    (
        re.compile(
            r"\b((?i:me llamo|mi nombre es|soy|le habla|habla))\s+"
            r"((?:[A-ZÁÉÍÓÚÑ][a-záéíóúñ]+)(?:\s+(?:de|del|la|los|las|[A-ZÁÉÍÓÚÑ][a-záéíóúñ]+)){0,3})"
        ),
        r"\1 [nombre]",
    ),
)


def redact(text: str) -> str:
    """The text with the shaped personal data replaced by placeholders."""
    out = text
    for pattern, replacement in _PATTERNS:
        out = pattern.sub(replacement, out)
    return out


def still_has_data(text: str) -> bool:
    """Whether the rules still find something in the text (a check after redacting)."""
    return redact(text) != text
