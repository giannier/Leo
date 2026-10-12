"""One business never reads or changes another's business information, FAQs or
promotions (Feature 004, TASK-403).

`business_info` is keyed by `(tenant_id, section)`, and the section names are
the same for everybody: `entregas` of tenant A and `entregas` of tenant B are
different rows with the same name, so any lookup by section alone returns the
wrong business's delivery zones.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

import pytest
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models.sales_config import Promotion
from app.repositories.base import TenantScopeError
from app.repositories.sales_config import (
    BusinessInfoRepository,
    FaqRepository,
    PromotionRepository,
)
from tests.isolation.conftest import TenantFixture

NOW = datetime(2026, 10, 10, 12, 0, tzinfo=UTC)


def _promotion(**overrides: Any) -> dict[str, Any]:
    values: dict[str, Any] = {
        "nombre": "10 % de descuento",
        "tipo": "porcentaje",
        "valor": Decimal("10.00"),
    }
    values.update(overrides)
    return values


@pytest.fixture
def seeded(db: Session, tenant_a: TenantFixture, tenant_b: TenantFixture) -> None:
    for label, fixture in (("A", tenant_a), ("B", tenant_b)):
        BusinessInfoRepository(db, fixture.tenant_id).upsert(
            "entregas", {"hace_delivery": label == "A", "zonas": []}
        )
        FaqRepository(db, fixture.tenant_id).add(
            pregunta=f"Pregunta {label}", respuesta=f"Respuesta {label}"
        )
        PromotionRepository(db, fixture.tenant_id).add(**_promotion(nombre=f"Promo {label}"))


class TestBusinessInfoIsPerTenant:
    def test_the_same_section_name_reads_your_own_row(
        self, db: Session, tenant_a: TenantFixture, tenant_b: TenantFixture, seeded: None
    ) -> None:
        mine = BusinessInfoRepository(db, tenant_a.tenant_id).section("entregas")
        theirs = BusinessInfoRepository(db, tenant_b.tenant_id).section("entregas")

        assert mine is not None and theirs is not None
        assert mine.data["hace_delivery"] is True
        assert theirs.data["hace_delivery"] is False

    def test_sections_lists_only_your_own(
        self, db: Session, tenant_a: TenantFixture, tenant_b: TenantFixture, seeded: None
    ) -> None:
        assert BusinessInfoRepository(db, tenant_a.tenant_id).sections() == {
            "entregas": {"hace_delivery": True, "zonas": []}
        }

    def test_saving_a_section_does_not_touch_the_other_tenants(
        self, db: Session, tenant_a: TenantFixture, tenant_b: TenantFixture, seeded: None
    ) -> None:
        BusinessInfoRepository(db, tenant_a.tenant_id).upsert(
            "entregas", {"hace_delivery": False, "zonas": [{"nombre": "Centro"}]}
        )

        theirs = BusinessInfoRepository(db, tenant_b.tenant_id).section("entregas")
        assert theirs is not None
        assert theirs.data == {"hace_delivery": False, "zonas": []}

    def test_counting_is_scoped(
        self, db: Session, tenant_a: TenantFixture, tenant_b: TenantFixture, seeded: None
    ) -> None:
        assert BusinessInfoRepository(db, tenant_a.tenant_id).count() == 1

    def test_a_repository_cannot_write_for_another_tenant(
        self, db: Session, tenant_a: TenantFixture, tenant_b: TenantFixture
    ) -> None:
        with pytest.raises(TenantScopeError):
            BusinessInfoRepository(db, tenant_a.tenant_id).add(
                tenant_id=tenant_b.tenant_id, section="contacto", data={}
            )

    def test_reading_by_a_row_id_is_not_offered(self, db: Session, tenant_a: TenantFixture) -> None:
        with pytest.raises(NotImplementedError):
            BusinessInfoRepository(db, tenant_a.tenant_id).get(tenant_a.conversation_id)

    def test_an_unknown_section_is_rejected_by_the_database(
        self, db: Session, tenant_a: TenantFixture
    ) -> None:
        with pytest.raises(IntegrityError), db.begin_nested():
            BusinessInfoRepository(db, tenant_a.tenant_id).add(section="inventada", data={})

    def test_a_section_is_unique_per_tenant(self, db: Session, tenant_a: TenantFixture) -> None:
        repository = BusinessInfoRepository(db, tenant_a.tenant_id)
        repository.add(section="contacto", data={})

        with pytest.raises(IntegrityError), db.begin_nested():
            repository.add(section="contacto", data={})


class TestFaqsArePerTenant:
    def test_listing_never_includes_the_other_business(
        self, db: Session, tenant_a: TenantFixture, tenant_b: TenantFixture, seeded: None
    ) -> None:
        rows = FaqRepository(db, tenant_a.tenant_id).list()

        assert [row.pregunta for row in rows] == ["Pregunta A"]
        assert [row.pregunta for row in FaqRepository(db, tenant_a.tenant_id).active()] == [
            "Pregunta A"
        ]

    def test_a_foreign_faq_id_reads_as_missing(
        self, db: Session, tenant_a: TenantFixture, tenant_b: TenantFixture, seeded: None
    ) -> None:
        foreign = FaqRepository(db, tenant_b.tenant_id).list()[0]

        assert FaqRepository(db, tenant_a.tenant_id).get(foreign.id) is None

    def test_updating_a_foreign_faq_changes_nothing(
        self, db: Session, tenant_a: TenantFixture, tenant_b: TenantFixture, seeded: None
    ) -> None:
        foreign = FaqRepository(db, tenant_b.tenant_id).list()[0]

        assert (
            FaqRepository(db, tenant_a.tenant_id).update(foreign.id, respuesta="Hackeada") is None
        )
        db.refresh(foreign)
        assert foreign.respuesta == "Respuesta B"

    def test_deleting_a_foreign_faq_is_refused(
        self, db: Session, tenant_a: TenantFixture, tenant_b: TenantFixture, seeded: None
    ) -> None:
        foreign = FaqRepository(db, tenant_b.tenant_id).list()[0]

        assert FaqRepository(db, tenant_a.tenant_id).delete(foreign.id) is False
        assert FaqRepository(db, tenant_b.tenant_id).count() == 1

    def test_a_repository_cannot_write_for_another_tenant(
        self, db: Session, tenant_a: TenantFixture, tenant_b: TenantFixture
    ) -> None:
        with pytest.raises(TenantScopeError):
            FaqRepository(db, tenant_a.tenant_id).add(
                tenant_id=tenant_b.tenant_id, pregunta="¿Horario?", respuesta="Siempre"
            )

    def test_a_faq_can_be_indexed_from_your_own_knowledge_source(
        self, db: Session, tenant_a: TenantFixture
    ) -> None:
        faq = FaqRepository(db, tenant_a.tenant_id).add(
            pregunta="¿Envían?", respuesta="Sí", knowledge_source_id=tenant_a.source_id
        )

        assert faq.knowledge_source_id == tenant_a.source_id

    def test_the_length_limits_are_enforced_by_the_database(
        self, db: Session, tenant_a: TenantFixture
    ) -> None:
        repository = FaqRepository(db, tenant_a.tenant_id)

        with pytest.raises(IntegrityError), db.begin_nested():
            repository.add(pregunta="ab", respuesta="corta")
        with pytest.raises(IntegrityError), db.begin_nested():
            repository.add(pregunta="¿Válida?", respuesta="")
        with pytest.raises(IntegrityError), db.begin_nested():
            repository.add(pregunta="¿Válida?", respuesta="x" * 2001)


class TestPromotionsArePerTenant:
    def test_listing_never_includes_the_other_business(
        self, db: Session, tenant_a: TenantFixture, tenant_b: TenantFixture, seeded: None
    ) -> None:
        names = [row.nombre for row in PromotionRepository(db, tenant_a.tenant_id).list()]

        assert names == ["Promo A"]

    def test_the_promotions_in_force_are_only_yours(
        self, db: Session, tenant_a: TenantFixture, tenant_b: TenantFixture, seeded: None
    ) -> None:
        in_force = PromotionRepository(db, tenant_a.tenant_id).in_force(now=NOW)

        assert [row.nombre for row in in_force] == ["Promo A"]

    def test_a_foreign_promotion_id_reads_as_missing(
        self, db: Session, tenant_a: TenantFixture, tenant_b: TenantFixture, seeded: None
    ) -> None:
        foreign = PromotionRepository(db, tenant_b.tenant_id).list()[0]

        assert PromotionRepository(db, tenant_a.tenant_id).get(foreign.id) is None

    def test_updating_a_foreign_promotion_changes_nothing(
        self, db: Session, tenant_a: TenantFixture, tenant_b: TenantFixture, seeded: None
    ) -> None:
        foreign = PromotionRepository(db, tenant_b.tenant_id).list()[0]

        assert (
            PromotionRepository(db, tenant_a.tenant_id).update(foreign.id, valor=Decimal("99"))
            is None
        )
        db.refresh(foreign)
        assert foreign.valor == Decimal("10.00")

    def test_deleting_a_foreign_promotion_is_refused(
        self, db: Session, tenant_a: TenantFixture, tenant_b: TenantFixture, seeded: None
    ) -> None:
        foreign = PromotionRepository(db, tenant_b.tenant_id).list()[0]

        assert PromotionRepository(db, tenant_a.tenant_id).delete(foreign.id) is False

    def test_a_repository_cannot_write_for_another_tenant(
        self, db: Session, tenant_a: TenantFixture, tenant_b: TenantFixture
    ) -> None:
        with pytest.raises(TenantScopeError):
            PromotionRepository(db, tenant_a.tenant_id).add(
                tenant_id=tenant_b.tenant_id, **_promotion()
            )

    def test_the_date_window_is_respected(self, db: Session, tenant_a: TenantFixture) -> None:
        repository = PromotionRepository(db, tenant_a.tenant_id)
        repository.add(**_promotion(nombre="vigente", inicia_en=NOW - timedelta(days=1)))
        repository.add(**_promotion(nombre="futura", inicia_en=NOW + timedelta(days=1)))
        repository.add(**_promotion(nombre="vencida", termina_en=NOW - timedelta(seconds=1)))
        repository.add(**_promotion(nombre="apagada", is_active=False))

        assert [row.nombre for row in repository.in_force(now=NOW)] == ["vigente"]

    def test_money_comes_back_as_decimal(self, db: Session, tenant_a: TenantFixture) -> None:
        promotion = PromotionRepository(db, tenant_a.tenant_id).add(
            **_promotion(tipo="monto_fijo", valor=Decimal("12.50"), monto_minimo=Decimal("100"))
        )
        db.refresh(promotion)

        assert isinstance(promotion.valor, Decimal)
        assert promotion.valor == Decimal("12.50")
        assert promotion.monto_minimo == Decimal("100.00")

    def test_product_ids_and_categories_default_to_empty(
        self, db: Session, tenant_a: TenantFixture
    ) -> None:
        promotion = PromotionRepository(db, tenant_a.tenant_id).add(**_promotion())
        db.refresh(promotion)

        assert promotion.product_ids == []
        assert promotion.categorias == []
        assert promotion.uso == "automatica"
        assert promotion.aplica_a == "todo"


class TestPromotionRulesLiveInTheDatabase:
    """The CHECKs guard against a writer that skips the API. Each case is a
    promotion that would price an order wrongly if it were stored."""

    @pytest.mark.parametrize(
        "overrides",
        [
            {"tipo": "porcentaje", "valor": None},
            {"tipo": "porcentaje", "valor": Decimal("0")},
            {"tipo": "porcentaje", "valor": Decimal("100.01")},
            {"tipo": "monto_fijo", "valor": None},
            {"tipo": "monto_fijo", "valor": Decimal("-1")},
            {"tipo": "nxm", "valor": None, "n": None, "m": None},
            {"tipo": "nxm", "valor": None, "n": 2, "m": 2},
            {"tipo": "nxm", "valor": None, "n": 3, "m": 0},
            {"tipo": "precio_cantidad", "valor": None, "m": 3},
            {"tipo": "precio_cantidad", "valor": Decimal("50"), "m": None},
            {"tipo": "precio_cantidad", "valor": Decimal("50"), "m": 1},
            {"uso": "cierre"},
            {"uso": "cierre", "vigencia_horas": 0},
            {"uso": "cierre", "vigencia_horas": 169},
            {"uso": "inventado"},
            {"tipo": "inventado"},
            {"aplica_a": "inventado"},
        ],
    )
    def test_an_invalid_promotion_is_rejected(
        self, db: Session, tenant_a: TenantFixture, overrides: dict[str, Any]
    ) -> None:
        with pytest.raises(IntegrityError), db.begin_nested():
            PromotionRepository(db, tenant_a.tenant_id).add(**_promotion(**overrides))

    @pytest.mark.parametrize(
        "overrides",
        [
            {"tipo": "porcentaje", "valor": Decimal("100")},
            {"tipo": "monto_fijo", "valor": Decimal("0.01")},
            {"tipo": "nxm", "valor": None, "n": 2, "m": 1},
            {"tipo": "nxm", "valor": None, "n": 3, "m": 2},
            {"tipo": "precio_cantidad", "valor": Decimal("50"), "m": 2},
            {"uso": "cierre", "vigencia_horas": 1},
            {"uso": "cierre", "vigencia_horas": 168},
        ],
    )
    def test_a_valid_promotion_is_accepted(
        self, db: Session, tenant_a: TenantFixture, overrides: dict[str, Any]
    ) -> None:
        promotion = PromotionRepository(db, tenant_a.tenant_id).add(**_promotion(**overrides))

        assert isinstance(promotion, Promotion)
