"""SQLAlchemy 2.x typed models. Schema changes only via Alembic migrations.

Importing every model here is what populates `Base.metadata`. Alembic's
`env.py` imports only this package, so a model that is not re-exported below
is invisible to autogenerate and would silently never get a migration.
"""

from app.models.audit import AuditLog
from app.models.base import Base
from app.models.business import (
    AgentConfig,
    BusinessProfile,
    PaymentMethodBusiness,
    Product,
)
from app.models.channel import Contact, MessageStatusEvent, WebhookEvent
from app.models.conversation import AgentTrace, Conversation, Message
from app.models.conversation_state import ConversationStageEvent, ConversationState
from app.models.knowledge import KnowledgeChunk, KnowledgeSource
from app.models.llm_settings import TenantLLMSettings
from app.models.plan import Plan
from app.models.tenant import (
    Invitation,
    Tenant,
    TenantNotification,
    TenantUser,
)
from app.models.user import EmailToken, OtpCode, RefreshToken, User
from app.models.whatsapp import ConnectionRequest, WhatsAppNumber

__all__ = [
    "AgentConfig",
    "AgentTrace",
    "AuditLog",
    "Base",
    "BusinessProfile",
    "ConnectionRequest",
    "Contact",
    "Conversation",
    "ConversationStageEvent",
    "ConversationState",
    "EmailToken",
    "Invitation",
    "KnowledgeChunk",
    "KnowledgeSource",
    "Message",
    "MessageStatusEvent",
    "OtpCode",
    "PaymentMethodBusiness",
    "Plan",
    "Product",
    "RefreshToken",
    "Tenant",
    "TenantLLMSettings",
    "TenantNotification",
    "TenantUser",
    "User",
    "WebhookEvent",
    "WhatsAppNumber",
]
