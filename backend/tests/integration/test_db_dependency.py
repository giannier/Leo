"""The request-scoped session dependency.

`get_db` is the one piece of database plumbing the rest of the suite never
exercises, because every other test overrides it with a session inside a
rolled-back transaction. Its rollback is the safety net for an exception
that escapes a service mid-transaction, so it gets its own test against the
real engine.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import Engine, text
from sqlalchemy.orm import Session

from app.api.deps import get_db
from app.models import Tenant

PROBE_NAME = "get_db rollback probe"


class TestGetDb:
    def test_yields_a_usable_session_and_closes_it(self, engine: Engine) -> None:
        generator = get_db()
        session = next(generator)

        assert session.execute(text("SELECT 1")).scalar_one() == 1

        with pytest.raises(StopIteration):
            next(generator)
        assert session.in_transaction() is False

    def test_an_escaping_exception_rolls_the_transaction_back(self, engine: Engine) -> None:
        """Nothing half-written may reach the next request on this connection."""
        generator = get_db()
        session = next(generator)

        tenant = Tenant(name=f"{PROBE_NAME} {uuid.uuid4().hex[:8]}")
        session.add(tenant)
        session.flush()
        probe_id = tenant.id

        with pytest.raises(RuntimeError, match="boom"):
            generator.throw(RuntimeError("boom"))

        try:
            with Session(engine) as check:
                assert check.get(Tenant, probe_id) is None
        finally:
            # Belt and braces: if the rollback ever regressed, the assertion
            # above fails *and* the row is removed, so a broken run does not
            # leave rubbish behind for the next one.
            with Session(engine) as cleanup:
                stray = cleanup.get(Tenant, probe_id)
                if stray is not None:  # pragma: no cover - only on regression
                    cleanup.delete(stray)
                    cleanup.commit()
