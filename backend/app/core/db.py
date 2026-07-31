"""Database engine and session factory for the running application.

Deliberately separate from the test fixtures: tests build their own engine
and wrap every test in a transaction they roll back, so nothing here is
imported by them except through the `get_db` dependency they override.

Transaction ownership is explicit. The request-scoped dependency owns the
*session lifetime*; services own the *transaction*, committing when the unit
of work they implement is complete. A service that never commits writes
nothing, which is far easier to spot than a dependency that quietly commits
half-finished work at the end of a failed request.
"""

from __future__ import annotations

from functools import lru_cache

from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import get_settings


@lru_cache
def get_engine() -> Engine:
    """The process-wide connection pool.

    `pool_pre_ping` costs one cheap round trip per checkout and removes the
    class of errors where a connection was closed by the database (restart,
    idle timeout) while sitting unused in the pool.
    """
    return create_engine(get_settings().database_url, pool_pre_ping=True)


@lru_cache
def get_session_factory() -> sessionmaker[Session]:
    """Session factory bound to the shared engine.

    Left on SQLAlchemy's defaults on purpose, so that a session built here
    behaves exactly like the one the test fixtures build by hand. A
    divergence in `autoflush` or `expire_on_commit` between the two is the
    kind of difference that makes a test pass and production fail.
    """
    return sessionmaker(bind=get_engine())
