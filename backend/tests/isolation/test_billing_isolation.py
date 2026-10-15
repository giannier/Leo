"""Billing never crosses businesses (Feature 009).

A's owner cannot see, cancel or download what belongs to B, and A's billing
service cannot pay, read or replay B's charge, even knowing its reference.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

import pytest
from httpx import AsyncClient
from sqlalchemy.orm import Session

from app.models.billing import Charge
from app.services.billing.service import BillingService, ChargeNotFound
from tests.integration.test_platform_admin import _register

B = "/api/v1/tenant/billing"


async def _owner(api: AsyncClient, mail: Any, db: Session) -> tuple[uuid.UUID, dict[str, str]]:
    headers, _ = await _register(api, mail, db)
    return uuid.UUID((await api.get("/api/v1/tenant", headers=headers)).json()["id"]), headers


class TestOneBusinessNeverReachesAnothersBilling:
    async def test_charges_payments_and_receipts_answer_404(
        self, api: AsyncClient, mail: Any, db: Session
    ) -> None:
        a_id, a = await _owner(api, mail, db)
        b_id, b = await _owner(api, mail, db)
        theirs = (await api.post(f"{B}/checkout", headers=b, json={"plan_id": "pro"})).json()
        service = BillingService(db, b_id, now=datetime.now(UTC))
        applied = service.apply_payment(
            reference=theirs["referencia"],
            provider="manual",
            provider_payment_id=f"manual:{uuid.uuid4().hex}",
            amount_usd=service.charges.by_reference(theirs["referencia"]).amount_usd,  # type: ignore[union-attr]
        )

        mine = (await api.get(B, headers=a)).json()
        cancel = await api.post(f"{B}/charges/{theirs['id']}/cancel", headers=a)
        receipt = await api.get(f"{B}/payments/{applied.payment.id}/receipt.pdf", headers=a)

        assert mine["pendientes"] == [] and mine["pagos"] == [] and mine["suscripcion"] is None
        assert cancel.status_code == 404 and receipt.status_code == 404
        assert db.get(Charge, uuid.UUID(theirs["id"])).status == "paid"  # type: ignore[union-attr]
        assert a_id != b_id

    async def test_a_reference_does_not_pay_across_businesses(
        self, api: AsyncClient, mail: Any, db: Session
    ) -> None:
        a_id, _ = await _owner(api, mail, db)
        _, b = await _owner(api, mail, db)
        theirs = (await api.post(f"{B}/checkout", headers=b, json={"plan_id": "crece"})).json()
        mine = BillingService(db, a_id, now=datetime.now(UTC))

        with pytest.raises(ChargeNotFound):
            mine.apply_payment(
                reference=theirs["referencia"],
                provider="manual",
                provider_payment_id="manual:cross-1",
                amount_usd=59,  # type: ignore[arg-type]
            )
        assert mine.charges.by_reference(theirs["referencia"]) is None
        assert mine.ledger.entries() == []
