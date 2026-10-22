"""Returns and the documents of an order (019, phase G).

Revision ID: 0061
Revises: 0060

* `order_returns` (+ `order_return_events`): a customer asks to return, exchange or claim the warranty of an order
  already made. The assistant registers it with what the customer said and the photos they sent; the business
  decides, because refunding is money. One open request of a kind per order.
* `order_documents`: the boleta, factura, guía or warranty of an order, apart from the media library so that a
  customer's invoice is never reachable by a key.
* Two notification kinds: a return was requested; a customer asked for a document that is not attached yet.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0061"
down_revision: str | Sequence[str] | None = "0060"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

KINDS_BEFORE = (
    "trial_ending_3d",
    "trial_ending_1d",
    "trial_ended",
    "wizard_reminder",
    "quota_80",
    "nuevo_comprobante",
    "pedido_contra_entrega",
    "derivacion",
    "ventana_cerrada",
    "llm_caido",
    "derivacion_sin_atender",
    "lead_caliente",
)
KINDS_AFTER = (*KINDS_BEFORE, "devolucion_solicitada", "documento_solicitado")
CONSTRAINT = "ck_tenant_notifications_tenant_notifications_kind"


def _in(kinds: Sequence[str]) -> str:
    return "kind IN (" + ", ".join(f"'{kind}'" for kind in kinds) + ")"


def upgrade() -> None:
    op.create_table(
        "order_documents",
        sa.Column("tenant_id", sa.UUID(), nullable=False),
        sa.Column("order_id", sa.UUID(), nullable=False),
        sa.Column("tipo", sa.Text(), nullable=False),
        sa.Column("titulo", sa.Text(), nullable=False),
        sa.Column("serie", sa.Text(), nullable=True),
        sa.Column("numero", sa.Text(), nullable=True),
        sa.Column("storage_key", sa.Text(), nullable=False),
        sa.Column("filename", sa.Text(), nullable=False),
        sa.Column("mime_type", sa.Text(), nullable=False),
        sa.Column("size_bytes", sa.Integer(), nullable=False),
        sa.Column("origen", sa.Text(), server_default="subido", nullable=False),
        sa.Column("estado", sa.Text(), server_default="emitido", nullable=False),
        sa.Column(
            "proveedor",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column("provider_media_id", sa.Text(), nullable=True),
        sa.Column("provider_media_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("subido_por", sa.UUID(), nullable=True),
        sa.Column("enviado_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "estado IN ('emitido', 'anulado')", name=op.f("ck_order_documents_estado")
        ),
        sa.CheckConstraint(
            "origen IN ('subido', 'generado', 'electronico')",
            name=op.f("ck_order_documents_origen"),
        ),
        sa.CheckConstraint(
            "tipo IN ('boleta', 'factura', 'guia', 'proforma', 'garantia', 'nota_credito', 'otro')",
            name=op.f("ck_order_documents_tipo"),
        ),
        sa.CheckConstraint("size_bytes >= 0", name=op.f("ck_order_documents_size_non_negative")),
        sa.ForeignKeyConstraint(
            ["order_id"], ["orders.id"], name=op.f("fk_order_documents_order_id_orders")
        ),
        sa.ForeignKeyConstraint(
            ["subido_por"],
            ["users.id"],
            name=op.f("fk_order_documents_subido_por_users"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"], ["tenants.id"], name=op.f("fk_order_documents_tenant_id_tenants")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_order_documents")),
    )
    op.create_index(
        "ix_order_documents_order_id", "order_documents", ["order_id", "created_at"], unique=False
    )
    op.create_index(
        "ix_order_documents_tenant_id_created_at",
        "order_documents",
        ["tenant_id", sa.literal_column("created_at DESC")],
        unique=False,
    )
    op.create_table(
        "order_returns",
        sa.Column("tenant_id", sa.UUID(), nullable=False),
        sa.Column("order_id", sa.UUID(), nullable=False),
        sa.Column("contact_id", sa.UUID(), nullable=False),
        sa.Column("conversation_id", sa.UUID(), nullable=True),
        sa.Column("numero", sa.Integer(), nullable=False),
        sa.Column("tipo", sa.Text(), nullable=False),
        sa.Column("motivo", sa.Text(), nullable=False),
        sa.Column("detalle", sa.Text(), server_default="", nullable=False),
        sa.Column(
            "items",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "evidencia",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
        sa.Column("estado", sa.Text(), server_default="solicitada", nullable=False),
        sa.Column("resolucion", sa.Text(), nullable=True),
        sa.Column("resuelta_por", sa.UUID(), nullable=True),
        sa.Column("resuelta_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_by", sa.Text(), nullable=False),
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "created_by IN ('agent', 'user')", name=op.f("ck_order_returns_created_by")
        ),
        sa.CheckConstraint(
            "estado IN ('solicitada', 'aprobada', 'rechazada', 'resuelta', 'cancelada')",
            name=op.f("ck_order_returns_estado"),
        ),
        sa.CheckConstraint(
            "motivo IN ('defectuoso', 'equivocado', 'incompleto', 'no_conforme', 'tardio', 'otro')",
            name=op.f("ck_order_returns_motivo"),
        ),
        sa.CheckConstraint(
            "tipo IN ('devolucion', 'cambio', 'garantia', 'reclamo')",
            name=op.f("ck_order_returns_tipo"),
        ),
        sa.CheckConstraint(
            "char_length(detalle) <= 1000", name=op.f("ck_order_returns_detalle_length")
        ),
        sa.CheckConstraint(
            "resolucion IS NULL OR char_length(resolucion) <= 500",
            name=op.f("ck_order_returns_resolucion_length"),
        ),
        sa.ForeignKeyConstraint(
            ["contact_id"], ["contacts.id"], name=op.f("fk_order_returns_contact_id_contacts")
        ),
        sa.ForeignKeyConstraint(
            ["conversation_id"],
            ["conversations.id"],
            name=op.f("fk_order_returns_conversation_id_conversations"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["order_id"], ["orders.id"], name=op.f("fk_order_returns_order_id_orders")
        ),
        sa.ForeignKeyConstraint(
            ["resuelta_por"],
            ["users.id"],
            name=op.f("fk_order_returns_resuelta_por_users"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"], ["tenants.id"], name=op.f("fk_order_returns_tenant_id_tenants")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_order_returns")),
        sa.UniqueConstraint("tenant_id", "numero", name="uq_order_returns_tenant_id_numero"),
    )
    op.create_index("ix_order_returns_order_id", "order_returns", ["order_id"], unique=False)
    op.create_index(
        "ix_order_returns_tenant_id_estado",
        "order_returns",
        ["tenant_id", "estado", sa.literal_column("created_at DESC")],
        unique=False,
    )
    op.create_index(
        "uq_order_returns_open_per_order",
        "order_returns",
        ["order_id", "tipo"],
        unique=True,
        postgresql_where=sa.text("estado IN ('solicitada', 'aprobada')"),
    )
    op.create_table(
        "order_return_events",
        sa.Column("tenant_id", sa.UUID(), nullable=False),
        sa.Column("return_id", sa.UUID(), nullable=False),
        sa.Column("desde", sa.Text(), nullable=True),
        sa.Column("hacia", sa.Text(), nullable=False),
        sa.Column("actor", sa.Text(), nullable=False),
        sa.Column("user_id", sa.UUID(), nullable=True),
        sa.Column(
            "detalle",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["return_id"],
            ["order_returns.id"],
            name=op.f("fk_order_return_events_return_id_order_returns"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"], ["tenants.id"], name=op.f("fk_order_return_events_tenant_id_tenants")
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name=op.f("fk_order_return_events_user_id_users"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_order_return_events")),
    )
    op.create_index(
        "ix_order_return_events_return_id",
        "order_return_events",
        ["return_id", "created_at"],
        unique=False,
    )
    op.drop_constraint(op.f(CONSTRAINT), "tenant_notifications", type_="check")
    op.create_check_constraint(op.f(CONSTRAINT), "tenant_notifications", _in(KINDS_AFTER))


def downgrade() -> None:
    op.execute(
        "DELETE FROM tenant_notifications "
        "WHERE kind IN ('devolucion_solicitada', 'documento_solicitado')"
    )
    op.drop_constraint(op.f(CONSTRAINT), "tenant_notifications", type_="check")
    op.create_check_constraint(op.f(CONSTRAINT), "tenant_notifications", _in(KINDS_BEFORE))
    op.drop_index("ix_order_return_events_return_id", table_name="order_return_events")
    op.drop_table("order_return_events")
    op.drop_index(
        "uq_order_returns_open_per_order",
        table_name="order_returns",
        postgresql_where=sa.text("estado IN ('solicitada', 'aprobada')"),
    )
    op.drop_index("ix_order_returns_tenant_id_estado", table_name="order_returns")
    op.drop_index("ix_order_returns_order_id", table_name="order_returns")
    op.drop_table("order_returns")
    op.drop_index("ix_order_documents_tenant_id_created_at", table_name="order_documents")
    op.drop_index("ix_order_documents_order_id", table_name="order_documents")
    op.drop_table("order_documents")
