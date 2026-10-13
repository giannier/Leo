"""Shared pytest fixtures for unit, integration and isolation suites.

**Never assert a global row count, or a position in an unscoped list.**

This has now broken four separate tests - products, knowledge sources, audit
logs, and the admin queue - and always the same way. A test that says "there
is exactly one such row in the database" is really saying "nothing else has
ever used this database", which stops being true the moment anybody runs the
end-to-end suite, clicks around the panel, or reproduces a bug by hand. The
failure then names a test that has nothing to do with what broke.

Assert the property instead, scoped to the rows the test created:

    # brittle
    assert session.execute(select(func.count()).select_from(Product)).scalar_one() == 2
    assert ids.index(first) < ids.index(second)

    # what was actually meant
    assert ProductRepository(session, tenant_id).count() == 2
    mine = [row for row in ids if row in {first, second}]
    assert mine == [first, second]

Tenant-scoped counts are fine and immune by construction - that is the point
of them. The rule is about reads that cross the whole table.
"""

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


@pytest.fixture(autouse=True)
def billing_on(monkeypatch: pytest.MonkeyPatch) -> None:
    """Every test runs with platform billing enforced.

    The default is off while Giannier tests the product with his own
    businesses (`BILLING_ENFORCED`), but the trial, quota and limit tests are
    about the behaviour that will be switched on, so they get it. The tests
    of the off switch turn it off themselves
    (`tests/integration/test_billing_switch.py`).
    """
    from app.core.config import get_settings

    monkeypatch.setattr(get_settings(), "billing_enforced", True)
