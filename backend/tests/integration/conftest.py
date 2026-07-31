"""Database fixtures for integration tests.

Every test runs inside a transaction that is rolled back afterwards, so the
suite can point at the ordinary development database without leaving rows
behind and without tests seeing each other's writes.
"""

import os
from collections.abc import AsyncIterator, Iterator

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import Engine, create_engine, text
from sqlalchemy.orm import Session

from app.api.deps import get_db, get_email_provider
from app.core.config import get_settings
from app.providers.email.base import EmailProvider
from app.providers.email.mock import MockEmailProvider

# CI sets this. Locally, skipping when the stack is down is a convenience;
# in CI it would be a false green - a misconfigured database would silently
# skip the entire suite and the pipeline would still pass. When this is set,
# an unreachable database fails the run instead.
REQUIRE_DB_ENV_VAR = "LEO_REQUIRE_DB"


@pytest.fixture(scope="session")
def engine() -> Iterator[Engine]:
    """Engine pointed at the configured database."""
    eng = create_engine(get_settings().database_url)
    try:
        with eng.connect() as connection:
            connection.execute(text("SELECT 1"))
    except Exception as exc:  # pragma: no cover - environment dependent
        message = f"database not reachable: {type(exc).__name__}: {exc}"
        if os.getenv(REQUIRE_DB_ENV_VAR):
            pytest.fail(f"{REQUIRE_DB_ENV_VAR} is set but the {message}")
        pytest.skip(message)
    yield eng
    eng.dispose()


@pytest.fixture
def db(engine: Engine) -> Iterator[Session]:
    """Session bound to a transaction that is always rolled back.

    The outer transaction is owned by the fixture, not the session, so even a
    test that calls `commit()` writes nothing durable.
    """
    connection = engine.connect()
    transaction = connection.begin()
    session = Session(bind=connection, join_transaction_mode="create_savepoint")
    try:
        yield session
    finally:
        session.close()
        transaction.rollback()
        connection.close()


@pytest.fixture
def mail() -> MockEmailProvider:
    """Email provider that records instead of sending."""
    return MockEmailProvider()


@pytest.fixture
async def api(app: FastAPI, db: Session, mail: MockEmailProvider) -> AsyncIterator[AsyncClient]:
    """HTTP client whose requests run inside the test's own transaction.

    `get_db` is overridden with the fixture session rather than a new one, so
    a request's writes are visible to the test's assertions and are undone by
    the same rollback. `join_transaction_mode="create_savepoint"` is what
    makes this work: a service that commits releases a savepoint, and one
    that rolls back returns to it, both without touching the outer
    transaction the fixture owns.
    """
    provider: EmailProvider = mail
    app.dependency_overrides[get_db] = lambda: db
    app.dependency_overrides[get_email_provider] = lambda: provider
    transport = ASGITransport(app=app)
    try:
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            yield client
    finally:
        app.dependency_overrides.clear()
