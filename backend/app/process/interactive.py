"""What the customer tapped on a button or a list (Feature 004, C-15).

Every interactive id the assistant sends carries a prefix:
`camino:{clave}`, `producto:{id}`, `categoria:{id}`, `promo:{id}`, `accion:{clave}`.

Only `camino:` writes a fact directly, because the choice must be recorded even
if the model never calls `elegir_camino`. Everything else becomes one line of
context for the turn ("ESTE TURNO" in the prompt) that the model reads and acts
on. Nothing here is persisted and nothing calls a provider: the function is pure,
so each prefix is a plain table-driven test.

The id arrives from the webhook, so it is treated as input: a path key the
tenant never configured is ignored (and logged), never stored as a fact.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Any

from app.process.facts import Facts
from app.process.templates.base import EffectiveOptions

# The type the provider adapter produces when it parses a `button_reply` or a
# `list_reply`; re-exported here so the process engine keeps one import path.
from app.providers.whatsapp.base import InteractiveReply

__all__ = ["InteractiveOutcome", "InteractiveReply", "apply_interactive"]

logger = logging.getLogger(__name__)

PATH_PREFIX = "camino"

# prefix -> the name the context line gives the id.
_CONTEXT_FIELDS: dict[str, str] = {
    "producto": "producto_id",
    "categoria": "categoria_id",
    "promo": "promocion_id",
    "accion": "accion",
}

# WhatsApp caps an id at 200 characters; ours are keys and UUIDs.
_VALUE = re.compile(r"^[A-Za-z0-9_.-]{1,64}$")
_MAX_TITLE = 100


@dataclass(frozen=True)
class InteractiveOutcome:
    """What a tap changes."""

    # Stored facts to merge (the `facts_patch` of `StateRepo.save`).
    facts_patch: dict[str, Any]
    # One line for "ESTE TURNO", or None when the tap needs no explaining.
    context: str | None = None


def _clean_title(title: str) -> str:
    """The title as one short line: it ends up inside the prompt."""
    return " ".join(title.split())[:_MAX_TITLE]


def apply_interactive(
    reply: InteractiveReply, facts: Facts, options: EffectiveOptions
) -> InteractiveOutcome:
    """Turn a tap into a fact (`camino:`) or a context line (the rest).

    `facts` is only read to skip a pointless write when the customer taps the
    path they are already on.
    """
    prefix, separator, value = reply.id.partition(":")
    value = value.strip()
    title = _clean_title(reply.title)

    if not separator or not _VALUE.match(value):
        # Not one of ours (or malformed): tell the model what the customer chose
        # by its title, and write nothing.
        logger.warning("interactive reply with an unusable id", extra={"prefix": prefix[:20]})
        return InteractiveOutcome({}, f"El cliente eligió: {title}" if title else None)

    if prefix == PATH_PREFIX:
        if options.camino(value) is None:
            logger.warning("interactive reply names an unknown path", extra={"camino": value})
            return InteractiveOutcome({})
        if facts.camino == value:
            return InteractiveOutcome({})
        return InteractiveOutcome({"camino": value})

    field = _CONTEXT_FIELDS.get(prefix)
    if field is None:
        logger.warning("interactive reply with an unknown prefix", extra={"prefix": prefix[:20]})
        return InteractiveOutcome({}, f"El cliente eligió: {title}" if title else None)
    if prefix == "producto":
        # A tapped product is a choice, not a question: showing the same options
        # again was the observed failure (Milestone B run).
        return InteractiveOutcome(
            {},
            f"El cliente eligió: {title} ({field}={value}). Ya lo eligió: no vuelvas a mostrar "
            "las opciones; agrégalo con gestionar_pedido (accion agregar, ese producto_id) y, "
            "si es un paquete, pregúntale sus temas.",
        )
    return InteractiveOutcome({}, f"El cliente eligió: {title} ({field}={value})")
