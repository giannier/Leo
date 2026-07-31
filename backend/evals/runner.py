"""Run the golden set against a real model (TASK-016).

Separated from the cases so the same runner can be pointed at a deliberately
broken prompt - which is how `test_evals_have_teeth` proves the suite would
catch a regression rather than passing whatever it is given.

**These need a real provider.** An eval against a mock asserts that the mock
returns what it was told to, which is worth nothing. The whole question is
whether *this* model, given *this* prompt and *these* tools, behaves. So the
runner skips loudly without a credential and fails outright when
`LEO_REQUIRE_LLM` is set, the same shape as the database guard - a silently
skipped eval suite is worse than none, because it still shows green.
"""

from __future__ import annotations

import asyncio
import os
import uuid
from dataclasses import dataclass
from decimal import Decimal

from evals.dataset import DEMO_KNOWLEDGE, DEMO_PRODUCTS, EvalCase
from sqlalchemy.orm import Session

from app.agent.core import AgentCore
from app.models.business import AgentConfig, BusinessProfile, Product
from app.models.conversation import Conversation, Message
from app.models.knowledge import KnowledgeChunk, KnowledgeSource
from app.models.tenant import Tenant
from app.providers.llm.base import LLMProvider

REQUIRE_LLM_ENV_VAR = "LEO_REQUIRE_LLM"

# A free-tier provider occasionally times out under load. That is a vendor
# blip, not a prompt regression, and failing the suite on it would train
# everyone to re-run evals until they pass - which destroys their value.
#
# Retried here and *not* in production: a customer waiting in a chat is better
# served by a fast fallback than by a second attempt that doubles their wait.
MAX_ATTEMPTS = 3


@dataclass(frozen=True)
class EvalOutcome:
    case: EvalCase
    passed: bool
    detail: str | None
    answer: str


@dataclass(frozen=True)
class EvalReport:
    outcomes: tuple[EvalOutcome, ...]

    @property
    def passed(self) -> int:
        return sum(1 for outcome in self.outcomes if outcome.passed)

    @property
    def failed(self) -> int:
        return len(self.outcomes) - self.passed

    @property
    def ok(self) -> bool:
        return self.failed == 0

    def render(self) -> str:
        lines = [f"{self.passed}/{len(self.outcomes)} golden cases passed", ""]
        for outcome in self.outcomes:
            mark = "PASS" if outcome.passed else "FAIL"
            lines.append(f"[{mark}] {outcome.case.name}")
            if not outcome.passed:
                # The rationale is printed on failure because the assertion
                # alone does not explain, six months later, what behaviour was
                # being protected.
                lines.append(f"       why it matters: {outcome.case.rationale}")
                lines.append(f"       problem       : {outcome.detail}")
                lines.append(f"       answer        : {outcome.answer!r}")
        return "\n".join(lines)


def seed_demo_tenant(session: Session, *, llm: LLMProvider) -> Tenant:
    """Build the demo restaurant the golden set is written against.

    Embeddings come from the same provider the agent will use, so retrieval in
    the eval behaves as it does in production. Seeding with a different model
    would make the knowledge case measure the wrong thing.
    """
    tenant = Tenant(name="Restaurante Demo")
    session.add(tenant)
    session.flush()

    session.add_all(
        [
            BusinessProfile(
                tenant_id=tenant.id,
                business_name="Restaurante Demo",
                industry="restaurante",
                currency="PEN",
                timezone="America/Lima",
            ),
            AgentConfig(
                tenant_id=tenant.id,
                agent_name="Leo",
                tone="cercano",
                objective="ambos",
                escalation_rules=["cliente_molesto", "pide_humano", "fuera_de_catalogo"],
            ),
        ]
    )
    for product in DEMO_PRODUCTS:
        session.add(
            Product(
                tenant_id=tenant.id,
                name=product.name,
                price=product.price,
                category=product.category,
                description=product.description,
                stock=product.stock,
                is_active=True,
            )
        )
    session.flush()
    return tenant


async def seed_demo_knowledge(session: Session, tenant: Tenant, *, llm: LLMProvider) -> None:
    for entry in DEMO_KNOWLEDGE:
        source = KnowledgeSource(
            tenant_id=tenant.id,
            type="faq",
            title=entry.title,
            content=entry.content,
            char_count=len(entry.content),
            status="indexed",
        )
        session.add(source)
        session.flush()
        vectors = await llm.embed([entry.content], input_type="passage")
        session.add(
            KnowledgeChunk(
                tenant_id=tenant.id,
                source_id=source.id,
                content=entry.content,
                embedding=vectors[0],
            )
        )
    session.flush()


async def run_case(
    session: Session, tenant: Tenant, case: EvalCase, *, llm: LLMProvider
) -> EvalOutcome:
    conversation = Conversation(tenant_id=tenant.id, channel="sandbox")
    session.add(conversation)
    session.flush()

    for author, content in case.history:
        session.add(
            Message(
                tenant_id=tenant.id,
                conversation_id=conversation.id,
                direction="inbound" if author == "customer" else "outbound",
                author=author,
                content=content,
            )
        )
    session.flush()

    core = AgentCore(session, tenant.id, llm=llm)
    for attempt in range(MAX_ATTEMPTS):
        reply = await core.respond(
            conversation_id=conversation.id, customer_message=case.customer_message
        )
        if not reply.degraded:
            break
        if attempt == MAX_ATTEMPTS - 1:
            return EvalOutcome(
                case=case,
                passed=False,
                detail=(
                    f"the provider failed {MAX_ATTEMPTS} times, so the model was "
                    "never exercised - this is an infrastructure failure, not a "
                    "prompt regression"
                ),
                answer=reply.content,
            )
        await asyncio.sleep(2**attempt)

    detail = case.check(reply)
    return EvalOutcome(case=case, passed=detail is None, detail=detail, answer=reply.content)


async def run_all(
    session: Session,
    cases: tuple[EvalCase, ...],
    *,
    llm: LLMProvider,
    tenant: Tenant | None = None,
) -> EvalReport:
    if tenant is None:
        tenant = seed_demo_tenant(session, llm=llm)
        await seed_demo_knowledge(session, tenant, llm=llm)
    outcomes = [await run_case(session, tenant, case, llm=llm) for case in cases]
    return EvalReport(outcomes=tuple(outcomes))


def llm_required() -> bool:
    """Whether a missing credential should fail rather than skip."""
    return bool(os.getenv(REQUIRE_LLM_ENV_VAR))


def unique_suffix() -> str:
    return uuid.uuid4().hex[:8]


__all__ = [
    "Decimal",
    "EvalOutcome",
    "EvalReport",
    "llm_required",
    "run_all",
    "run_case",
    "seed_demo_knowledge",
    "seed_demo_tenant",
    "unique_suffix",
]
