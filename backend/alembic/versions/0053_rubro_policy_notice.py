"""A policy notice per rubro (feature 019).

Revision ID: 0053
Revises: 0052

Some rubros sell things the WhatsApp Business Messaging Policy (section 4, last updated 2026-09-23) prohibits or
restricts, and it asks businesses not to request sensitive identifiers in a chat (section 3). The setup shows the
notice of the business's rubro next to what the agent does, so nobody finds out from a blocked number. It is
platform data (a column of `rubros`), not a rule of the agent: it informs the owner, the prompt is unchanged.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0053"
down_revision: str | Sequence[str] | None = "0052"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SENSITIVE = "No pidas por el chat números de tarjeta, de cuenta ni de documento de identidad: la política de WhatsApp lo prohíbe."
NOTICES = {
    "salud": (
        "La política de WhatsApp prohíbe vender productos médicos y de salud por el chat y pide no enviar ni "
        "pedir información de salud cuando la regulación lo restringe. Tu agente atiende horarios, citas y "
        "logística, no da indicaciones clínicas y pasa esos casos a una persona."
    ),
    "retail": (
        "La política de WhatsApp prohíbe vender armas, alcohol, tabaco, drogas, productos médicos y juegos de "
        "azar con dinero real por el chat; el alcohol y los medicamentos sin receta tienen excepciones por país, "
        "con licencia y solo para mayores de 18. Si vendes alguno, revisa si aplica a tu caso antes de activar la venta."
    ),
    "restaurante": (
        "La política de WhatsApp prohíbe vender alcohol y tabaco por el chat, con excepciones por país, con "
        "licencia y solo para mayores de 18. Si tu carta incluye alcohol, revisa si aplica a tu caso."
    ),
    "inmobiliaria": (
        f"{SENSITIVE} Los créditos y las tasas los explica un asesor: tu agente no los ofrece ni los promete."
    ),
    "automotriz": (
        f"{SENSITIVE} El financiamiento y la tasación de un auto en parte de pago los explica un asesor: tu "
        "agente no da cifras ni promete aprobaciones."
    ),
    "servicios": f"{SENSITIVE} Para eso, usa una reunión o un medio seguro.",
}


def upgrade() -> None:
    op.add_column("rubros", sa.Column("aviso_politica", sa.Text(), nullable=True))
    bind = op.get_bind()
    for key, notice in NOTICES.items():
        bind.execute(
            sa.text("UPDATE rubros SET aviso_politica = :n WHERE key = :k"), {"n": notice, "k": key}
        )


def downgrade() -> None:
    op.drop_column("rubros", "aviso_politica")
