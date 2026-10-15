"""What a model call cost (Feature 010, RF-002).

From `model_costs`, the price in force when the call happened: a price change
never rewrites the cost of past traces. A model with no price has no cost
(None), shown as such, never as zero: "free" and "unknown" are different facts.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.metrics import ModelCost

THOUSAND = Decimal(1000)
PLACES = Decimal("0.000001")


def price_at(session: Session, model: str, at: datetime) -> ModelCost | None:
    return session.execute(
        select(ModelCost)
        .where(ModelCost.model == model, ModelCost.valid_from <= at)
        .order_by(ModelCost.valid_from.desc())
        .limit(1)
    ).scalar_one_or_none()


def call_cost(
    session: Session, model: str, tokens_in: int, tokens_out: int, at: datetime
) -> Decimal | None:
    price = price_at(session, model, at)
    if price is None:
        return None
    cost = (
        Decimal(tokens_in) * price.input_per_1k_usd + Decimal(tokens_out) * price.output_per_1k_usd
    ) / THOUSAND
    return cost.quantize(PLACES)
