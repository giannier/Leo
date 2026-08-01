"""Builds the right `LLMProvider` for a given caller.

Two levels of configuration:

* The **platform** profile, from the environment. Free NVIDIA Build by
  default; OpenAI when `LLM_PROFILE=openai`.
* A **tenant** override, stored encrypted in `tenant_llm_settings` and edited
  from the panel, for customers who want to pay for their own model.

Only the chat model is overridable. Embeddings always come from the platform
provider - see `app/models/llm_settings.py` for why.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.core.config import Settings, get_settings
from app.core.crypto import decrypt_secret
from app.models.knowledge import EMBEDDING_DIMENSIONS
from app.models.llm_settings import TenantLLMSettings
from app.providers.llm.base import LLMConfigurationError, LLMProvider
from app.providers.llm.offline import OfflineLLMProvider
from app.providers.llm.openai_compatible import OpenAICompatibleProvider

# Shortest thing that could plausibly be a key. Real ones are far longer -
# an OpenAI project key is around 160 characters, an NVIDIA one about 70 - so
# this rejects only the things that are obviously not keys: a placeholder
# somebody tabbed past, a truncated paste, the word "changeme".
#
# The check exists because "set" turned out to be the wrong question. A
# ten-character value passed every guard here and failed at the embedding
# call, where the symptom is a knowledge source stuck at `failed` with a
# provider error - a long way from the line in `.env` that caused it.
MIN_PLAUSIBLE_KEY_LENGTH = 20


def _require_key(value: str | None, *, variable: str, hint: str) -> str:
    """The configured credential, or a refusal that names the fix."""
    key = (value or "").strip()
    if not key:
        raise LLMConfigurationError(f"{variable} is not set. {hint}")
    if len(key) < MIN_PLAUSIBLE_KEY_LENGTH:
        raise LLMConfigurationError(
            f"{variable} is only {len(key)} characters, which is too short to be a real "
            f"key - it looks like a placeholder or a truncated paste. {hint}"
        )
    return key


@dataclass(frozen=True)
class ProviderProfile:
    """Everything needed to build one provider."""

    name: str
    api_key: str
    base_url: str
    chat_model: str
    embedding_model: str
    # OpenAI's v3 embedding models accept `dimensions`, which is what keeps
    # the paid profile producing vectors the same width as the free one.
    # NVIDIA's do not, and their native width already matches.
    embedding_dimensions: int | None
    # NVIDIA requires `input_type` on embeddings; OpenAI rejects it.
    send_embedding_input_type: bool


def _nvidia_profile(settings: Settings) -> ProviderProfile:
    key = _require_key(
        settings.nvidia_api_key,
        variable="NVIDIA_API_KEY",
        hint="Get a free key at https://build.nvidia.com and put it in .env.",
    )
    return ProviderProfile(
        name="nvidia",
        api_key=key,
        base_url=settings.nvidia_base_url,
        chat_model=settings.nvidia_chat_model,
        embedding_model=settings.nvidia_embedding_model,
        embedding_dimensions=None,
        send_embedding_input_type=True,
    )


def _openai_profile(settings: Settings) -> ProviderProfile:
    key = _require_key(
        settings.openai_api_key,
        variable="OPENAI_API_KEY",
        hint="Create one at https://platform.openai.com/api-keys and put it in .env.",
    )
    return ProviderProfile(
        name="openai",
        api_key=key,
        base_url=settings.openai_base_url,
        chat_model=settings.openai_chat_model,
        embedding_model=settings.openai_embedding_model,
        embedding_dimensions=EMBEDDING_DIMENSIONS,
        send_embedding_input_type=False,
    )


def platform_profile(settings: Settings | None = None) -> ProviderProfile:
    """The profile the platform itself pays for."""
    settings = settings or get_settings()
    if settings.llm_profile == "openai":
        return _openai_profile(settings)
    return _nvidia_profile(settings)


def _offline_provider(settings: Settings) -> LLMProvider:
    """The provider for `LLM_PROFILE=offline`.

    It has no profile: there is no base URL, no key and no model to describe,
    so it is built directly rather than squeezed into `ProviderProfile`.
    `OfflineLLMProvider` refuses to construct outside development, so this
    cannot become a deployment that answers customers from a hash.
    """
    return OfflineLLMProvider(environment=settings.environment)


def build_provider(profile: ProviderProfile) -> LLMProvider:
    return OpenAICompatibleProvider(
        api_key=profile.api_key,
        base_url=profile.base_url,
        chat_model=profile.chat_model,
        embedding_model=profile.embedding_model,
        embedding_dimensions=profile.embedding_dimensions,
        send_embedding_input_type=profile.send_embedding_input_type,
    )


def platform_provider(settings: Settings | None = None) -> LLMProvider:
    """Provider for platform-funded work: embeddings, and default chat."""
    settings = settings or get_settings()
    if settings.llm_profile == "offline":
        return _offline_provider(settings)
    return build_provider(platform_profile(settings))


def provider_for_tenant(
    llm_settings: TenantLLMSettings | None,
    *,
    settings: Settings | None = None,
) -> LLMProvider:
    """Chat provider for one tenant, honouring their own credential.

    Falls back to the platform provider when the tenant has not configured
    anything. A tenant whose stored credential is unusable raises rather than
    silently falling back: quietly moving their traffic onto the platform's
    paid account would be the platform absorbing a cost the customer chose to
    take on, and they would never find out their key was broken.
    """
    settings = settings or get_settings()
    if llm_settings is None or llm_settings.mode == "platform":
        return platform_provider(settings)

    if llm_settings.api_key_enc is None:
        raise LLMConfigurationError(
            f"Tenant is set to {llm_settings.mode!r} but has no stored API key."
        )
    if not llm_settings.chat_model:
        raise LLMConfigurationError(
            f"Tenant is set to {llm_settings.mode!r} but has no chat model selected."
        )

    api_key = decrypt_secret(llm_settings.api_key_enc)

    if llm_settings.mode == "byo_openai":
        base_url = settings.openai_base_url
    else:
        if not llm_settings.base_url:
            raise LLMConfigurationError("byo_openai_compatible requires a base_url.")
        base_url = llm_settings.base_url

    return OpenAICompatibleProvider(
        api_key=api_key,
        base_url=base_url,
        chat_model=llm_settings.chat_model,
        # Never used: embeddings stay on the platform provider. Set to the
        # tenant's chat model so a mistaken call fails loudly at the provider
        # rather than quietly writing vectors of the wrong shape.
        embedding_model=llm_settings.chat_model,
        embedding_dimensions=EMBEDDING_DIMENSIONS,
        send_embedding_input_type=False,
    )
