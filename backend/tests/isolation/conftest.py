"""Two-tenant fixture for the multi-tenant isolation suite.

Every test here runs against two real tenants with real rows, so a leak shows
up as one tenant seeing the other's data rather than as a mocked assertion
that proves nothing about the actual SQL.
"""

from __future__ import annotations

import os
import uuid
from collections.abc import Iterator
from dataclasses import dataclass
from decimal import Decimal

import pytest
from sqlalchemy import Engine, create_engine, text
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.models import (
    Conversation,
    KnowledgeChunk,
    KnowledgeSource,
    Message,
    Product,
    Tenant,
)

REQUIRE_DB_ENV_VAR = "LEO_REQUIRE_DB"
EMBEDDING_DIM = 1536


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
