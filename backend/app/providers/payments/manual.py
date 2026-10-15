"""Payments collected by hand: transfer or Yape, recorded by an admin (Feature 009)."""

from __future__ import annotations

from decimal import Decimal

from app.providers.payments.base import CheckoutInfo

PROVIDER = "manual"


class ManualPaymentProvider:
    name = PROVIDER

    def __init__(self, instructions: str) -> None:
        self._instructions = instructions.strip()

    def checkout(self, *, reference: str, amount_usd: Decimal, concept: str) -> CheckoutInfo:
        return CheckoutInfo(
            provider=PROVIDER,
            reference=reference,
            amount_usd=amount_usd,
            instructions=self._instructions,
        )
