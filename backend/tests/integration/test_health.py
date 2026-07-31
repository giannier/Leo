"""Integration test for the health check endpoint.

Runs against the real FastAPI app through the ASGI transport (no live
socket, no Postgres/Redis) — this is the one test TASK-001 must ship
passing per the task's Verification criteria.
"""

import pytest
from httpx import AsyncClient


@pytest.mark.asyncio
async def test_health_check_returns_ok(client: AsyncClient) -> None:
    response = await client.get("/api/v1/health")

    assert response.status_code == 200
    body = response.json()
    assert body == {"status": "ok", "service": "leo-api"}
