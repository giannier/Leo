"""The rubro learns: situations that several businesses wrote alike (017, phase 4, RF-023).

The platform operator sees, per rubro, the situations that at least two different businesses
wrote with the same meaning, and may offer one as a suggested situation of the next version of
the rubro. Three guards, because this is the only place where text crosses from one business to
others:

* **Only what a business allowed**: `learning_settings.compartir_con_rubro`, off by default,
  switched on by the owner knowing what it means.
* **Only the rule**: the text of the card (when it applies, what it does), never a conversation,
  an example or a customer; and a card the personal-data rules still find something in is
  flagged and cannot be offered.
* **A person at each end**: the operator reads and adopts it as a *draft*; it still goes through
  the rubro's evals before it is published, like any change.

This module reads across businesses on purpose (the way the trial jobs do) and nothing else here
does.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.learning.anonymizer import redact

# Two "cuándo aplica" this close (cosine distance) say the same thing.
SAME_MEANING = 0.2
MIN_BUSINESSES = 2
MAX_ROWS = 400
MAX_VARIANTS = 3


@dataclass
class Candidate:
    titulo: str
    cuando_aplica: str
    que_hace: str
    negocios: int
    variantes: list[str] = field(default_factory=list)
    # The rules still find personal data in it: it cannot be offered to the rubro as it is.
    con_datos: bool = False


_ROWS = text(
    """
    SELECT s.id, s.tenant_id, s.titulo, s.cuando_aplica, s.que_hace, s.embedding_model
    FROM situations s
    JOIN learning_settings l ON l.tenant_id = s.tenant_id AND l.compartir_con_rubro
    JOIN business_profiles p ON p.tenant_id = s.tenant_id
    WHERE p.industry = :rubro
      AND s.estado = 'activa'
      AND s.origen IN ('manual', 'aprendida')
      AND s.embedding IS NOT NULL
      AND s.embedding_model = :model
    ORDER BY s.created_at DESC
    LIMIT :limit
    """
)
_PAIRS = text(
    """
    SELECT a.id AS a, b.id AS b
    FROM situations a
    JOIN situations b ON a.id < b.id AND a.tenant_id <> b.tenant_id
    WHERE a.id = ANY(:ids) AND b.id = ANY(:ids)
      AND (a.embedding <=> b.embedding) < :distance
    """
)


def candidates(session: Session, rubro: str, *, model: str) -> list[Candidate]:
    rows: list[Any] = list(
        session.execute(_ROWS, {"rubro": rubro, "model": model, "limit": MAX_ROWS}).all()
    )
    if len(rows) < MIN_BUSINESSES:
        return []
    parent: dict[uuid.UUID, uuid.UUID] = {row.id: row.id for row in rows}

    def find(item: uuid.UUID) -> uuid.UUID:
        while parent[item] != item:
            parent[item] = parent[parent[item]]
            item = parent[item]
        return item

    pairs = session.execute(_PAIRS, {"ids": list(parent), "distance": SAME_MEANING})
    for pair in pairs:
        parent[find(pair.a)] = find(pair.b)

    groups: dict[uuid.UUID, list[Any]] = {}
    for row in rows:
        groups.setdefault(find(row.id), []).append(row)
    found: list[Candidate] = []
    for members in groups.values():
        tenants = {m.tenant_id for m in members}
        if len(tenants) < MIN_BUSINESSES:
            continue
        first = members[0]
        body = f"{first.titulo} {first.cuando_aplica} {first.que_hace}"
        found.append(
            Candidate(
                titulo=first.titulo,
                cuando_aplica=first.cuando_aplica,
                que_hace=first.que_hace,
                negocios=len(tenants),
                variantes=[m.que_hace for m in members[1 : 1 + MAX_VARIANTS]],
                con_datos=redact(body) != body,
            )
        )
    return sorted(found, key=lambda c: c.negocios, reverse=True)
