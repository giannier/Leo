"""Version 1 of the public API — aggregates all v1 routers."""

from fastapi import APIRouter

from app.api.v1 import auth, health, knowledge

router = APIRouter()
router.include_router(health.router)
router.include_router(auth.router)
router.include_router(knowledge.router)
