"""Shared pytest fixtures for unit, integration and isolation suites."""

from collections.abc import AsyncIterator

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from app.main import create_app


@pytest.fixture
def app() -> FastAPI:
    """Build a fresh FastAPI app instance per test."""
    return create_app()


@pytest.fixture
async def client(app: FastAPI) -> AsyncIterator[AsyncClient]:
    """Async HTTP client wired directly to the app via ASGI transport.

    No real network socket or running server is needed, so tests stay fast
    and work without Postgres/Redis for endpoints that don't need them.
    """
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as async_client:
        yield async_client
