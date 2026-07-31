"""Application settings loaded from environment variables.

No secrets are committed: every default here is safe for local development
only. Production values must be supplied via the real environment (or a
non-committed `.env`), never hardcoded.
"""

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

Environment = Literal["development", "staging", "production", "test"]

# Which upstream the platform's own LLM calls go to. Both are hosted APIs
# speaking the same protocol; neither runs a model on our infrastructure.
LLMProfile = Literal["nvidia", "openai"]

# backend/app/core/config.py -> backend/app/core -> backend/app -> backend -> repo root
_REPO_ROOT = Path(__file__).resolve().parents[3]

# Absolute, so the same `.env` is found no matter the working directory.
# A relative "env_file" resolves against the CWD, which silently returns the
# hardcoded defaults when Alembic runs from `backend/` instead of the repo
# root - producing a confusing authentication failure rather than an error.
ENV_FILE = _REPO_ROOT / ".env"


class Settings(BaseSettings):
    """Typed application configuration.

    Values are read from environment variables (case-insensitive) or a
    local `.env` file. See `.env.example` at the repo root for the full
    list of supported variables and their safe development defaults.
    """

    model_config = SettingsConfigDict(
        env_file=ENV_FILE,
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # General
    app_name: str = "Leo API"
    environment: Environment = "development"
    debug: bool = False
    api_v1_prefix: str = "/api/v1"
    log_level: str = "INFO"

    # Database (SQLAlchemy 2.x + psycopg 3 driver)
    database_url: str = Field(
        default="postgresql+psycopg://leo:leo@localhost:5432/leo",
        description="SQLAlchemy connection URL for PostgreSQL 16 + pgvector.",
    )

    # Redis (cache + ARQ job queue)
    redis_url: str = "redis://localhost:6379/0"

    # --- LLM providers ----------------------------------------------------
    # Which profile the platform itself uses. Tenants may override the *chat*
    # side with their own paid credentials; see `providers/llm/factory.py`.
    llm_profile: LLMProfile = "nvidia"

    # NVIDIA Build - free tier, OpenAI-compatible endpoint.
    nvidia_api_key: str | None = None
    nvidia_base_url: str = "https://integrate.api.nvidia.com/v1"
    nvidia_chat_model: str = "nvidia/nemotron-3-super-120b-a12b"
    nvidia_embedding_model: str = "nvidia/nv-embedqa-e5-v5"

    # OpenAI - paid.
    openai_api_key: str | None = None
    openai_base_url: str = "https://api.openai.com/v1"
    openai_chat_model: str = "gpt-5.4-mini"
    openai_embedding_model: str = "text-embedding-3-small"

    # Fernet key for credentials stored in the database (tenant API keys,
    # WhatsApp provider tokens). Generate with:
    #   python -c "from cryptography.fernet import Fernet; \
    #              print(Fernet.generate_key().decode())"
    credentials_encryption_key: str | None = None

    # Auth (JWT access + refresh)
    secret_key: str = "dev-secret-key-change-me"
    jwt_algorithm: str = "HS256"
    access_token_expire_minutes: int = 15
    refresh_token_expire_days: int = 30

    # Object storage (S3-compatible / MinIO in dev)
    s3_endpoint_url: str | None = "http://localhost:9000"
    s3_access_key: str = "leo-minio"
    s3_secret_key: str = "leo-minio-secret"
    s3_bucket: str = "leo-storage"
    s3_region: str = "us-east-1"

    # Transactional email (Resend)
    resend_api_key: str | None = None
    email_from: str = "Leo <onboarding@example.com>"

    # Public base URL of the tenant panel. Verification links in transactional
    # email are built from this, so in production it must be the real HTTPS
    # origin - a wrong value produces links that silently go nowhere.
    frontend_base_url: str = "http://localhost:3000"

    # Lifetime of an email verification link (data-model.md: 24h).
    email_verification_token_ttl_hours: int = 24

    # CORS
    cors_origins: list[str] = ["http://localhost:3000"]


@lru_cache
def get_settings() -> Settings:
    """Return a cached `Settings` instance.

    Cached so environment parsing happens once per process; tests can
    bypass this by constructing `Settings(...)` directly.
    """
    return Settings()
