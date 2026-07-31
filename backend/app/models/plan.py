"""Subscription plans.

Plan limits live in the database rather than in code so that features which
enforce them (seats, AI message quota, RAG budget) read one source of truth
instead of hardcoding tiers. `trial` is an ordinary row here, not a special
case in the application.
"""

from __future__ import annotations

from decimal import Decimal

from sqlalchemy import BigInteger, Boolean, Integer, Numeric, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin


class Plan(Base, TimestampMixin):
    """A subscription tier and the quotas it grants."""

    __tablename__ = "plans"

    # Natural key ('trial', 'emprende', 'crece', 'pro') rather than a UUID:
    # plans are referenced by name in configuration and in support tickets.
    id: Mapped[str] = mapped_column(Text, primary_key=True)
    name: Mapped[str] = mapped_column(Text, nullable=False)

    max_seats: Mapped[int] = mapped_column(Integer, nullable=False)
    max_ai_messages: Mapped[int] = mapped_column(Integer, nullable=False)
    max_rag_chars: Mapped[int] = mapped_column(BigInteger, nullable=False)
    max_knowledge_files: Mapped[int] = mapped_column(Integer, nullable=False)

    # Only meaningful for the 'trial' plan.
    trial_days: Mapped[int | None] = mapped_column(Integer, nullable=True)

    # Reference price only. Actual billing arrives with Feature 009.
    # Money is always Decimal, never float (Constitution).
    price_usd_month: Mapped[Decimal | None] = mapped_column(Numeric(8, 2), nullable=True)

    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="true")
