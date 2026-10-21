"""Where a customer came from (feature 019, phase F: attribution of origin).

Three ways to know, none of them Meta's: the link the customer followed (the web chat's
`?o=instagram`), what they said in their first words ("hola, te escribo desde Instagram", the text
a link to WhatsApp for a social network pre-fills), and, on WhatsApp, the ad they came through
(kept apart, as `entrada_anuncio`). The names are the ones a person reads in the CRM: a short fixed
list, and whatever else the link says, cleaned.
"""

from __future__ import annotations

import re

MAX_ORIGIN = 40

# What people write, by the name the CRM shows.
KNOWN: dict[str, tuple[str, ...]] = {
    "Instagram": ("instagram", "ig", "insta"),
    "Facebook": ("facebook", "fb", "face"),
    "TikTok": ("tiktok", "tik tok", "tt"),
    "YouTube": ("youtube", "yt", "you tube"),
    "Google": ("google", "goog"),
    "WhatsApp": ("whatsapp", "wa", "wsp"),
    "LinkedIn": ("linkedin",),
    "Página web": (
        "web",
        "pagina web",
        "página web",
        "sitio web",
        "su pagina",
        "su página",
        "tu pagina",
        "tu página",
    ),
}
_BY_ALIAS = {alias: name for name, aliases in KNOWN.items() for alias in aliases}
_SAID = re.compile(
    r"\b(?:desde|vengo de|te escribo de|vi (?:tu|su|el|la) \w+ (?:en|de)|los vi en|las vi en|"
    r"te vi en|los encontr[eé] en|te encontr[eé] en)\s+(?:el\s+|la\s+|tu\s+|su\s+)?"
    r"(instagram|insta|facebook|tiktok|tik tok|youtube|google|linkedin|"
    r"p[aá]gina web|sitio web|la web|su p[aá]gina|tu p[aá]gina)",
    re.IGNORECASE,
)


def normalize_origin(raw: str | None) -> str | None:
    """A name for the CRM from what a link or a person wrote, or None if there is nothing usable."""
    if not raw:
        return None
    cleaned = re.sub(r"[^\w\s\-áéíóúñÁÉÍÓÚÑ]", "", raw, flags=re.UNICODE).strip()[:MAX_ORIGIN]
    if not cleaned:
        return None
    known = _BY_ALIAS.get(cleaned.lower())
    if known is not None:
        return known
    return cleaned[:1].upper() + cleaned[1:]


def origin_from_text(text: str) -> str | None:
    """The origin a customer names in their words ("te escribo desde Instagram"), if they do."""
    found = _SAID.search(text or "")
    return normalize_origin(found.group(1)) if found else None
