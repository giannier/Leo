"""Situations belong to one business (017, TASK-462 and TASK-463).

The selector ranks by vector distance: if the tenant filter ran after the
LIMIT, another business's closer situation could take the slot or, worse,
enter this business's prompt.
"""

from __future__ import annotations

import pytest
from sqlalchemy.orm import Session

from app.agent.situations import select_situations
from app.providers.llm.offline import OfflineLLMProvider
from app.repositories.base import TenantScopeError
from app.repositories.situation import SituationRepository
from app.services.situations import SituationInput, SituationService, embed_pending
from tests.isolation.conftest import TenantFixture

WHEN = "El cliente pide factura o boleta con RUC"


def _add(db: Session, fixture: TenantFixture, titulo: str, **values: object) -> None:
    situation = SituationService(db, fixture.tenant_id).create(
        SituationInput(titulo=titulo, cuando_aplica=WHEN, que_hace="Pide el RUC.", **values),  # type: ignore[arg-type]
        by=None,
    )
    situation.estado = "activa"
    db.flush()


class TestSituationsArePerBusiness:
    async def test_the_selector_never_returns_another_business_s_situation(
        self, db: Session, tenant_a: TenantFixture, tenant_b: TenantFixture
    ) -> None:
        llm = OfflineLLMProvider(environment="test")
        _add(db, tenant_b, "De B")
        _add(db, tenant_b, "Siempre de B", siempre=True)
        await embed_pending(db, tenant_b.tenant_id, llm)

        chosen = await select_situations(
            db, tenant_a.tenant_id, stage="COBRO", message=WHEN, llm=llm
        )

        assert chosen == []

    async def test_each_business_gets_its_own(
        self, db: Session, tenant_a: TenantFixture, tenant_b: TenantFixture
    ) -> None:
        llm = OfflineLLMProvider(environment="test")
        _add(db, tenant_a, "De A")
        _add(db, tenant_b, "De B")
        for fixture in (tenant_a, tenant_b):
            await embed_pending(db, fixture.tenant_id, llm)

        a = await select_situations(db, tenant_a.tenant_id, stage="COBRO", message=WHEN, llm=llm)
        b = await select_situations(db, tenant_b.tenant_id, stage="COBRO", message=WHEN, llm=llm)

        assert [s.titulo for s in a] == ["De A"]
        assert [s.titulo for s in b] == ["De B"]

    def test_listing_and_reading_are_scoped(
        self, db: Session, tenant_a: TenantFixture, tenant_b: TenantFixture
    ) -> None:
        _add(db, tenant_b, "De B")
        theirs = SituationRepository(db, tenant_b.tenant_id).listing()[0]

        assert SituationRepository(db, tenant_a.tenant_id).listing() == []
        assert SituationRepository(db, tenant_a.tenant_id).get(theirs.id) is None

    async def test_embedding_one_business_leaves_the_other_pending(
        self, db: Session, tenant_a: TenantFixture, tenant_b: TenantFixture
    ) -> None:
        llm = OfflineLLMProvider(environment="test")
        _add(db, tenant_a, "De A")
        _add(db, tenant_b, "De B")

        assert await embed_pending(db, tenant_a.tenant_id, llm) == 1
        assert (
            len(SituationRepository(db, tenant_b.tenant_id).needing_embedding(llm.embedding_model))
            == 1
        )

    def test_a_repository_cannot_write_for_another_tenant(
        self, db: Session, tenant_a: TenantFixture, tenant_b: TenantFixture
    ) -> None:
        with pytest.raises(TenantScopeError):
            SituationRepository(db, tenant_a.tenant_id).add(
                tenant_id=tenant_b.tenant_id, titulo="x", cuando_aplica="x", que_hace="x"
            )
