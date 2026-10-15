"""Subscription plans.

Plan limits live in the database rather than in code so that features which
enforce them (seats, AI message quota, RAG budget) read one source of truth
instead of hardcoding tiers. `trial` is an ordinary row here, not a special
case in the application.
"""

from __future__ import annotations

from decimal import Decimal

from sqlalchemy import ARRAY, BigInteger, Boolean, CheckConstraint, Integer, Numeric, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin


class Plan(Base, TimestampMixin):
    """A subscription tier and the quotas it grants."""

    __tablename__ = "plans"
    __table_args__ = (
        # A plan that allowed no level would leave its tenants with no model to
        # answer with; an unknown level could never be resolved to one.
        CheckConstraint(
            "cardinality(allowed_intelligence) >= 1 "
            "AND allowed_intelligence <@ ARRAY['economico', 'medio', 'mejor']::text[]",
            name="allowed_intelligence",
        ),
    )

    # Natural key ('trial', 'emprende', 'crece', 'pro') rather than a UUID:
    # plans are referenced by name in configuration and in support tickets.
    id: Mapped[str] = mapped_column(Text, primary_key=True)
    name: Mapped[str] = mapped_column(Text, nullable=False)

    max_seats: Mapped[int] = mapped_column(Integer, nullable=False)
    max_ai_messages: Mapped[int] = mapped_column(Integer, nullable=False)
    max_rag_chars: Mapped[int] = mapped_column(BigInteger, nullable=False)
    max_knowledge_files: Mapped[int] = mapped_column(Integer, nullable=False)

    # Model tiers a tenant on this plan may pick (Feature 004).
    allowed_intelligence: Mapped[list[str]] = mapped_column(
        ARRAY(Text), nullable=False, server_default="{economico}"
    )

    # Only meaningful for the 'trial' plan.
    trial_days: Mapped[int | None] = mapped_column(Integer, nullable=True)

    # List prices (Feature 009). NULL means the plan is not bought (the trial).
    # Money is always Decimal, never float (Constitution).
    price_usd_month: Mapped[Decimal | None] = mapped_column(Numeric(8, 2), nullable=True)
    price_usd_year: Mapped[Decimal | None] = mapped_column(Numeric(8, 2), nullable=True)

    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="true")
