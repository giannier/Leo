"""The `PaymentProvider` port (Feature 009, RF-001).

How a business pays a charge. v1 is manual (Giannier, 2026-10-14): the business
pays by transfer or Yape quoting the charge's reference, and an admin records the
payment. A gateway (Mercado Pago, Culqi) is another adapter behind this port that
answers with a checkout URL and confirms by webhook; the billing service does not
change.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Protocol, runtime_checkable


@dataclass(frozen=True)
class CheckoutInfo:
    """What the business is shown to pay one charge."""

    provider: str
    reference: str
    amount_usd: Decimal
    instructions: str
    # A gateway's hosted checkout; None when paying by hand.
    url: str | None = None


@runtime_checkable
class PaymentProvider(Protocol):
    name: str

    def checkout(self, *, reference: str, amount_usd: Decimal, concept: str) -> CheckoutInfo:
        """How to pay this amount, quoting `reference`."""
        ...
