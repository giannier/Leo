"""LLM provider port and adapters.

Everything that talks to a model API lives behind `LLMProvider`. Nothing
above this package may import `httpx` or a vendor SDK directly
(Constitution).
"""

from app.providers.llm.base import (
    ChatMessage,
    ChatResult,
    LLMAuthError,
    LLMConfigurationError,
    LLMError,
    LLMProvider,
    LLMRateLimitError,
    ToolCall,
    ToolSpec,
)
from app.providers.llm.factory import (
    ProviderProfile,
    build_provider,
    platform_profile,
    platform_provider,
    provider_for_tenant,
)
from app.providers.llm.mock import MockLLMProvider
from app.providers.llm.offline import OfflineLLMProvider
from app.providers.llm.openai_compatible import OpenAICompatibleProvider

__all__ = [
    "ChatMessage",
    "ChatResult",
    "LLMAuthError",
    "LLMConfigurationError",
    "OfflineLLMProvider",
    "LLMError",
    "LLMProvider",
    "LLMRateLimitError",
    "MockLLMProvider",
    "OpenAICompatibleProvider",
    "ProviderProfile",
    "ToolCall",
    "ToolSpec",
    "build_provider",
    "platform_profile",
    "platform_provider",
    "provider_for_tenant",
]
