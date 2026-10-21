"""What the agent does, and what follows from it (feature 019): the pure rules.

A business says what its agent does; the platform derives the rest instead of asking the owner to
understand it:

* the **process template** (how an order or a quote moves) follows from the capabilities and the
  rubro's suggestion, unless the owner picked a template by hand;
* the **objective** (how hard it leads toward a sale) follows from whether it sells or quotes.

No database here, so every rule is a table-driven test; the writer is `services/capabilities.py`.
"""

from __future__ import annotations

from collections.abc import Iterable

from app.models.business import CAPABILITIES, CAPABILITIES_AVAILABLE

DEFAULT_CAPABILITIES = ("atender", "vender")


def normalize(capabilities: Iterable[str] | None, *, only_available: bool = True) -> list[str]:
    """In the canonical order, without repeats, always with "atender".

    `only_available` drops the planned ones (agendar, captar) a screen must not offer yet.
    """
    allowed = CAPABILITIES_AVAILABLE if only_available else CAPABILITIES
    chosen = set(capabilities or ()) & set(allowed)
    chosen.add("atender")
    return [c for c in CAPABILITIES if c in chosen]


def derive_template(capabilities: Iterable[str], suggested: str | None) -> str:
    """The template key that fits what the agent does.

    Education keeps its package flow when it sells. Quoting, a services rubro, or an agent that
    does not sell use the services template (it explains, quotes and hands over). The rest sell
    products.
    """
    caps = set(capabilities)
    if suggested == "certificaciones" and "vender" in caps:
        return "certificaciones"
    if "cotizar" in caps or suggested == "servicios" or "vender" not in caps:
        return "servicios"
    return "productos"


def derive_objective(capabilities: Iterable[str]) -> str:
    """`atender` if the agent only answers; else `ambos` (answer, then lead to the close)."""
    caps = set(capabilities)
    return "ambos" if caps & {"vender", "cotizar", "agendar", "captar"} else "atender"


def sells(capabilities: Iterable[str] | None) -> bool:
    return "vender" in set(capabilities or DEFAULT_CAPABILITIES)


def leads_to_a_sale(capabilities: Iterable[str] | None) -> bool:
    return bool(set(capabilities or DEFAULT_CAPABILITIES) & {"vender", "cotizar", "agendar"})
