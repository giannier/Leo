"""The weekly review and the generated test questions (feature 019, phase C)."""

from __future__ import annotations

# ruff: noqa: F811
import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

import pytest
from httpx import AsyncClient
from sqlalchemy.orm import Session

from app.models.business import AgentConfig, Product
from app.models.conversation import Conversation, Message
from app.services import reviews, test_questions
from tests.integration.test_orders_api import operator_for
from tests.integration.test_turn_pipeline import _conversation, _tenant

NOW = datetime(2026, 10, 21, 15, 0, tzinfo=UTC)


def _talk(
    db: Session, tenant_id: uuid.UUID, conversation: Conversation, *, agent: bool = True
) -> None:
    conversation.last_message_at = NOW - timedelta(days=1)
    db.add(
        Message(
            tenant_id=tenant_id,
            conversation_id=conversation.id,
            direction="inbound",
            author="customer",
            content="Hola, ¿cuánto cuesta?",
            msg_type="text",
        )
    )
    if agent:
        db.add(
            Message(
                tenant_id=tenant_id,
                conversation_id=conversation.id,
                direction="outbound",
                author="agent",
                content="Cuesta S/ 35.",
                msg_type="text",
            )
        )
    db.flush()


def _conversations(db: Session, tenant_id: uuid.UUID, tenant: Any, n: int) -> list[Conversation]:
    made = []
    for _ in range(n):
        c = _conversation(db, tenant, channel="whatsapp")
        _talk(db, tenant_id, c)
        made.append(c)
    return made


class TestTheSample:
    def test_it_is_the_same_all_week_and_bounded(self, db: Session) -> None:
        tenant = _tenant(db, template="servicios")
        _conversations(db, tenant.id, tenant, 14)

        first = [r.conversation.id for r in reviews.sample(db, tenant.id, NOW)]
        again = [
            r.conversation.id for r in reviews.sample(db, tenant.id, NOW + timedelta(hours=30))
        ]
        next_week = [
            r.conversation.id for r in reviews.sample(db, tenant.id, NOW + timedelta(days=7))
        ]

        assert len(first) == 10 and first == again
        assert reviews.week_label(NOW) != reviews.week_label(NOW + timedelta(days=7))
        assert next_week == []  # nothing happened in the window of next week

    def test_only_real_conversations_where_the_assistant_spoke(self, db: Session) -> None:
        tenant = _tenant(db, template="servicios")
        real = _conversations(db, tenant.id, tenant, 1)[0]
        silent = _conversation(db, tenant, channel="whatsapp")
        _talk(db, tenant.id, silent, agent=False)
        simulator = _conversation(db, tenant, channel="sandbox")
        _talk(db, tenant.id, simulator)
        old = _conversation(db, tenant, channel="whatsapp")
        _talk(db, tenant.id, old)
        old.last_message_at = NOW - timedelta(days=20)
        db.flush()

        chosen = [r.conversation.id for r in reviews.sample(db, tenant.id, NOW)]

        assert chosen == [real.id]

    def test_nobody_reviews_somebody_else_s_conversations(self, db: Session) -> None:
        mine, theirs = _tenant(db, template="servicios"), _tenant(db, template="servicios")
        _conversations(db, theirs.id, theirs, 3)

        assert reviews.sample(db, mine.id, NOW) == []


class TestRating:
    def test_rate_change_and_the_reasons_only_when_it_was_not_good(self, db: Session) -> None:
        tenant = _tenant(db, template="servicios")
        (conversation,) = _conversations(db, tenant.id, tenant, 1)
        user = uuid.uuid4()
        from app.models.user import User

        db.add(
            User(
                id=user,
                email=f"r-{user.hex[:6]}@example.com",
                password_hash="x",
                full_name="Revisora",
            )
        )
        db.flush()

        bad = reviews.rate(
            db,
            tenant.id,
            conversation.id,
            rating=-1,
            motivos=["invento_un_dato", "invento_un_dato", "no_existe"],
            nota="  Inventó el precio ",
            user_id=user,
        )
        assert (bad.rating, bad.motivos, bad.nota) == (-1, ["invento_un_dato"], "Inventó el precio")

        good = reviews.rate(
            db, tenant.id, conversation.id, rating=1, motivos=["tono"], nota=None, user_id=user
        )
        assert good.id == bad.id and (good.rating, good.motivos, good.nota) == (1, [], None)
        shown = reviews.sample(db, tenant.id, NOW)[0]
        assert shown.review is not None and shown.review.rating == 1

    def test_refused_for_a_stranger_conversation_or_an_invented_rating(self, db: Session) -> None:
        mine, theirs = _tenant(db, template="servicios"), _tenant(db, template="servicios")
        (other,) = _conversations(db, theirs.id, theirs, 1)

        with pytest.raises(reviews.ReviewError) as missing:
            reviews.rate(
                db, mine.id, other.id, rating=1, motivos=[], nota=None, user_id=uuid.uuid4()
            )
        with pytest.raises(reviews.ReviewError) as invented:
            reviews.rate(
                db, theirs.id, other.id, rating=5, motivos=[], nota=None, user_id=uuid.uuid4()
            )

        assert (missing.value.code, invented.value.code) == ("not_found", "invalid_rating")

    def test_the_summary_counts_and_names_what_went_wrong_most(self, db: Session) -> None:
        tenant = _tenant(db, template="servicios")
        user = uuid.uuid4()
        from app.models.user import User

        db.add(
            User(id=user, email=f"s-{user.hex[:6]}@example.com", password_hash="x", full_name="R")
        )
        db.flush()
        marks = [(1, []), (1, []), (0, ["tono"]), (-1, ["invento_un_dato", "tono"])]
        for rating, motivos in marks:
            c = _conversations(db, tenant.id, tenant, 1)[0]
            reviews.rate(
                db, tenant.id, c.id, rating=rating, motivos=motivos, nota=None, user_id=user
            )

        result = reviews.summary(db, tenant.id, datetime.now(UTC))

        assert (result["total"], result["bien"], result["regular"], result["mal"]) == (4, 2, 1, 1)
        assert result["porcentaje_bien"] == 50 and result["fallas"] == {
            "tono": 2,
            "invento_un_dato": 1,
        }


class TestTheApi:
    async def test_a_reviewer_reads_and_rates_and_an_operator_cannot(
        self, api: AsyncClient, mail: Any, db: Session
    ) -> None:
        account, owner, operator = await operator_for(api, mail, db)
        tenant_id = uuid.UUID(str(account["tenant_id"]))
        tenant = _tenant_row(db, tenant_id)
        (conversation,) = _conversations(db, tenant_id, tenant, 1)
        conversation.last_message_at = datetime.now(UTC) - timedelta(hours=3)
        db.commit()

        refused = await api.get("/api/v1/assistant/review", headers=operator)
        page = (await api.get("/api/v1/assistant/review", headers=owner)).json()
        marked = await api.put(
            f"/api/v1/assistant/review/{conversation.id}",
            headers=owner,
            json={"rating": -1, "motivos": ["no_resolvio"], "nota": "No respondió lo que preguntó"},
        )
        invalid = await api.put(
            f"/api/v1/assistant/review/{conversation.id}", headers=owner, json={"rating": 3}
        )
        after = (await api.get("/api/v1/assistant/review", headers=owner)).json()

        assert refused.status_code == 403
        assert page["progreso"] == {"revisadas": 0, "total": 1}
        assert [m["de"] for m in page["items"][0]["mensajes"]] == ["cliente", "asistente"]
        assert marked.status_code == 200 and marked.json()["motivos"] == ["no_resolvio"]
        assert invalid.status_code == 400
        assert after["progreso"] == {"revisadas": 1, "total": 1} and after["resumen"]["mal"] == 1


def _tenant_row(db: Session, tenant_id: uuid.UUID) -> Any:
    from app.models.tenant import Tenant

    tenant = db.get(Tenant, tenant_id)
    assert tenant is not None
    return tenant


class TestTheTestQuestions:
    def test_they_come_from_what_the_business_loaded(self, db: Session) -> None:
        tenant = _tenant(db, template="servicios")
        config = db.get(AgentConfig, tenant.id)
        assert config is not None
        config.capacidades = ["atender", "vender", "cotizar", "agendar"]
        db.add_all(
            [
                Product(
                    tenant_id=tenant.id,
                    name="Hosting básico",
                    price=Decimal("30"),
                    modo_venta="precio_fijo",
                ),
                Product(
                    tenant_id=tenant.id,
                    name="Sistema a medida",
                    price=Decimal("0"),
                    modo_venta="cotizacion",
                ),
                Product(
                    tenant_id=tenant.id,
                    name="Consulta",
                    price=Decimal("80"),
                    agendable=True,
                    duracion_min=60,
                ),
            ]
        )
        db.flush()

        asked: dict[str, list[str]] = {}
        for q in test_questions.build(db, tenant.id):
            asked.setdefault(q.sobre, []).append(q.texto)

        assert (
            "Hosting básico" in asked["precio"][0] and "Sistema a medida" in asked["cotizacion"][0]
        )
        assert "Consulta" in asked["agenda"][0]
        assert {"horario", "objecion", "derivacion", "transparencia"} <= set(asked)

    def test_a_business_with_nothing_still_gets_the_universal_ones(self, db: Session) -> None:
        tenant = _tenant(db, template="servicios")

        asked = [q.sobre for q in test_questions.build(db, tenant.id)]

        assert asked == ["horario", "objecion", "derivacion", "transparencia"]
