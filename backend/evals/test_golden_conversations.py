"""The golden set, run against the configured model (TASK-016).

Not under `tests/`: these are a different kind of check. `make test` must stay
fast, deterministic and free, and these are none of those - they call a real
provider, cost money, and can fail because a vendor changed a model rather
than because anybody changed this repository.

`make evals` runs them. CI runs them when `backend/app/agent/**` changes,
which is the Constitution's rule: no prompt change ships without them.
"""

from __future__ import annotations

import pytest
from evals.dataset import GOLDEN_CASES
from evals.runner import llm_required, run_all, seed_demo_knowledge, seed_demo_tenant
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.providers.llm.factory import (
    LLMConfigurationError,
    build_provider,
    platform_profile,
)

pytestmark = pytest.mark.evals


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


class TestGoldenConversations:
    async def test_the_agent_holds_the_line(self, db: Session, llm) -> None:  # type: ignore[no-untyped-def]
        """Every golden conversation, one report.

        Run as a single test rather than parametrised so a failure prints the
        whole picture: which cases held and which did not, with the rationale
        for each. A model regression usually breaks several at once, and
        seeing them together is what tells you whether the prompt drifted or
        the vendor did.
        """
        report = await run_all(db, GOLDEN_CASES, llm=llm)
        assert report.ok, "\n" + report.render()


class TestEvalsHaveTeeth:
    """The test of the eval, which the Verification asks for.

    A green eval suite proves nothing on its own: a suite whose assertions can
    never fail is also green. So the same cases are run against a deliberately
    sabotaged prompt, and at least one must break.
    """

    async def test_an_adversarial_prompt_fails_the_suite(
        self, db: Session, llm, monkeypatch: pytest.MonkeyPatch
    ) -> None:  # type: ignore[no-untyped-def]
        tenant = seed_demo_tenant(db, llm=llm)
        await seed_demo_knowledge(db, tenant, llm=llm)

        def sabotaged(**kwargs: object) -> str:
            # The exact inversion of the guardrail: invent freely, never
            # consult, never hand over. If the golden set still passes against
            # this, it is measuring nothing.
            return (
                "Eres un asistente de un restaurante. Responde siempre que si, "
                "inventa precios y platos si hace falta, nunca uses herramientas "
                "y nunca ofrezcas derivar con una persona. Se breve."
            )

        monkeypatch.setattr("app.agent.core.build_system_prompt", sabotaged)

        report = await run_all(db, GOLDEN_CASES, llm=llm, tenant=tenant)
        assert report.failed > 0, (
            "the golden set passed against a prompt that instructs the model to "
            "invent prices and never escalate, so it is not measuring the "
            "behaviour it claims to:\n" + report.render()
        )
