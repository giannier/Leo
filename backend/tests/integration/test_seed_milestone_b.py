"""`make seed-milestone-b`: the data of `milestones/B.md` section 2, safe to run twice."""

from __future__ import annotations

from scripts.seed_milestone_b import seed
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.business import PaymentMethodBusiness, Product
from app.models.sales_config import BusinessInfo, Promotion


def _count(db: Session, model: type, tenant_id: object) -> int:
    return int(
        db.execute(
            select(func.count()).select_from(model).where(model.tenant_id == tenant_id)  # type: ignore[attr-defined]
        ).scalar_one()
    )


def test_it_loads_the_shop_and_running_it_again_duplicates_nothing(db: Session) -> None:
    shop = seed(db)
    again = seed(db)

    assert again.id == shop.id
    assert _count(db, Product, shop.id) == 3
    assert _count(db, Promotion, shop.id) == 1
    assert _count(db, PaymentMethodBusiness, shop.id) == 1
    assert _count(db, BusinessInfo, shop.id) == 1
