"""The CRM's API: board, moves, assignment, notes, the day's list, CSV and numbers (Feature 012)."""

from __future__ import annotations

# ruff: noqa: F811
import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

from httpx import AsyncClient
from sqlalchemy.orm import Session

from app.models.channel import Contact
from app.models.conversation import Conversation
from app.models.conversation_state import FunnelCode
from app.models.crm import Lead
from app.process.state import StateRepo
from app.process.templates import TEMPLATES
from app.process.templates.base import Stage
from app.repositories.orders import OrderRepository
from tests.integration.test_orders_api import operator_for

P = "/api/v1/tenant/pipeline"
L = "/api/v1/tenant/leads"


def _lead(
    db: Session,
    tenant_id: uuid.UUID,
    *,
    name: str = "Luis",
    total: str | None = None,
    stage: Stage = Stage.PEDIDO,
) -> Conversation:
    contact = Contact(
        tenant_id=tenant_id,
        phone_e164=f"+5198{uuid.uuid4().int % 10**7:07d}",
        profile_name=name,
        last_inbound_at=datetime.now(UTC),
    )
    db.add(contact)
    db.flush()
    conversation = Conversation(
        tenant_id=tenant_id, channel="whatsapp", contact_id=contact.id, status="open"
    )
    db.add(conversation)
    db.flush()
    states = StateRepo(db, tenant_id)
    state = states.get_or_create(conversation, TEMPLATES["productos"])
    if total is not None:
        OrderRepository(db, tenant_id).add_numbered(
            conversation_id=conversation.id,
            contact_id=contact.id,
            moneda="PEN",
            subtotal=Decimal(total),
            total=Decimal(total),
        )
        db.flush()
    states.save(
        conversation.id,
        stage=stage,
        funnel=FunnelCode.NEGOCIACION,
        facts_patch={"vio_productos": True},
        reason="turno",
        expected_version=state.version,
    )
    db.commit()
    return conversation


async def _setup(
    api: AsyncClient, mail: Any, db: Session
) -> tuple[uuid.UUID, dict[str, str], dict[str, str]]:
    account, owner, operator = await operator_for(api, mail, db)
    return uuid.UUID(str(account["tenant_id"])), owner, operator


class TestTheBoard:
    async def test_pipeline_board_and_a_whole_human_cycle(
        self, api: AsyncClient, mail: Any, db: Session
    ) -> None:
        tenant_id, owner, _ = await _setup(api, mail, db)
        conversation = _lead(db, tenant_id, total="450.00")

        pipeline = (await api.get(P, headers=owner)).json()
        keys = [s["key"] for s in pipeline["stages"]]
        assert keys == ["nuevo", "interesado", "por_pagar", "ganado", "perdido"]
        assert {r["key"] for r in pipeline["lost_reasons"]} >= {"precio", "dejo_de_responder"}

        board = (await api.get(f"{L}/board", headers=owner)).json()
        interested = next(c for c in board["columns"] if c["stage"]["key"] == "interesado")
        (card,) = interested["items"]
        assert card["nombre"] == "Luis" and card["amount_estimated"] == "450.00"
        assert card["conversation_id"] == str(conversation.id)

        stages = {s["key"]: s["id"] for s in pipeline["stages"]}
        moved = await api.post(
            f"{L}/{card['id']}/move", headers=owner, json={"stage_id": stages["por_pagar"]}
        )
        assert moved.status_code == 200
        assert (
            moved.json()["lead"]["stage_key"] == "por_pagar"
            and moved.json()["lead"]["moved_by_person"]
        )

        noted = await api.post(
            f"{L}/{card['id']}/notes", headers=owner, json={"content": "Llamar el lunes"}
        )
        assert noted.status_code == 201

        lost = await api.post(
            f"{L}/{card['id']}/move",
            headers=owner,
            json={"stage_id": stages["perdido"], "lost_reason": "precio"},
        )
        assert lost.json()["lead"]["status"] == "lost"
        detail = (await api.get(f"{L}/{card['id']}", headers=owner)).json()
        assert [h["to_stage"] for h in detail["history"] if h["to_stage"]] == [
            "interesado",
            "por_pagar",
            "perdido",
        ]
        assert detail["history"][-1]["actor"] == "user" and detail["history"][-1]["actor_name"]
        assert [n["content"] for n in detail["notes"]] == ["Llamar el lunes"]

        unknown = await api.post(
            f"{L}/{card['id']}/move", headers=owner, json={"stage_id": str(uuid.uuid4())}
        )
        assert unknown.status_code == 422 and unknown.json()["error"] == "unknown_stage"

    async def test_the_operator_works_the_board_without_money_or_config(
        self, api: AsyncClient, mail: Any, db: Session
    ) -> None:
        tenant_id, owner, operator = await _setup(api, mail, db)
        _lead(db, tenant_id, total="99.00")
        cards = (await api.get(L, headers=operator)).json()["items"]
        assert cards[0]["amount_estimated"] is None and cards[0]["moneda"] is None
        assert "valor_pipeline" not in (await api.get(f"{L}/metrics", headers=operator)).json()
        assert "valor_pipeline" in (await api.get(f"{L}/metrics", headers=owner)).json()
        stages = (await api.get(P, headers=operator)).json()["stages"]
        refused = await api.put(
            f"{P}/stages",
            headers=operator,
            json=[{"key": s["key"], "label": s["label"]} for s in stages],
        )
        assert refused.status_code == 403
        csv = (await api.get(f"{L}/export.csv", headers=operator)).text
        assert "monto_estimado" not in csv.splitlines()[0]

    async def test_assign_discard_today_and_csv(
        self, api: AsyncClient, mail: Any, db: Session
    ) -> None:
        tenant_id, owner, _ = await _setup(api, mail, db)
        _lead(db, tenant_id, name="=HYPERLINK(1)", total="10.00")
        idle = _lead(db, tenant_id, name="Ana")
        lead_row = db.query(Lead).filter(Lead.conversation_id == idle.id).one()
        lead_row.last_activity_at = datetime.now(UTC) - timedelta(days=5)
        db.commit()

        today = (await api.get(f"{L}/today", headers=owner)).json()
        assert [c["nombre"] for c in today["items"]] == ["Ana"]

        team = (await api.get("/api/v1/tenant/team", headers=owner)).json()
        me = next(m for m in team["members"] if m["role"] == "owner")
        assigned = await api.post(
            f"{L}/{lead_row.id}/assign", headers=owner, json={"user_id": me["user_id"]}
        )
        assert assigned.json()["lead"]["assigned_name"]
        mine = (await api.get(L, headers=owner, params={"asignado": "me"})).json()
        assert [c["nombre"] for c in mine["items"]] == ["Ana"]
        stranger = await api.post(
            f"{L}/{lead_row.id}/assign", headers=owner, json={"user_id": str(uuid.uuid4())}
        )
        assert stranger.status_code == 422

        assert (await api.post(f"{L}/{lead_row.id}/discard", headers=owner)).status_code == 204
        assert (await api.get(L, headers=owner)).json()["total"] == 1

        csv = (await api.get(f"{L}/export.csv", headers=owner)).text
        assert "'=HYPERLINK(1)" in csv and "monto_estimado" in csv.splitlines()[0]

    async def test_stages_and_reasons_are_configurable(
        self, api: AsyncClient, mail: Any, db: Session
    ) -> None:
        _, owner, _ = await _setup(api, mail, db)
        stages = (await api.get(P, headers=owner)).json()["stages"]
        body = [{"key": s["key"], "label": s["label"]} for s in stages]
        body.insert(2, {"label": "Cotización enviada"})
        saved = await api.put(f"{P}/stages", headers=owner, json=body)
        assert saved.status_code == 200
        assert [s["label"] for s in saved.json()["stages"]][2] == "Cotización enviada"
        broken = await api.put(f"{P}/stages", headers=owner, json=body[1:])
        assert broken.status_code == 422 and broken.json()["error"] == "fixed_stages"

        reasons = await api.put(
            f"{P}/lost-reasons",
            headers=owner,
            json=[{"key": "precio", "label": "Le pareció caro"}, {"label": "Compró en otro lado"}],
        )
        labels = {r["key"]: r["label"] for r in reasons.json()["lost_reasons"]}
        assert (
            labels["precio"] == "Le pareció caro"
            and labels["compro_en_otro_lado"] == "Compró en otro lado"
        )

    async def test_metrics(self, api: AsyncClient, mail: Any, db: Session) -> None:
        tenant_id, owner, _ = await _setup(api, mail, db)
        _lead(db, tenant_id, total="300.00", stage=Stage.COBRO)
        report = (await api.get(f"{L}/metrics", headers=owner)).json()
        assert report["nuevos"] == 1 and report["activos_por_etapa"]["por_pagar"] == 1
        assert report["valor_pipeline"] == {"PEN": "300.00"}
        assert report["llegaron_a"]["por_pagar"] == 1
