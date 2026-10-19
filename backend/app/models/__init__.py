"""SQLAlchemy 2.x typed models. Schema changes only via Alembic migrations.

Importing every model here is what populates `Base.metadata`. Alembic's
`env.py` imports only this package, so a model that is not re-exported below
is invisible to autogenerate and would silently never get a migration.
"""

from app.models.audit import AuditLog
from app.models.base import Base
from app.models.billing import (
    BillingLedger,
    Charge,
    Coupon,
    CouponRedemption,
    Payment,
    PlanAddon,
    Subscription,
    UsagePeriod,
)
from app.models.business import (
    AgentConfig,
    BusinessProfile,
    PaymentMethodBusiness,
    Product,
    ProductCategory,
)
from app.models.campaigns import (
    Campaign,
    CampaignRecipient,
    ContactOptin,
    ContactTag,
    MessageTemplate,
    MetaRate,
    OptinImport,
)
from app.models.catalog import (
    CatalogEmbedding,
    CatalogFaq,
    CatalogObjection,
    CatalogRule,
    CatalogSuggestedSituation,
    CatalogVersion,
    Rubro,
    TenantCatalogSeen,
    TenantRuleSwitch,
)
from app.models.channel import Contact, MessageStatusEvent, WebhookEvent
from app.models.conversation import AgentTrace, Conversation, Message
from app.models.conversation_state import ConversationStageEvent, ConversationState
from app.models.crm import Lead, LeadEvent, LostReason, PipelineStage
from app.models.feedback import SandboxFeedback
from app.models.inbox import ConversationNote, NotificationSettings
from app.models.knowledge import KnowledgeChunk, KnowledgeSource
from app.models.llm_settings import TenantLLMSettings
from app.models.media import MediaAsset
from app.models.metrics import DashboardSettings, MetricsDaily, ModelCost
from app.models.orders import Order, OrderItem, PaymentProof, TenantSequence
from app.models.plan import Plan
from app.models.sales_config import BusinessInfo, Faq, Promotion
from app.models.situation import Situation
from app.models.tenant import (
    Invitation,
    Tenant,
    TenantNotification,
    TenantUser,
)
from app.models.user import EmailToken, OtpCode, RefreshToken, User
from app.models.whatsapp import ConnectionRequest, WhatsAppNumber

__all__ = [
    "Campaign",
    "CampaignRecipient",
    "ContactOptin",
    "ContactTag",
    "MessageTemplate",
    "MetaRate",
    "OptinImport",
    "ConversationNote",
    "SandboxFeedback",
    "Lead",
    "LeadEvent",
    "LostReason",
    "PipelineStage",
    "NotificationSettings",
    "AgentConfig",
    "AgentTrace",
    "AuditLog",
    "Base",
    "BusinessInfo",
    "CatalogEmbedding",
    "CatalogFaq",
    "CatalogObjection",
    "CatalogRule",
    "CatalogSuggestedSituation",
    "CatalogVersion",
    "BusinessProfile",
    "ConnectionRequest",
    "Contact",
    "Conversation",
    "ConversationStageEvent",
    "ConversationState",
    "EmailToken",
    "Faq",
    "Invitation",
    "KnowledgeChunk",
    "KnowledgeSource",
    "MediaAsset",
    "Message",
    "MessageStatusEvent",
    "Order",
    "OrderItem",
    "OtpCode",
    "PaymentProof",
    "PaymentMethodBusiness",
    "BillingLedger",
    "Charge",
    "Coupon",
    "CouponRedemption",
    "Payment",
    "DashboardSettings",
    "MetricsDaily",
    "ModelCost",
    "Plan",
    "PlanAddon",
    "Subscription",
    "UsagePeriod",
    "Product",
    "ProductCategory",
    "Promotion",
    "Rubro",
    "Situation",
    "TenantCatalogSeen",
    "TenantRuleSwitch",
    "RefreshToken",
    "Tenant",
    "TenantLLMSettings",
    "TenantNotification",
    "TenantSequence",
    "TenantUser",
    "User",
    "WebhookEvent",
    "WhatsAppNumber",
]
