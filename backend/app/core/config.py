"""Application settings loaded from environment variables.

No secrets are committed: every default here is safe for local development
only. Production values must be supplied via the real environment (or a
non-committed `.env`), never hardcoded.
"""

import json
from decimal import Decimal
from functools import lru_cache
from pathlib import Path
from typing import Annotated, Literal, Self

from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

Environment = Literal["development", "staging", "production", "test"]

# Environments where a real deployment is reachable by real users, so a
# placeholder secret or a plaintext cookie is a live vulnerability rather
# than a local convenience.
DEPLOYED_ENVIRONMENTS: frozenset[str] = frozenset({"staging", "production"})

# The committed placeholder. Signing tokens with a value everyone can read in
# the repository means anyone can mint an access token for any account.
#
# Long enough to clear the 32-byte floor below: HS256 keys shorter than the
# hash they feed weaken the MAC, and PyJWT warns about it on every call.
INSECURE_SECRET_KEY = "dev-secret-key-change-me-in-production"

# Any of these means "nobody has set a real key yet", whichever placeholder a
# given `.env` happens to be carrying. Historic values stay listed: an old
# `.env` promoted to production must still be refused.
PLACEHOLDER_SECRET_KEYS: frozenset[str] = frozenset(
    {"dev-secret-key-change-me", INSECURE_SECRET_KEY, "change-me", "secret"}
)

# The credential key the development compose file hands out, so a fresh
# clone can connect a number without generating anything. It is refused in a
# deployed environment: the whole value of encrypting credentials at rest is
# that the key is not in the repository, and a default that silently survived
# into production would hand every stored WhatsApp token to anyone who has
# read this file.
DEV_CREDENTIALS_KEY = "bGVvLWRldi1rZXktTk9ULWZvci1wcm9kdWN0aW9uISE="

# RFC 7518 §3.2: an HMAC key should be at least as long as the hash output.
MIN_SECRET_KEY_LENGTH = 32

# Which upstream the platform's own LLM calls go to: OpenAI, a hosted API (no
# model runs on our infrastructure), or the offline stand-in for development.
# The NVIDIA Build profile was removed in Milestone B at Giannier's request.
LLMProfile = Literal["openai", "offline"]

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

    # --- Platform billing --------------------------------------------------
    # Whether the platform charges its tenants: the trial that expires and
    # pauses the agent, the message quota and its warnings, the seat and
    # knowledge limits of the plan, one trial per phone. Built and tested, but
    # off by default while Giannier tests the product with his own businesses
    # (Milestone B of the 004). Usage is still metered either way. Turn it on
    # (`BILLING_ENFORCED=true`) the day Leo starts selling.
    billing_enforced: bool = False
    # How a business pays the platform while payments are collected by hand
    # (Feature 009, Giannier 2026-10-14): bank account, Yape number, holder.
    # Shown in the panel and in every billing email next to the reference.
    billing_payment_instructions: str = ""
    # Soles per dollar, shown only as a reference next to dollar prices (009
    # C-001: Leo does not fix exchange rates). Empty shows no soles at all.
    billing_pen_reference_rate: Decimal | None = None
    # Days before the period ends that the renewal charge is issued, and days of
    # grace after it (reminders on days 1, 3 and 5).
    billing_renewal_notice_days: int = 7
    billing_grace_days: int = 5

    # --- LLM providers ----------------------------------------------------
    # Which profile the platform itself uses. Tenants may override the *chat*
    # side with their own paid credentials; see `providers/llm/factory.py`.
    #
    # OpenAI rather than NVIDIA, since 8/9/2026. `nvidia/nv-embedqa-e5-v5`
    # reached end of life on 25/8/2026 and answers HTTP 410, so the previous
    # default could not index knowledge at all: a first deploy would come up
    # with retrieval dead and nothing saying why. Of the embedding models this
    # account can still reach on NVIDIA Build, only `nemotron-3-embed-1b`
    # responds, and it returns 2048 dimensions against a `Vector(1024)`
    # column - a schema migration and a full reindex, to keep a provider that
    # is not the one this platform actually runs on.
    llm_profile: LLMProfile = "openai"
    # Whether payment receipts may be sent to the platform model for reading
    # (hallazgo P-1 of the Milestone B review). Off: every receipt is stored
    # without data and the operator reads the photo. Only a profile whose vision
    # model was approved for personal data should run with it on.
    vision_enabled: bool = True

    # OpenAI - paid.
    openai_api_key: str | None = None
    openai_base_url: str = "https://api.openai.com/v1"
    openai_chat_model: str = "gpt-5.4-mini"
    openai_embedding_model: str = "text-embedding-3-small"
    # The model behind each intelligence level (TASK-445, C-02). Unset: the level
    # runs on `openai_chat_model`, so choosing a level changes nothing until the
    # platform decides what a better model is worth (Giannier ratifies: LLM cost).
    llm_model_economico: str | None = None
    llm_model_medio: str | None = None
    llm_model_mejor: str | None = None
    # How many credits one agent reply costs at each level (Milestone B, decision 4).
    # Charged by Feature 009; equal while every level runs the same model.
    llm_credits_economico: int = 1
    llm_credits_medio: int = 1
    llm_credits_mejor: int = 1

    # Fernet key for credentials stored in the database (tenant API keys,
    # WhatsApp provider tokens). Generate with:
    #   python -c "from cryptography.fernet import Fernet; \
    #              print(Fernet.generate_key().decode())"
    credentials_encryption_key: str | None = None

    # Auth (JWT access + refresh)
    secret_key: str = INSECURE_SECRET_KEY
    jwt_algorithm: str = "HS256"
    access_token_expire_minutes: int = 15
    refresh_token_expire_days: int = 30

    # Rate limit applied to every `/auth/*` endpoint, per client IP
    # (contracts/api.md documents both numbers).
    # Coarse per-IP guard on the endpoints that take a guessable secret.
    #
    # It was 5/min, matching the original contract, and that number assumed
    # one IP meant one person. The customers are small businesses, and a
    # small business is one wifi router: the owner and two assistants opening
    # the panel at eight in the morning are a single client to this counter,
    # and a mistyped password between them was enough to lock the rest out.
    #
    # The precise work is now done by `login_attempt_*` below, which counts
    # per account and does not care whose connection the attempt came from.
    # What is left for this one is stopping a single source from hammering
    # *many* accounts, and 20 a minute does that while leaving room for a
    # shop full of people.
    auth_rate_limit_requests: int = 20
    auth_rate_limit_window_seconds: int = 60

    # Failed sign-ins per account. The actual defence against somebody
    # guessing a password, and the reason the per-IP number above can be
    # relaxed: this one is immune to shared connections, because it counts
    # the account being attacked rather than the address attacking it.
    #
    # Ten in a quarter of an hour is far past normal - nobody mistypes their
    # own password ten times in fifteen minutes - and far below useful for an
    # attacker: 960 guesses a day against an Argon2 hash of a password that
    # must be at least ten characters.
    #
    # A success clears the counter, so honest fumbling never accumulates.
    login_attempt_limit: int = 10
    login_attempt_window_seconds: int = 900

    # Whether `X-Forwarded-For` may be believed when identifying the client.
    # False by default because a client can send that header itself: trusting
    # it without a reverse proxy in front turns every per-IP limit into a
    # per-header-value limit, which an attacker simply varies. Set it to true
    # only where a proxy we control (Caddy, in `deploy/`) rewrites the header.
    trust_proxy_headers: bool = False

    # Where uploaded files live. "postgres" keeps them in the database (nothing
    # else to run, one backup); "s3" uses any S3-compatible store (the S3_*
    # settings below). Both sit behind the same `StorageProvider` port.
    storage_backend: Literal["postgres", "s3"] = "postgres"
    # The largest single file the Postgres store accepts. 100 MB is the biggest
    # thing the media library allows (a PDF); everything else is smaller.
    storage_max_object_bytes: int = 100 * 1024 * 1024
    # Bytes one business may keep in the Postgres store. 0 = no limit, which is
    # the behaviour of the S3 store too; set it once there is a plan to enforce.
    storage_tenant_quota_bytes: int = 0
    # The address the API is reached at from *outside*: links to stored files are
    # built on it, and they are opened by a browser and downloaded by WhatsApp,
    # so it has to be absolute and reachable by both. In production it is the
    # site's own address (https://<LEO_DOMAIN>); locally the API's port.
    api_public_base_url: str = "http://localhost:8000"

    # Object storage, only read when STORAGE_BACKEND=s3 (any S3-compatible store).
    s3_endpoint_url: str | None = "http://localhost:9000"
    # Base URL a browser can reach the object store on. Signed links (receipt
    # photos, library files) are built with this host; empty means "same as
    # `s3_endpoint_url`". Needed whenever the backend reaches storage on an
    # in-network name (`minio:9000`) the browser cannot resolve.
    s3_public_endpoint_url: str | None = None
    s3_access_key: str = "leo-minio"
    s3_secret_key: str = "leo-minio-secret"
    s3_bucket: str = "leo-storage"
    s3_region: str = "us-east-1"

    # --- WhatsApp channel (Feature 002) -----------------------------------
    # There is no platform-wide webhook secret: every business brings its own
    # YCloud account, and the secret YCloud returns when Leo creates the webhook
    # there is kept encrypted with that number's credentials.
    # Base URL of YCloud's API. Overridable so a staging deployment can be
    # pointed at a recorded mock without touching code.
    ycloud_base_url: str = "https://api.ycloud.com/v2"

    # How long messages from one customer are collected before the agent is
    # asked to answer (RF-005). Somebody typing "hola" / "buenas" / "tienen
    # pan?" in three bursts is asking one question, and three separate
    # answers read like a broken robot and cost three model calls.
    channel_debounce_seconds: int = 8

    # Attempts to hand one reply to the provider before it is marked failed
    # (HU-02). Only retryable failures count - a rejected credential is not
    # retried at all.
    channel_send_max_attempts: int = 3

    # Sandbox (contracts/api.md: 30 messages per minute, per tenant)
    sandbox_rate_limit_requests: int = 30
    sandbox_rate_limit_window_seconds: int = 60

    # Phone OTP (contracts/api.md: 3 requests per hour)
    otp_request_limit: int = 3
    otp_request_window_seconds: int = 3600

    # Transactional email (Resend)
    resend_api_key: str | None = None
    email_from: str = "Leo <onboarding@example.com>"

    # Which provider to build. `auto` picks Resend when a key is present and
    # falls back to the console in development.
    #
    # `console` is an explicit override, and it exists because "has a key"
    # turned out to be the wrong question. A developer with a Resend key on a
    # sandbox account cannot send to anyone but themselves and cannot send
    # *from* an unverified domain - so registration fails at the one step that
    # gates entry to the panel, and `auto` never falls back because the key is
    # right there. Forcing `console` walks the whole flow offline.
    email_provider: Literal["auto", "console", "resend"] = "auto"

    # Where the console provider records what it "sent", as JSONL. Only read
    # in development - `ConsoleEmailProvider` refuses to construct anywhere
    # else. The end-to-end suite reads the verification link from here,
    # because the token is stored hashed and the message is the only place
    # its plaintext ever exists.
    email_outbox_path: str | None = None

    # Public base URL of the tenant panel. Verification links in transactional
    # email are built from this, so in production it must be the real HTTPS
    # origin - a wrong value produces links that silently go nowhere.
    frontend_base_url: str = "http://localhost:3000"

    # Lifetime of an email verification link (data-model.md: 24h).
    email_verification_token_ttl_hours: int = 24

    # --- Knowledge ingestion / RAG (TASK-012) -----------------------------
    # Per-file upload ceiling. Clarification 011 fixes it at 10 MB; it is not
    # a `plans` column because the clarification states it as one rule for
    # every plan. The *aggregate* budgets (`max_rag_chars`,
    # `max_knowledge_files`) do vary per plan and are read from that table -
    # never from here.
    knowledge_max_pdf_bytes: int = 10 * 1024 * 1024

    # Chunking. See `app/services/chunking.py` for why these are characters
    # and why these values stay inside the embedding model's token window.
    knowledge_chunk_chars: int = 1000
    knowledge_chunk_overlap_chars: int = 150

    # Chunks per embedding request. Bounds the payload and the blast radius
    # of a provider timeout; a 400-page manual would otherwise go up in one
    # request that either succeeds entirely or has to be redone entirely.
    knowledge_embed_batch_size: int = 32

    # Default `k` for `search_knowledge`.
    knowledge_search_default_k: int = 5

    # OCR of scanned PDF pages with the vision model (Feature 006, TASK-602).
    # A page whose text layer has fewer characters than `min_chars` is read by
    # the model; at most `max_pages` pages per document (the spec's trial limit,
    # applied always because it bounds model spend), `concurrency` at a time.
    knowledge_ocr_max_pages: int = 30
    knowledge_ocr_min_chars: int = 20
    knowledge_ocr_concurrency: int = 3
    knowledge_ocr_timeout_seconds: float = 90.0
    knowledge_ocr_max_tokens: int = 2500

    # Reading the business's website (Feature 006, TASK-603): pages per crawl,
    # seconds per request and bytes per page.
    knowledge_web_max_pages: int = 20
    knowledge_web_timeout_seconds: float = 10.0
    knowledge_web_max_page_bytes: int = 2_000_000

    # CORS
    # `NoDecode` turns off pydantic-settings' JSON decoding for this field.
    # Without it the environment source parses the value before any
    # validator runs, so the one below never sees the raw string.
    cors_origins: Annotated[list[str], NoDecode] = ["http://localhost:3000"]

    @field_validator("cors_origins", mode="before")
    @classmethod
    def _split_origins(cls, value: object) -> object:
        """Accept a comma-separated list, not only JSON.

        pydantic-settings parses a `list[str]` from the environment as JSON,
        so `CORS_ORIGINS=http://localhost:3000` raises at startup and
        `CORS_ORIGINS=["http://localhost:3000"]` is required instead. Nobody
        writes the second form by hand, `deploy/env.reference` documented the
        first, and the failure is a `SettingsError` during import - which
        looks like a broken build rather than a typo in a config file.

        JSON still works, so an existing deployment written that way keeps
        working.
        """
        if not isinstance(value, str):
            return value
        text = value.strip()
        if not text:
            return []
        if text.startswith("["):
            # Still accept the JSON form, so a deployment already written
            # that way keeps working.
            return json.loads(text)
        return [origin.strip() for origin in text.split(",") if origin.strip()]

    @property
    def is_deployed(self) -> bool:
        """Whether this process serves real users over the public internet.

        Drives the `Secure` flag on the refresh cookie: forcing it on in
        development would make the cookie unusable over plain-HTTP localhost,
        and leaving it off in production would put the refresh token on the
        wire in the clear.
        """
        return self.environment in DEPLOYED_ENVIRONMENTS

    @model_validator(mode="after")
    def _require_a_decision_about_proxy_headers(self) -> Self:
        """A deployed process must say, out loud, whether it sits behind a proxy.

        The default is `False`, and silently keeping it is the worst outcome
        available. `deploy/` puts Caddy in front, so every request reaches the
        application from Caddy's address: with the header untrusted, the
        per-IP rate limiter sees one client, and the entire customer base -
        every shop in the country - shares a single budget of
        `auth_rate_limit_requests` per window. Nobody can sign in, and nothing
        in the logs says why.

        Setting it to `True` without a proxy is the mirror image: the header
        is then attacker-supplied, so the limiter is keyed on a string the
        attacker picks and stops limiting anything at all.

        Both failures are silent, and they are opposites, so there is no
        default that is safe in both cases. What this refuses is not a value -
        it is the *absence of a choice*. Either answer boots; not answering
        does not.
        """
        if not self.is_deployed:
            return self
        if "trust_proxy_headers" not in self.model_fields_set:
            raise ValueError(
                "TRUST_PROXY_HEADERS is not set, and in environment="
                f"{self.environment!r} it must be stated explicitly. "
                "Behind a reverse proxy (the `deploy/` stack uses Caddy) set it "
                "to true; exposed directly, set it to false. Left unset, every "
                "per-IP limit counts the whole platform as one client and locks "
                "everybody out."
            )
        return self

    @model_validator(mode="after")
    def _reject_weak_secret_when_deployed(self) -> Self:
        """Refuse to start a deployed process with a guessable signing key.

        Failing at startup is the only safe answer: the alternative is a
        deployment that looks perfectly healthy while every access token it
        issues can be forged by anyone who has read the repository.

        Only enforced where it matters. Development and the test suite keep
        the placeholder, because a local stack that demands a generated
        secret before it will boot is a local stack nobody runs.
        """
        if not self.is_deployed:
            return self
        hint = 'Generate one with `python -c "import secrets; print(secrets.token_urlsafe(48))"`.'
        if self.secret_key in PLACEHOLDER_SECRET_KEYS:
            raise ValueError(
                f"SECRET_KEY is still a placeholder in environment={self.environment!r}. {hint}"
            )
        if self.credentials_encryption_key == DEV_CREDENTIALS_KEY:
            raise ValueError(
                "CREDENTIALS_ENCRYPTION_KEY is still the development default in "
                f"environment={self.environment!r}. Generate one with "
                '`python -c "from cryptography.fernet import Fernet; '
                'print(Fernet.generate_key().decode())"`. Encrypting credentials with a '
                "key that is committed to the repository protects nothing."
            )
        if len(self.secret_key) < MIN_SECRET_KEY_LENGTH:
            raise ValueError(
                f"SECRET_KEY must be at least {MIN_SECRET_KEY_LENGTH} characters in "
                f"environment={self.environment!r}. {hint}"
            )
        return self


@lru_cache
def get_settings() -> Settings:
    """Return a cached `Settings` instance.

    Cached so environment parsing happens once per process; tests can
    bypass this by constructing `Settings(...)` directly.
    """
    return Settings()
