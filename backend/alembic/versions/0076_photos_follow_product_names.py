"""Product photos that still carry a product's old name (2026-10-29).

Revision ID: 0076
Revises: 0075

A product photo gets its key, title and «when to send it» from the product's name when it is
uploaded (`services/product_photos.py`). Renaming the product did not update them, so the assistant
kept reading the old name: Giannier renamed «Agua de Mesa de 500 ml» to «… 625 ml» and its photo
still said «Cuando quiera ver Agua de Mesa de 500 ml». Renames now carry over (`follow_rename`);
this brings the photos renamed before that up to date. Only texts Leo wrote from a name change; one
the business wrote itself is left as it is.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0076"
down_revision: str | Sequence[str] | None = "0075"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_CLAVE_ROOM = 40 - len("foto__99")
_TITLE = re.compile(r"^Foto de (.+)$", re.S)
_WHEN = re.compile(r"^Cuando quiera ver (.+) o pida una foto$", re.S)
_KEY = re.compile(r"^foto_([a-z0-9_]+)_\d+$")
_FILLER = {"de", "del", "la", "el", "los", "las", "y", "con", "en"}


def _slug(name: str) -> str:
    """As `services/product_photos._slug` on 2026-10-29 (a migration does not import app code)."""
    plain = unicodedata.normalize("NFKD", name.casefold())
    plain = "".join(c for c in plain if not unicodedata.combining(c))
    return re.sub(r"[^a-z0-9]+", "_", plain).strip("_")[:_CLAVE_ROOM].strip("_") or "producto"


def _words(text: str) -> set[str]:
    return set(_slug(text).split("_")) - _FILLER


def _was_its_name(said: str, name: str) -> bool:
    """Whether a text names an earlier version of the product («Agua de Mesa de 500 ml» for
    «… 625 ml»), not something the business wrote («la botella»): half the words in common."""
    before, now = _words(said), _words(name)
    return bool(before and now) and len(before & now) * 2 >= max(len(before), len(now))


def upgrade() -> None:
    bind = op.get_bind()
    rows = bind.execute(
        sa.text(
            "SELECT m.id, m.tenant_id, m.clave, m.titulo, m.cuando_usar, p.name "
            "FROM media_assets m JOIN products p ON p.id = m.product_id "
            "WHERE m.tipo = 'imagen' ORDER BY m.tenant_id, m.orden, m.created_at"
        )
    ).all()
    taken: dict[object, set[str]] = {}
    for row in bind.execute(sa.text("SELECT tenant_id, clave FROM media_assets")).all():
        taken.setdefault(row.tenant_id, set()).add(row.clave)

    for row in rows:
        name = " ".join(row.name.split())
        title = f"Foto de {name}"[:120]
        when = f"Cuando quiera ver {name} o pida una foto"[:300]
        said_title = _TITLE.match(row.titulo or "")
        new_title = (
            title
            if said_title and row.titulo != title and _was_its_name(said_title.group(1), name)
            else row.titulo
        )
        said_when = _WHEN.match(row.cuando_usar or "")
        new_when = (
            when
            if said_when and row.cuando_usar != when and _was_its_name(said_when.group(1), name)
            else row.cuando_usar
        )
        new_key = row.clave
        base = f"foto_{_slug(name)}"
        said_key = _KEY.match(row.clave)
        if (
            said_key
            and not re.fullmatch(re.escape(base) + r"_\d+", row.clave)
            and _was_its_name(said_key.group(1).replace("_", " "), name)
        ):
            used = taken.setdefault(row.tenant_id, set())
            free = next((f"{base}_{n}" for n in range(1, 100) if f"{base}_{n}" not in used), None)
            if free is not None:
                used.discard(row.clave)
                used.add(free)
                new_key = free
        if (new_key, new_title, new_when) != (row.clave, row.titulo, row.cuando_usar):
            bind.execute(
                sa.text(
                    "UPDATE media_assets SET clave = :k, titulo = :t, cuando_usar = :w "
                    "WHERE id = :id"
                ),
                {"k": new_key, "t": new_title, "w": new_when, "id": row.id},
            )


def downgrade() -> None:
    """Nothing to undo: the old texts named a product that no longer exists under that name."""
