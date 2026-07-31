"""Version 1 of the public API — aggregates all v1 routers."""

from fastapi import APIRouter

from app.api.v1 import health

router = APIRouter()
router.include_router(health.router)
