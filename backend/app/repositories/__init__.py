"""Data access layer. Every business query is scoped by `tenant_id` here."""

from app.repositories.base import BaseRepository, TenantScopeError
from app.repositories.conversation import (
    AgentTraceRepository,
    ConversationRepository,
    MessageRepository,
)
from app.repositories.knowledge import (
    KnowledgeChunkRepository,
    KnowledgeSourceRepository,
)
from app.repositories.product import ProductRepository

__all__ = [
    "AgentTraceRepository",
    "BaseRepository",
    "ConversationRepository",
    "KnowledgeChunkRepository",
    "KnowledgeSourceRepository",
    "MessageRepository",
    "ProductRepository",
    "TenantScopeError",
]
