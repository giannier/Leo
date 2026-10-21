"""Version 1 of the public API — aggregates all v1 routers."""

from fastapi import APIRouter

from app.api.v1 import (
    agent,
    analytics,
    assistant,
    assistant_info,
    assistant_media,
    auth,
    billing,
    campaigns,
    connection,
    conversations,
    crm,
    files,
    health,
    inbox,
    knowledge,
    metrics,
    orders,
    platform_admin,
    platform_billing,
    platform_catalog,
    products,
    realtime,
    sandbox,
    scheduling,
    team,
    tenant,
    wizard,
)

router = APIRouter()
router.include_router(health.router)
router.include_router(inbox.router)
router.include_router(agent.router)
router.include_router(analytics.router)
router.include_router(assistant.router)
router.include_router(assistant_info.router)
router.include_router(assistant_media.router)
router.include_router(auth.router)
router.include_router(billing.router)
router.include_router(campaigns.router)
router.include_router(connection.router)
router.include_router(conversations.router)
router.include_router(crm.router)
router.include_router(scheduling.router)
router.include_router(files.router)
router.include_router(knowledge.router)
router.include_router(metrics.router)
router.include_router(orders.router)
router.include_router(platform_admin.router)
router.include_router(platform_billing.router)
router.include_router(platform_catalog.router)
router.include_router(team.router)
router.include_router(products.router)
router.include_router(realtime.router)
router.include_router(sandbox.router)
router.include_router(tenant.router)
router.include_router(wizard.router)
