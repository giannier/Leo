"""Data access layer. Every business query is scoped by `tenant_id` here."""

from app.repositories.base import BaseRepository, TenantScopeError
from app.repositories.channel import (
    ContactRepository,
    MessageStatusEventRepository,
    WebhookEventRepository,
    session_window_is_open,
)
from app.repositories.conversation import (
    AgentTraceRepository,
    ConversationRepository,
    MessageRepository,
)
from app.repositories.knowledge import (
    KnowledgeChunkRepository,
    KnowledgeSourceRepository,
)
from app.repositories.plan import PlanRepository
from app.repositories.product import ProductRepository
from app.repositories.tenant import (
    TenantMembership,
    TenantRepository,
    TenantUserRepository,
    TrialPhoneRepository,
    UserMembershipRepository,
)
from app.repositories.user import (
    EmailTokenRepository,
    RefreshTokenRepository,
    UserRepository,
)

__all__ = [
    "AgentTraceRepository",
    "BaseRepository",
    "ContactRepository",
    "ConversationRepository",
    "EmailTokenRepository",
    "KnowledgeChunkRepository",
    "KnowledgeSourceRepository",
    "MessageRepository",
    "MessageStatusEventRepository",
    "PlanRepository",
    "ProductRepository",
    "RefreshTokenRepository",
    "TenantMembership",
    "TenantRepository",
    "TenantScopeError",
    "TenantUserRepository",
    "TrialPhoneRepository",
    "UserMembershipRepository",
    "UserRepository",
    "WebhookEventRepository",
    "session_window_is_open",
]
