"""Health check endpoint.

Intentionally dependency-free: it must respond even when PostgreSQL and
Redis are unreachable, so it works as a bare liveness probe.
"""

from fastapi import APIRouter
from pydantic import BaseModel

router = APIRouter(tags=["health"])


class HealthResponse(BaseModel):
    """Shape of the health check response."""

    status: str
    service: str


@router.get("/health", response_model=HealthResponse)
async def get_health() -> HealthResponse:
    """Return a static liveness signal for the API service."""
    return HealthResponse(status="ok", service="leo-api")
