"""Fixtures for the eval suite.

Reuses the integration suite's transaction-rollback session: an eval seeds a
whole demo restaurant, and leaving that behind would pollute every later run.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from sqlalchemy import Engine, create_engine, text
from sqlalchemy.orm import Session

from app.core.config import get_settings


@pytest.fixture(scope="session")
def engine() -> Iterator[Engine]:
    eng = create_engine(get_settings().database_url)
    try:
        with eng.connect() as connection:
            connection.execute(text("SELECT 1"))
    except Exception as exc:  # pragma: no cover - environment dependent
        pytest.skip(f"database not reachable: {type(exc).__name__}")
    yield eng
    eng.dispose()


@pytest.fixture
def db(engine: Engine) -> Iterator[Session]:
    connection = engine.connect()
    transaction = connection.begin()
    session = Session(bind=connection, join_transaction_mode="create_savepoint")
    try:
        yield session
    finally:
        session.close()
        transaction.rollback()
        connection.close()
