"""Two-tenant fixture for the multi-tenant isolation suite.

Every test here runs against two real tenants with real rows, so a leak shows
up as one tenant seeing the other's data rather than as a mocked assertion
that proves nothing about the actual SQL.
"""

from __future__ import annotations

import os
import uuid
from collections.abc import AsyncIterator, Iterator
from dataclasses import dataclass
from decimal import Decimal

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import Engine, create_engine, text
from sqlalchemy.orm import Session

from app.api.deps import get_db, get_email_provider, get_login_limiter, get_rate_limiter
from app.core.config import get_settings
from app.core.rate_limit import NullRateLimiter, RateLimiter
from app.models import (
    Conversation,
    KnowledgeChunk,
    KnowledgeSource,
    Message,
    Product,
    Tenant,
)
from app.models.knowledge import EMBEDDING_DIMENSIONS
from app.providers.email.base import EmailProvider
from app.providers.email.mock import MockEmailProvider

REQUIRE_DB_ENV_VAR = "LEO_REQUIRE_DB"

# Re-exported from the model rather than repeated, so a dimension change is a
# one-line edit instead of a hunt through the test suite.
EMBEDDING_DIM = EMBEDDING_DIMENSIONS


@dataclass(frozen=True)
class TenantFixture:
    """One tenant plus one row of each kind that belongs to it."""

    tenant_id: uuid.UUID
    product_id: uuid.UUID
    source_id: uuid.UUID
    chunk_id: uuid.UUID
    conversation_id: uuid.UUID
    message_id: uuid.UUID


@pytest.fixture(scope="session")
def engine() -> Iterator[Engine]:
    """Engine for the isolation suite.

    Mirrors the integration suite: skipping locally is convenient, but in CI
    an unreachable database must fail. An isolation suite that silently does
    not run is worse than no isolation suite, because it still shows green.
    """
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
    """Session inside a transaction that is always rolled back."""
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
    """HTTP client sharing this test's transaction.

    Mirrors the integration suite's fixture. It is here because the tenant
    boundary is not only a repository property: the authentication
    dependencies are what decide which tenant a request may act in, and a
    hole there is a cross-tenant leak whatever the repositories do. Proving
    that end to end needs real requests.
    """
    provider: EmailProvider = mail
    limiter: RateLimiter = NullRateLimiter()
    app.dependency_overrides[get_db] = lambda: db
    app.dependency_overrides[get_email_provider] = lambda: provider
    app.dependency_overrides[get_rate_limiter] = lambda: limiter
    # And the per-account login limiter. Both are Redis-backed and cached
    # per process, so a real one would carry a connection bound to a dead
    # event loop into the next test.
    app.dependency_overrides[get_login_limiter] = lambda: limiter
    transport = ASGITransport(app=app)
    try:
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            yield client
    finally:
        app.dependency_overrides.clear()


def _populate(db: Session, label: str) -> TenantFixture:
    """Create one tenant owning one row of every tenant-scoped kind."""
    tenant = Tenant(name=f"Tenant {label}")
    db.add(tenant)
    db.flush()

    product = Product(tenant_id=tenant.id, name=f"Product {label}", price=Decimal("10.00"))
    source = KnowledgeSource(
        tenant_id=tenant.id, type="faq", title=f"FAQ {label}", status="indexed"
    )
    conversation = Conversation(tenant_id=tenant.id, channel="sandbox")
    db.add_all([product, source, conversation])
    db.flush()

    chunk = KnowledgeChunk(
        tenant_id=tenant.id,
        source_id=source.id,
        content=f"Secret of {label}",
        # Distinct directions, so a cosine search cannot return the right row
        # by luck when the tenant filter is missing.
        embedding=[1.0 if label == "A" else -1.0] * EMBEDDING_DIM,
    )
    message = Message(
        tenant_id=tenant.id,
        conversation_id=conversation.id,
        direction="inbound",
        author="customer",
        content=f"Message from {label}",
    )
    db.add_all([chunk, message])
    db.flush()

    return TenantFixture(
        tenant_id=tenant.id,
        product_id=product.id,
        source_id=source.id,
        chunk_id=chunk.id,
        conversation_id=conversation.id,
        message_id=message.id,
    )


@pytest.fixture
def tenant_a(db: Session) -> TenantFixture:
    return _populate(db, "A")


@pytest.fixture
def tenant_b(db: Session) -> TenantFixture:
    return _populate(db, "B")
