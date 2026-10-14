"""Fixtures for the eval suite.

Reuses the integration suite's transaction-rollback session: an eval seeds a
whole demo restaurant, and leaving that behind would pollute every later run.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from evals.runner import llm_required
from sqlalchemy import Engine, create_engine, text
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.providers.llm.factory import (
    LLMConfigurationError,
    build_provider,
    platform_profile,
)


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


# The hosted profile the platform runs on. It used to be two (NVIDIA Build and
# OpenAI); NVIDIA was removed in Milestone B. The lesson of running both still
# stands: two bugs shipped while these only ran against NVIDIA, because OpenAI
# rejects `max_tokens` and refuses a `tool` message whose preceding assistant
# turn omits its `tool_calls`. The evals must run against the provider that
# actually serves customers.
#
# `offline` is deliberately absent. It answers from a hash, so running golden
# conversations against it would assert that a hash is not a good waiter.
EVAL_PROFILES = ("openai",)


@pytest.fixture(params=EVAL_PROFILES, ids=EVAL_PROFILES)
def llm(request: pytest.FixtureRequest):  # type: ignore[no-untyped-def]
    """One real provider per run of the suite.

    An eval against a mock asserts that the mock returns what it was told to,
    which is worth nothing. The whole question is whether *this* model
    behaves.

    The profile is built explicitly rather than read from `LLM_PROFILE`: the
    evals run against OpenAI however the environment happens to be configured.
    """
    profile = request.param
    settings = get_settings().model_copy(update={"llm_profile": profile})
    try:
        return build_provider(platform_profile(settings))
    except LLMConfigurationError as exc:
        if llm_required():
            pytest.fail(f"LEO_REQUIRE_LLM is set but the {profile!r} profile is not usable: {exc}")
        pytest.skip(f"{profile}: {exc}")
