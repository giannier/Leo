"""Generate `deploy/env.reference` from `Settings`.

`.env.example` went stale the moment TASK-005 added settings nobody copied
into it, and by TASK-009 it was missing 24 of 41 variables - which is how a
Resend key ends up "configured" in a file the application never reads.

The fix is not a better-maintained example file; it is not maintaining one by
hand at all. This derives the reference from the model, and
`tests/unit/test_env_reference.py` fails if the checked-in copy drifts, so
adding a setting without documenting it breaks the build.

Run with `make env-reference`.
"""

from __future__ import annotations

from pathlib import Path

from app.core.config import Settings

# Values a deployment must supply; there is no default that works.
SECRETS = frozenset(
    {
        "resend_api_key",
        "nvidia_api_key",
        "openai_api_key",
        "credentials_encryption_key",
        "secret_key",
        "ycloud_webhook_secret",
    }
)

GROUPS: list[tuple[str, list[str]]] = [
    ("General", ["app_name", "environment", "debug", "api_v1_prefix", "log_level"]),
    ("Database / Redis", ["database_url", "redis_url"]),
    (
        "Auth",
        [
            "secret_key",
            "jwt_algorithm",
            "access_token_expire_minutes",
            "refresh_token_expire_days",
            "auth_rate_limit_requests",
            "auth_rate_limit_window_seconds",
            "login_attempt_limit",
            "login_attempt_window_seconds",
            "trust_proxy_headers",
        ],
    ),
    ("Credential encryption", ["credentials_encryption_key"]),
    (
        "LLM providers",
        [
            "llm_profile",
            "nvidia_api_key",
            "nvidia_base_url",
            "nvidia_chat_model",
            "nvidia_embedding_model",
            "openai_api_key",
            "openai_base_url",
            "openai_chat_model",
            "openai_embedding_model",
        ],
    ),
    (
        "Transactional email",
        [
            "email_provider",
            "resend_api_key",
            "email_from",
            "email_outbox_path",
            "email_verification_token_ttl_hours",
        ],
    ),
    ("Phone OTP", ["otp_request_limit", "otp_request_window_seconds"]),
    (
        "WhatsApp channel",
        [
            "ycloud_webhook_secret",
            "ycloud_base_url",
            "channel_debounce_seconds",
            "channel_send_max_attempts",
        ],
    ),
    (
        "Object storage",
        ["s3_endpoint_url", "s3_access_key", "s3_secret_key", "s3_bucket", "s3_region"],
    ),
    (
        "Knowledge / RAG",
        [
            "knowledge_max_pdf_bytes",
            "knowledge_chunk_chars",
            "knowledge_chunk_overlap_chars",
            "knowledge_embed_batch_size",
            "knowledge_search_default_k",
        ],
    ),
    ("Frontend / CORS", ["frontend_base_url", "cors_origins"]),
]

HEADER = """\
# Reference environment for Leo - GENERATED from app/core/config.py.
#
# Regenerate with `make env-reference`. Copy what you need into `.env`, which
# is git-ignored and is the only place real credentials belong.
#
# Every variable below has a working development default except the ones
# marked REQUIRED. Omitting the rest is fine.
"""


def _render_value(name: str, default: object) -> str:
    if isinstance(default, bool):
        return str(default).lower()
    if isinstance(default, list):
        return ",".join(str(item) for item in default)
    if default is None:
        return ""
    return str(default)


def render() -> str:
    fields = Settings.model_fields
    lines = [HEADER]
    documented: set[str] = set()

    for title, names in GROUPS:
        lines.append(f"\n# --- {title} " + "-" * max(3, 60 - len(title)))
        for name in names:
            if name not in fields:
                continue
            documented.add(name)
            default = fields[name].default
            needs_value = name in SECRETS and (default is None or "change-me" in str(default))
            if needs_value:
                lines.append("# REQUIRED - no usable default")
                lines.append(f"{name.upper()}=")
            else:
                lines.append(f"{name.upper()}={_render_value(name, default)}")

    undocumented = sorted(set(fields) - documented)
    if undocumented:
        # Never silently dropped: an unplaced setting still has to appear, or
        # this file stops being a complete reference.
        lines.append("\n# --- Ungrouped " + "-" * 48)
        for name in undocumented:
            lines.append(f"{name.upper()}={_render_value(name, fields[name].default)}")

    return "\n".join(lines) + "\n"


REFERENCE_PATH = Path(__file__).resolve().parents[2] / "deploy" / "env.reference"


def main() -> None:
    REFERENCE_PATH.write_text(render(), encoding="utf-8")
    print(f"wrote {REFERENCE_PATH}")


if __name__ == "__main__":
    main()
