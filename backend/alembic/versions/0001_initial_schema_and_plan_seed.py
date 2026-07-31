"""Initial schema and plan seed.

Creates every table in `leo-sdd/features/001-onboarding/data-model.md`, the
two required PostgreSQL extensions, and the four subscription plans.

Revision ID: 0001
Revises:
Create Date: 2026-07-30 20:42:36.359186

"""

from collections.abc import Sequence

# Autogenerate renders the embedding column as
# `pgvector.sqlalchemy.vector.VECTOR(...)` but does not emit this import.
# Without it the migration raises NameError on the knowledge_chunks table.
import pgvector.sqlalchemy
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "0001"
down_revision: str | Sequence[str] | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create extensions, then every table, then seed the plans."""
    # Extensions first: `users.email` is CITEXT and `knowledge_chunks.embedding`
    # is VECTOR, so both types must exist before any table referencing them.
    # `gen_random_uuid()` is built into PostgreSQL 13+, so pgcrypto is not
    # needed despite the data model listing it as optional.
    op.execute("CREATE EXTENSION IF NOT EXISTS citext")
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")

    op.create_table(
        "plans",
        sa.Column("id", sa.Text(), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("max_seats", sa.Integer(), nullable=False),
        sa.Column("max_ai_messages", sa.Integer(), nullable=False),
        sa.Column("max_rag_chars", sa.BigInteger(), nullable=False),
        sa.Column("max_knowledge_files", sa.Integer(), nullable=False),
        sa.Column("trial_days", sa.Integer(), nullable=True),
        sa.Column("price_usd_month", sa.Numeric(precision=8, scale=2), nullable=True),
        sa.Column("is_active", sa.Boolean(), server_default="true", nullable=False),
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
        sa.PrimaryKeyConstraint("id", name=op.f("pk_plans")),
    )
    op.create_table(
        "users",
        sa.Column("email", postgresql.CITEXT(), nullable=False),
        sa.Column("password_hash", sa.Text(), nullable=False),
        sa.Column("full_name", sa.Text(), nullable=False),
        sa.Column("phone_e164", sa.Text(), nullable=True),
        sa.Column("phone_verified_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("email_verified_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("is_platform_admin", sa.Boolean(), server_default="false", nullable=False),
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
        sa.PrimaryKeyConstraint("id", name=op.f("pk_users")),
        sa.UniqueConstraint("email", name=op.f("uq_users_email")),
    )
    op.create_table(
        "email_tokens",
        sa.Column("user_id", sa.UUID(), nullable=False),
        sa.Column("purpose", sa.Text(), nullable=False),
        sa.Column("token_hash", sa.Text(), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("consumed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "purpose IN ('verify_email', 'reset_password')",
            name=op.f("ck_email_tokens_email_tokens_purpose"),
        ),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"], name=op.f("fk_email_tokens_user_id_users")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_email_tokens")),
        sa.UniqueConstraint("token_hash", name=op.f("uq_email_tokens_token_hash")),
    )
    op.create_table(
        "otp_codes",
        sa.Column("user_id", sa.UUID(), nullable=False),
        sa.Column("purpose", sa.Text(), nullable=False),
        sa.Column("code_hash", sa.Text(), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("consumed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("attempts", sa.Integer(), server_default="0", nullable=False),
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "purpose IN ('phone_verify')", name=op.f("ck_otp_codes_otp_codes_purpose")
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], name=op.f("fk_otp_codes_user_id_users")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_otp_codes")),
    )
    op.create_table(
        "refresh_tokens",
        sa.Column("user_id", sa.UUID(), nullable=False),
        sa.Column("token_hash", sa.Text(), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"], name=op.f("fk_refresh_tokens_user_id_users")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_refresh_tokens")),
        sa.UniqueConstraint("token_hash", name=op.f("uq_refresh_tokens_token_hash")),
    )
    op.create_index(op.f("ix_refresh_tokens_user_id"), "refresh_tokens", ["user_id"], unique=False)
    op.create_table(
        "tenants",
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), server_default="trial", nullable=False),
        sa.Column("plan_id", sa.Text(), server_default="trial", nullable=False),
        sa.Column("trial_ends_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("ai_messages_used", sa.Integer(), server_default="0", nullable=False),
        sa.Column("paused_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("archived_at", sa.DateTime(timezone=True), nullable=True),
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
            "status IN ('trial', 'active', 'paused', 'archived')",
            name=op.f("ck_tenants_tenants_status"),
        ),
        sa.ForeignKeyConstraint(["plan_id"], ["plans.id"], name=op.f("fk_tenants_plan_id_plans")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_tenants")),
    )
    op.create_table(
        "agent_configs",
        sa.Column("tenant_id", sa.UUID(), nullable=False),
        sa.Column("agent_name", sa.Text(), server_default="Leo", nullable=False),
        sa.Column("tone", sa.Text(), server_default="cercano", nullable=False),
        sa.Column("objective", sa.Text(), server_default="ambos", nullable=False),
        sa.Column("restrictions", sa.Text(), nullable=True),
        sa.Column(
            "escalation_rules",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default='["cliente_molesto", "pide_humano", "fuera_de_catalogo"]',
            nullable=False,
        ),
        sa.Column("farewell_on_pause", sa.Text(), nullable=True),
        sa.Column("is_active", sa.Boolean(), server_default="false", nullable=False),
        sa.Column("activated_first_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "objective IN ('vender', 'atender', 'ambos')",
            name=op.f("ck_agent_configs_agent_configs_objective"),
        ),
        sa.CheckConstraint(
            "tone IN ('profesional', 'cercano', 'juvenil')",
            name=op.f("ck_agent_configs_agent_configs_tone"),
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"], ["tenants.id"], name=op.f("fk_agent_configs_tenant_id_tenants")
        ),
        sa.PrimaryKeyConstraint("tenant_id", name=op.f("pk_agent_configs")),
    )
    op.create_table(
        "audit_logs",
        sa.Column("actor_id", sa.UUID(), nullable=False),
        sa.Column("tenant_id", sa.UUID(), nullable=True),
        sa.Column("action", sa.Text(), nullable=False),
        sa.Column(
            "detail", postgresql.JSONB(astext_type=sa.Text()), server_default="{}", nullable=False
        ),
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["actor_id"], ["users.id"], name=op.f("fk_audit_logs_actor_id_users")
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"], ["tenants.id"], name=op.f("fk_audit_logs_tenant_id_tenants")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_audit_logs")),
    )
    op.create_index(
        "ix_audit_logs_tenant_id_created_at",
        "audit_logs",
        ["tenant_id", "created_at"],
        unique=False,
    )
    op.create_table(
        "business_profiles",
        sa.Column("tenant_id", sa.UUID(), nullable=False),
        sa.Column("business_name", sa.Text(), nullable=True),
        sa.Column("industry", sa.Text(), nullable=True),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("website", sa.Text(), nullable=True),
        sa.Column(
            "social_links",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default="{}",
            nullable=False,
        ),
        sa.Column("currency", sa.Text(), server_default="PEN", nullable=False),
        sa.Column("timezone", sa.Text(), server_default="America/Lima", nullable=False),
        sa.Column(
            "opening_hours",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default="{}",
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "industry IN ('restaurante', 'instituto', 'retail', 'servicios', 'salud', 'otros')",
            name=op.f("ck_business_profiles_business_profiles_industry"),
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"], ["tenants.id"], name=op.f("fk_business_profiles_tenant_id_tenants")
        ),
        sa.PrimaryKeyConstraint("tenant_id", name=op.f("pk_business_profiles")),
    )
    op.create_table(
        "connection_requests",
        sa.Column("tenant_id", sa.UUID(), nullable=False),
        sa.Column("phone_e164", sa.Text(), nullable=False),
        sa.Column("display_name", sa.Text(), nullable=False),
        sa.Column("profile_photo_key", sa.Text(), nullable=True),
        sa.Column("business_description", sa.Text(), nullable=True),
        sa.Column("status", sa.Text(), server_default="requested", nullable=False),
        sa.Column("rejection_reason", sa.Text(), nullable=True),
        sa.Column("admin_notes", sa.Text(), nullable=True),
        sa.Column("previous_request_id", sa.UUID(), nullable=True),
        sa.Column("processed_by", sa.UUID(), nullable=True),
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
            "status IN ('requested', 'in_progress', 'connected', 'rejected')",
            name=op.f("ck_connection_requests_connection_requests_status"),
        ),
        sa.ForeignKeyConstraint(
            ["previous_request_id"],
            ["connection_requests.id"],
            name=op.f("fk_connection_requests_previous_request_id_connection_requests"),
        ),
        sa.ForeignKeyConstraint(
            ["processed_by"], ["users.id"], name=op.f("fk_connection_requests_processed_by_users")
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"], ["tenants.id"], name=op.f("fk_connection_requests_tenant_id_tenants")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_connection_requests")),
    )
    op.create_index(
        "ix_connection_requests_status", "connection_requests", ["status"], unique=False
    )
    op.create_index(
        "uq_connection_requests_open_phone",
        "connection_requests",
        ["phone_e164"],
        unique=True,
        postgresql_where=sa.text("status IN ('requested', 'in_progress')"),
    )
    op.create_table(
        "conversations",
        sa.Column("tenant_id", sa.UUID(), nullable=False),
        sa.Column("channel", sa.Text(), nullable=False),
        sa.Column("contact_id", sa.UUID(), nullable=True),
        sa.Column("started_by", sa.UUID(), nullable=True),
        sa.Column("status", sa.Text(), server_default="open", nullable=False),
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
            "channel IN ('sandbox', 'whatsapp')",
            name=op.f("ck_conversations_conversations_channel"),
        ),
        sa.CheckConstraint(
            "status IN ('open', 'closed')", name=op.f("ck_conversations_conversations_status")
        ),
        sa.ForeignKeyConstraint(
            ["started_by"], ["users.id"], name=op.f("fk_conversations_started_by_users")
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"], ["tenants.id"], name=op.f("fk_conversations_tenant_id_tenants")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_conversations")),
    )
    op.create_index(
        "ix_conversations_tenant_id_channel",
        "conversations",
        ["tenant_id", "channel"],
        unique=False,
    )
    op.create_table(
        "invitations",
        sa.Column("tenant_id", sa.UUID(), nullable=False),
        sa.Column("email", postgresql.CITEXT(), nullable=False),
        sa.Column("role", sa.Text(), nullable=False),
        sa.Column("token_hash", sa.Text(), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("accepted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("invited_by", sa.UUID(), nullable=False),
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "role IN ('tenant_admin', 'operator')", name=op.f("ck_invitations_invitations_role")
        ),
        sa.ForeignKeyConstraint(
            ["invited_by"], ["users.id"], name=op.f("fk_invitations_invited_by_users")
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"], ["tenants.id"], name=op.f("fk_invitations_tenant_id_tenants")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_invitations")),
        sa.UniqueConstraint("token_hash", name=op.f("uq_invitations_token_hash")),
    )
    op.create_index(op.f("ix_invitations_tenant_id"), "invitations", ["tenant_id"], unique=False)
    op.create_table(
        "knowledge_sources",
        sa.Column("tenant_id", sa.UUID(), nullable=False),
        sa.Column("type", sa.Text(), nullable=False),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("content", sa.Text(), nullable=True),
        sa.Column("file_key", sa.Text(), nullable=True),
        sa.Column("char_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("status", sa.Text(), server_default="pending", nullable=False),
        sa.Column("error_msg", sa.Text(), nullable=True),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
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
            "status IN ('pending', 'indexing', 'indexed', 'failed')",
            name=op.f("ck_knowledge_sources_knowledge_sources_status"),
        ),
        sa.CheckConstraint(
            "type IN ('faq', 'policy', 'pdf', 'text')",
            name=op.f("ck_knowledge_sources_knowledge_sources_type"),
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"], ["tenants.id"], name=op.f("fk_knowledge_sources_tenant_id_tenants")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_knowledge_sources")),
    )
    op.create_index(
        "ix_knowledge_sources_tenant_id_active",
        "knowledge_sources",
        ["tenant_id"],
        unique=False,
        postgresql_where=sa.text("deleted_at IS NULL"),
    )
    op.create_table(
        "payment_methods_business",
        sa.Column("tenant_id", sa.UUID(), nullable=False),
        sa.Column("method", sa.Text(), nullable=False),
        sa.Column("instructions", sa.Text(), nullable=False),
        sa.Column("validate_receipts", sa.Boolean(), server_default="false", nullable=False),
        sa.Column("is_active", sa.Boolean(), server_default="true", nullable=False),
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
            "method IN ('yape', 'plin', 'transferencia', 'tarjeta', 'efectivo', 'link')",
            name=op.f("ck_payment_methods_business_payment_methods_business_method"),
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
            name=op.f("fk_payment_methods_business_tenant_id_tenants"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_payment_methods_business")),
        sa.UniqueConstraint(
            "tenant_id", "method", name="uq_payment_methods_business_tenant_id_method"
        ),
    )
    op.create_table(
        "products",
        sa.Column("tenant_id", sa.UUID(), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("price", sa.Numeric(precision=12, scale=2), nullable=False),
        sa.Column("stock", sa.Integer(), nullable=True),
        sa.Column("category", sa.Text(), nullable=True),
        sa.Column("image_key", sa.Text(), nullable=True),
        sa.Column("file_key", sa.Text(), nullable=True),
        sa.Column("is_active", sa.Boolean(), server_default="true", nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
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
            "price >= 0", name=op.f("ck_products_ck_products_products_price_non_negative")
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"], ["tenants.id"], name=op.f("fk_products_tenant_id_tenants")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_products")),
    )
    op.create_index(
        "ix_products_tenant_id_active",
        "products",
        ["tenant_id"],
        unique=False,
        postgresql_where=sa.text("deleted_at IS NULL"),
    )
    op.create_table(
        "tenant_users",
        sa.Column("tenant_id", sa.UUID(), nullable=False),
        sa.Column("user_id", sa.UUID(), nullable=False),
        sa.Column("role", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), server_default="active", nullable=False),
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
            "role IN ('owner', 'tenant_admin', 'operator')",
            name=op.f("ck_tenant_users_tenant_users_role"),
        ),
        sa.CheckConstraint(
            "status IN ('active', 'disabled')", name=op.f("ck_tenant_users_tenant_users_status")
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"], ["tenants.id"], name=op.f("fk_tenant_users_tenant_id_tenants")
        ),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"], name=op.f("fk_tenant_users_user_id_users")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_tenant_users")),
        sa.UniqueConstraint("tenant_id", "user_id", name="uq_tenant_users_tenant_id_user_id"),
    )
    op.create_index(op.f("ix_tenant_users_tenant_id"), "tenant_users", ["tenant_id"], unique=False)
    op.create_table(
        "trial_phone_locks",
        sa.Column("phone_e164", sa.Text(), nullable=False),
        sa.Column("tenant_id", sa.UUID(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
            name=op.f("fk_trial_phone_locks_tenant_id_tenants"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("phone_e164", name=op.f("pk_trial_phone_locks")),
    )
    op.create_table(
        "whatsapp_numbers",
        sa.Column("tenant_id", sa.UUID(), nullable=False),
        sa.Column("phone_e164", sa.Text(), nullable=False),
        sa.Column("display_name", sa.Text(), nullable=False),
        sa.Column("provider", sa.Text(), server_default="ycloud", nullable=False),
        sa.Column("credentials_enc", sa.LargeBinary(), nullable=True),
        sa.Column("status", sa.Text(), server_default="connected", nullable=False),
        sa.Column("quality_rating", sa.Text(), nullable=True),
        sa.Column(
            "connected_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
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
            "provider IN ('ycloud', 'meta')",
            name=op.f("ck_whatsapp_numbers_whatsapp_numbers_provider"),
        ),
        sa.CheckConstraint(
            "status IN ('connected', 'disconnected')",
            name=op.f("ck_whatsapp_numbers_whatsapp_numbers_status"),
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"], ["tenants.id"], name=op.f("fk_whatsapp_numbers_tenant_id_tenants")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_whatsapp_numbers")),
        sa.UniqueConstraint("phone_e164", name=op.f("uq_whatsapp_numbers_phone_e164")),
    )
    op.create_table(
        "knowledge_chunks",
        sa.Column("tenant_id", sa.UUID(), nullable=False),
        sa.Column("source_id", sa.UUID(), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("embedding", pgvector.sqlalchemy.vector.VECTOR(dim=1024), nullable=True),
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["source_id"],
            ["knowledge_sources.id"],
            name=op.f("fk_knowledge_chunks_source_id_knowledge_sources"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"], ["tenants.id"], name=op.f("fk_knowledge_chunks_tenant_id_tenants")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_knowledge_chunks")),
    )
    op.create_index(
        "ix_knowledge_chunks_embedding",
        "knowledge_chunks",
        ["embedding"],
        unique=False,
        postgresql_using="hnsw",
        postgresql_ops={"embedding": "vector_cosine_ops"},
    )
    op.create_index(
        op.f("ix_knowledge_chunks_tenant_id"), "knowledge_chunks", ["tenant_id"], unique=False
    )
    op.create_table(
        "messages",
        sa.Column("tenant_id", sa.UUID(), nullable=False),
        sa.Column("conversation_id", sa.UUID(), nullable=False),
        sa.Column("direction", sa.Text(), nullable=False),
        sa.Column("author", sa.Text(), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "author IN ('customer', 'agent', 'human_operator', 'system')",
            name=op.f("ck_messages_messages_author"),
        ),
        sa.CheckConstraint(
            "direction IN ('inbound', 'outbound')", name=op.f("ck_messages_messages_direction")
        ),
        sa.ForeignKeyConstraint(
            ["conversation_id"],
            ["conversations.id"],
            name=op.f("fk_messages_conversation_id_conversations"),
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"], ["tenants.id"], name=op.f("fk_messages_tenant_id_tenants")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_messages")),
    )
    op.create_index(
        "ix_messages_conversation_id_created_at",
        "messages",
        ["conversation_id", "created_at"],
        unique=False,
    )
    op.create_table(
        "agent_traces",
        sa.Column("tenant_id", sa.UUID(), nullable=False),
        sa.Column("message_id", sa.UUID(), nullable=False),
        sa.Column("model", sa.Text(), nullable=False),
        sa.Column(
            "tools_called",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default="[]",
            nullable=False,
        ),
        sa.Column(
            "rag_sources",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default="[]",
            nullable=False,
        ),
        sa.Column("input_tokens", sa.Integer(), server_default="0", nullable=False),
        sa.Column("output_tokens", sa.Integer(), server_default="0", nullable=False),
        sa.Column("latency_ms", sa.Integer(), nullable=True),
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["message_id"], ["messages.id"], name=op.f("fk_agent_traces_message_id_messages")
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"], ["tenants.id"], name=op.f("fk_agent_traces_tenant_id_tenants")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_agent_traces")),
    )
    op.create_index(
        "ix_agent_traces_tenant_id_created_at",
        "agent_traces",
        ["tenant_id", "created_at"],
        unique=False,
    )
    # ### end Alembic commands ###

    _seed_plans()


def _seed_plans() -> None:
    """Insert the four subscription tiers.

    `trial` is fully specified by Clarification 001/003/011 and is the only
    plan Feature 001 actually enforces.

    For the paid tiers, only `max_seats` (Clarification 003) and
    `max_rag_chars` (Clarification 011) are documented. `max_ai_messages` and
    `max_knowledge_files` are NOT NULL but have no documented value, so the
    numbers below are PROVISIONAL and scale with the documented RAG budget.
    They must be ratified before Feature 009 turns them into billing limits.

    `price_usd_month` is left NULL rather than guessed: the data model calls
    it a reference value and billing is out of scope until Feature 009.
    """
    plans = sa.table(
        "plans",
        sa.column("id", sa.Text),
        sa.column("name", sa.Text),
        sa.column("max_seats", sa.Integer),
        sa.column("max_ai_messages", sa.Integer),
        sa.column("max_rag_chars", sa.BigInteger),
        sa.column("max_knowledge_files", sa.Integer),
        sa.column("trial_days", sa.Integer),
        sa.column("price_usd_month", sa.Numeric(8, 2)),
    )
    op.bulk_insert(
        plans,
        [
            {
                # Clarification 001: 14 days, 250 AI messages, no credit card.
                # Clarification 003: 2 seats. Clarification 011: 500K chars, 20 files.
                "id": "trial",
                "name": "Trial",
                "max_seats": 2,
                "max_ai_messages": 250,
                "max_rag_chars": 500_000,
                "max_knowledge_files": 20,
                "trial_days": 14,
                "price_usd_month": None,
            },
            {
                "id": "emprende",
                "name": "Emprende",
                "max_seats": 2,  # documented
                "max_ai_messages": 1_000,  # PROVISIONAL
                "max_rag_chars": 1_000_000,  # documented
                "max_knowledge_files": 40,  # PROVISIONAL
                "trial_days": None,
                "price_usd_month": None,
            },
            {
                "id": "crece",
                "name": "Crece",
                "max_seats": 5,  # documented
                "max_ai_messages": 4_000,  # PROVISIONAL
                "max_rag_chars": 4_000_000,  # documented
                "max_knowledge_files": 160,  # PROVISIONAL
                "trial_days": None,
                "price_usd_month": None,
            },
            {
                "id": "pro",
                "name": "Pro",
                "max_seats": 10,  # documented
                "max_ai_messages": 8_000,  # PROVISIONAL
                "max_rag_chars": 8_000_000,  # documented
                "max_knowledge_files": 320,  # PROVISIONAL
                "trial_days": None,
                "price_usd_month": None,
            },
        ],
    )


def downgrade() -> None:
    """Drop every table, then the extensions that only these tables needed."""
    # ### commands auto generated by Alembic - please adjust! ###
    op.drop_index("ix_agent_traces_tenant_id_created_at", table_name="agent_traces")
    op.drop_table("agent_traces")
    op.drop_index("ix_messages_conversation_id_created_at", table_name="messages")
    op.drop_table("messages")
    op.drop_index(op.f("ix_knowledge_chunks_tenant_id"), table_name="knowledge_chunks")
    op.drop_index(
        "ix_knowledge_chunks_embedding",
        table_name="knowledge_chunks",
        postgresql_using="hnsw",
        postgresql_ops={"embedding": "vector_cosine_ops"},
    )
    op.drop_table("knowledge_chunks")
    op.drop_table("whatsapp_numbers")
    op.drop_table("trial_phone_locks")
    op.drop_index(op.f("ix_tenant_users_tenant_id"), table_name="tenant_users")
    op.drop_table("tenant_users")
    op.drop_index(
        "ix_products_tenant_id_active",
        table_name="products",
        postgresql_where=sa.text("deleted_at IS NULL"),
    )
    op.drop_table("products")
    op.drop_table("payment_methods_business")
    op.drop_index(
        "ix_knowledge_sources_tenant_id_active",
        table_name="knowledge_sources",
        postgresql_where=sa.text("deleted_at IS NULL"),
    )
    op.drop_table("knowledge_sources")
    op.drop_index(op.f("ix_invitations_tenant_id"), table_name="invitations")
    op.drop_table("invitations")
    op.drop_index("ix_conversations_tenant_id_channel", table_name="conversations")
    op.drop_table("conversations")
    op.drop_index(
        "uq_connection_requests_open_phone",
        table_name="connection_requests",
        postgresql_where=sa.text("status IN ('requested', 'in_progress')"),
    )
    op.drop_index("ix_connection_requests_status", table_name="connection_requests")
    op.drop_table("connection_requests")
    op.drop_table("business_profiles")
    op.drop_index("ix_audit_logs_tenant_id_created_at", table_name="audit_logs")
    op.drop_table("audit_logs")
    op.drop_table("agent_configs")
    op.drop_table("tenants")
    op.drop_index(op.f("ix_refresh_tokens_user_id"), table_name="refresh_tokens")
    op.drop_table("refresh_tokens")
    op.drop_table("otp_codes")
    op.drop_table("email_tokens")
    op.drop_table("users")
    op.drop_table("plans")
    # ### end Alembic commands ###

    # Symmetric with upgrade(). Plain DROP EXTENSION (not CASCADE): if some
    # later object still depends on citext or vector, failing loudly is far
    # better than silently dropping that object along with the extension.
    op.execute("DROP EXTENSION IF EXISTS vector")
    op.execute("DROP EXTENSION IF EXISTS citext")
