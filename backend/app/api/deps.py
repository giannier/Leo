"""Shared FastAPI dependencies.

Both of these exist to be overridden in tests: `get_db` with a session
already inside a rolled-back transaction, `get_email_provider` with the mock
that records what was sent.
"""

from __future__ import annotations

import logging
from collections.abc import Iterator

from sqlalchemy.orm import Session

from app.core.db import get_session_factory
from app.providers.email.base import EmailConfigurationError, EmailProvider
from app.providers.email.factory import build_email_provider

logger = logging.getLogger(__name__)


def get_db() -> Iterator[Session]:
    """A session for the duration of one request.

    Owns the session, not the transaction: services commit their own units
    of work. The rollback here is the safety net for an exception that
    escaped a service mid-transaction, so a half-written unit of work is
    never left for the connection pool to hand to the next request.
    """
    session = get_session_factory()()
    try:
        yield session
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def get_email_provider() -> EmailProvider | None:
    """The configured email provider, or `None` when there is no credential.

    Returning `None` rather than raising keeps registration working on a
    deployment that cannot send mail yet: the account is still created and
    the response says the message did not go out. The warning is logged once
    per attempt so the gap is visible rather than assumed.
    """
    try:
        return build_email_provider()
    except EmailConfigurationError as exc:
        logger.warning("email provider unavailable: %s", exc)
        return None
