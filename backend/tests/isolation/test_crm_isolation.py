"""A lead, its history and the board never cross businesses (Feature 012)."""

from __future__ import annotations

import uuid
from typing import Any

from httpx import AsyncClient
from sqlalchemy.orm import Session

from app.process.templates.base import Stage
from tests.integration.test_crm_api import L, P, _lead
from tests.integration.test_orders_api import operator_for


async def test_another_business_sees_and_touches_nothing(
    api: AsyncClient, mail: Any, db: Session
) -> None:
    account_a, owner_a, _ = await operator_for(api, mail, db)
    account_b, owner_b, _ = await operator_for(api, mail, db)
    _lead(
        db,
        uuid.UUID(str(account_b["tenant_id"])),
        name="Secreto",
        total="100.00",
        stage=Stage.COBRO,
    )
    lead_b = (await api.get(L, headers=owner_b)).json()["items"][0]
    stages_a = {s["key"]: s["id"] for s in (await api.get(P, headers=owner_a)).json()["stages"]}
    stages_b = {s["key"]: s["id"] for s in (await api.get(P, headers=owner_b)).json()["stages"]}
    assert set(stages_a.values()).isdisjoint(stages_b.values())

    assert (await api.get(L, headers=owner_a)).json()["total"] == 0
    assert (await api.get(L, headers=owner_a, params={"search": "Secreto"})).json()["total"] == 0
    board_a = (await api.get(f"{L}/board", headers=owner_a)).json()
    assert all(not c["items"] for c in board_a["columns"])
    assert (await api.get(f"{L}/today", headers=owner_a, params={"dias": 1})).json()["total"] == 0
    assert "Secreto" not in (await api.get(f"{L}/export.csv", headers=owner_a)).text
    assert (await api.get(f"{L}/metrics", headers=owner_a)).json()["nuevos"] == 0

    lid = lead_b["id"]
    assert (await api.get(f"{L}/{lid}", headers=owner_a)).status_code == 404
    assert (
        await api.post(f"{L}/{lid}/move", headers=owner_a, json={"stage_id": stages_a["ganado"]})
    ).status_code == 404
    # B's lead cannot be moved into A's stage either, nor A's lead into B's.
    assert (
        await api.post(f"{L}/{lid}/move", headers=owner_b, json={"stage_id": stages_a["ganado"]})
    ).status_code == 422
    assert (
        await api.post(f"{L}/{lid}/assign", headers=owner_a, json={"user_id": None})
    ).status_code == 404
    assert (await api.post(f"{L}/{lid}/discard", headers=owner_a)).status_code == 404
    assert (
        await api.post(f"{L}/{lid}/notes", headers=owner_a, json={"content": "x"})
    ).status_code == 404
    assert (await api.get(f"{L}/{lid}", headers=owner_b)).status_code == 200
