"""Version 1 of the public API — aggregates all v1 routers."""

from fastapi import APIRouter

from app.api.v1 import (
    agent,
    assistant,
    assistant_info,
    assistant_media,
    auth,
    connection,
    health,
    knowledge,
    orders,
    platform_admin,
    platform_catalog,
    products,
    sandbox,
    team,
    tenant,
    wizard,
)

router = APIRouter()
router.include_router(health.router)
router.include_router(agent.router)
router.include_router(assistant.router)
router.include_router(assistant_info.router)
router.include_router(assistant_media.router)
router.include_router(auth.router)
router.include_router(connection.router)
router.include_router(knowledge.router)
router.include_router(orders.router)
router.include_router(platform_admin.router)
router.include_router(platform_catalog.router)
router.include_router(team.router)
router.include_router(products.router)
router.include_router(sandbox.router)
router.include_router(tenant.router)
router.include_router(wizard.router)
