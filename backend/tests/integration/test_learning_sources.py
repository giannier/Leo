"""The sources of suggestions besides imports: a person took over, a sale, a lost sale (017, 3)."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.learning.budget import LearningBudget
from app.learning.importer import REDACTION_PROMPT
from app.learning.sources import (
    LOST_PROMPT,
    TAKEOVER_PROMPT,
    from_lost_sales,
    from_sales,
    from_takeovers,
)
from app.models.learning import LearningMark, Suggestion
from app.providers.llm import MockLLMProvider
from app.providers.llm.base import ChatResult
from app.repositories.conversation import MessageRepository
from app.repositories.orders import OrderRepository
from app.workers.learning import learning_sweep
from tests.integration.test_learning import _Borrowed, _on
from tests.integration.test_turn_pipeline import _conversation, _tenant

RULE = {
    "titulo": "Ofrece recojo en tienda",
    "cuando_aplica": "El cliente de provincia pregunta por el envío",
    "que_hace": "Ofrece que recoja en la tienda de Miraflores y explica el horario.",
}


class Model(MockLLMProvider):
    """Answers by what the system prompt says, and remembers what it was shown."""

    def __init__(self, *, takeover: Any = RULE, lost: Any = None) -> None:
        super().__init__()
        self.takeover, self.lost = takeover, lost if lost is not None else [RULE]
        self.seen: dict[str, list[str]] = {"takeover": [], "lost": [], "redaction": []}

    async def chat(self, messages: Any, **kwargs: Any) -> ChatResult:
        system, user = messages[0].content, messages[1].content
        if system == TAKEOVER_PROMPT:
            self.seen["takeover"].append(user)
            return ChatResult(content=json.dumps(self.takeover))
        if system == LOST_PROMPT:
            self.seen["lost"].append(user)
            return ChatResult(content=json.dumps(self.lost))
        if system == REDACTION_PROMPT:
            self.seen["redaction"].append(user)
            return ChatResult(content=user)
        return ChatResult(
            content=json.dumps([{"i": 0, "titulo": "Venta", "tema": "Cliente compra"}])
        )


def _talk(
    db: Session, tenant: Any, lines: list[tuple[str, str, str]], *, when: datetime | None = None
) -> Any:
    conversation = _conversation(db, tenant, channel="whatsapp")
    repo = MessageRepository(db, tenant.id)
    for direction, author, content in lines:
        repo.append(
            conversation_id=conversation.id, direction=direction, author=author, content=content
        )
    conversation.last_message_at = when or datetime.now(UTC)
    db.flush()
    return conversation


TAKEOVER_CHAT = [
    ("inbound", "customer", "Hola, vivo en Piura, ¿hacen envíos? Mi cel es 987654321"),
    ("outbound", "agent", "Hola, sí hacemos envíos a todo el Perú."),
    ("inbound", "customer", "Pero es muy caro el envío a provincia"),
    (
        "outbound",
        "human_operator",
        "Si quieres puedes recoger en nuestra tienda de Miraflores, abrimos de 9 a 6",
    ),
    ("inbound", "customer", "Ah perfecto, paso el sábado"),
    ("outbound", "human_operator", "Te esperamos, preguntas por Laura"),
]


def _order(
    db: Session,
    tenant: Any,
    conversation: Any,
    *,
    estado: str,
    confirmed: bool,
    sandbox: bool = False,
) -> Any:
    order = OrderRepository(db, tenant.id).add_numbered(
        conversation_id=conversation.id,
        contact_id=conversation.contact_id,
        moneda="PEN",
        subtotal=Decimal("70"),
        total=Decimal("70"),
        estado=estado,
        is_sandbox=sandbox,
        confirmed_at=datetime.now(UTC) if confirmed else None,
    )
    db.flush()
    return order


class TestAPersonTookOver:
    async def test_what_the_team_did_becomes_a_rule_without_the_customers_data(
        self, db: Session
    ) -> None:
        tenant = _tenant(db, template="productos")
        _on(db, tenant)
        conversation = _talk(db, tenant, TAKEOVER_CHAT)
        model = Model()

        created = await from_takeovers(db, tenant.id, model)

        (suggestion,) = db.execute(select(Suggestion)).scalars().all()
        assert (
            created == 1
            and suggestion.fuente == "toma_de_control"
            and suggestion.tipo == "situacion"
        )
        assert suggestion.conversaciones == [conversation.id]
        assert suggestion.propuesta["titulo"] == RULE["titulo"]
        shown = model.seen["takeover"][0]
        assert "987654321" not in shown and "[teléfono]" in shown
        assert "equipo:" in shown and "asistente:" in shown and "cliente:" in shown

    async def test_a_conversation_is_analysed_once(self, db: Session) -> None:
        tenant = _tenant(db, template="productos")
        _on(db, tenant)
        _talk(db, tenant, TAKEOVER_CHAT)
        model = Model()

        await from_takeovers(db, tenant.id, model)
        again = await from_takeovers(db, tenant.id, model)

        assert again == 0 and len(model.seen["takeover"]) == 1
        assert db.execute(select(LearningMark)).scalar_one().fuente == "toma_de_control"

    async def test_nothing_general_is_marked_and_costs_one_call(self, db: Session) -> None:
        tenant = _tenant(db, template="productos")
        _on(db, tenant)
        _talk(db, tenant, TAKEOVER_CHAT)

        created = await from_takeovers(db, tenant.id, Model(takeover=None))

        assert created == 0 and db.execute(select(Suggestion)).first() is None
        assert db.execute(select(LearningMark)).first() is not None
        assert LearningBudget(db, tenant.id).used == 1

    async def test_a_rule_that_still_carries_personal_data_is_not_kept(self, db: Session) -> None:
        tenant = _tenant(db, template="productos")
        _on(db, tenant)
        _talk(db, tenant, TAKEOVER_CHAT)
        leaky = {**RULE, "que_hace": "Escribe al 987654321 y pregunta por Laura."}

        created = await from_takeovers(db, tenant.id, Model(takeover=leaky))

        assert created == 0 and db.execute(select(Suggestion)).first() is None

    async def test_only_a_conversation_the_assistant_and_then_a_person_spoke_in_counts(
        self, db: Session
    ) -> None:
        tenant = _tenant(db, template="productos")
        _on(db, tenant)
        _talk(db, tenant, [line for line in TAKEOVER_CHAT if line[1] != "agent"])  # a person alone
        _talk(
            db, tenant, [line for line in TAKEOVER_CHAT if line[1] != "human_operator"]
        )  # the assistant alone
        _talk(db, tenant, TAKEOVER_CHAT, when=datetime.now(UTC) - timedelta(days=30))  # too old
        model = Model()

        created = await from_takeovers(db, tenant.id, model)

        assert created == 0 and model.seen["takeover"] == []

    async def test_a_spent_week_makes_no_call(self, db: Session) -> None:
        tenant = _tenant(db, template="productos")
        _on(db, tenant, ceiling=0)
        _talk(db, tenant, TAKEOVER_CHAT)
        model = Model()

        created = await from_takeovers(db, tenant.id, model)

        assert created == 0 and model.seen["takeover"] == []
        assert db.execute(select(LearningMark)).first() is None  # it waits for next week

    async def test_another_businesss_conversations_are_never_read(self, db: Session) -> None:
        mine, theirs = _tenant(db, template="productos"), _tenant(db, template="productos")
        _on(db, mine)
        _talk(db, theirs, TAKEOVER_CHAT)
        model = Model()

        assert await from_takeovers(db, mine.id, model) == 0 and model.seen["takeover"] == []


class TestAConfirmedSale:
    async def test_the_conversation_becomes_an_anonymised_example(self, db: Session) -> None:
        tenant = _tenant(db, template="productos")
        _on(db, tenant)
        conversation = _talk(db, tenant, TAKEOVER_CHAT)
        _order(db, tenant, conversation, estado="completado", confirmed=True)
        model = Model()

        created = await from_sales(db, tenant.id, model)

        (suggestion,) = db.execute(select(Suggestion)).scalars().all()
        assert created == 1 and (suggestion.tipo, suggestion.fuente) == (
            "ejemplo",
            "venta_confirmada",
        )
        assert suggestion.conversaciones == [conversation.id]
        kept = json.dumps(suggestion.propuesta, ensure_ascii=False)
        assert "987654321" not in kept and "[teléfono]" in kept
        assert {t["de"] for t in suggestion.propuesta["mensajes"]} == {"cliente", "vendedor"}
        assert db.execute(select(LearningMark)).scalar_one().fuente == "venta_confirmada"

    async def test_a_sale_is_taken_once_and_the_simulator_never(self, db: Session) -> None:
        tenant = _tenant(db, template="productos")
        _on(db, tenant)
        real = _talk(db, tenant, TAKEOVER_CHAT)
        fake = _talk(db, tenant, TAKEOVER_CHAT)
        _order(db, tenant, real, estado="completado", confirmed=True)
        _order(db, tenant, fake, estado="completado", confirmed=True, sandbox=True)
        model = Model()

        first = await from_sales(db, tenant.id, model)
        second = await from_sales(db, tenant.id, model)

        assert (first, second) == (1, 0)


class TestALostSale:
    async def test_the_unsolved_objections_become_rules_in_one_call(self, db: Session) -> None:
        tenant = _tenant(db, template="productos")
        _on(db, tenant)
        threads = [_talk(db, tenant, TAKEOVER_CHAT) for _ in range(3)]
        for thread in threads:
            _order(db, tenant, thread, estado="descartado", confirmed=False)
        model = Model()

        created = await from_lost_sales(db, tenant.id, model)

        (suggestion,) = db.execute(select(Suggestion)).scalars().all()
        assert created == 1 and suggestion.fuente == "venta_perdida"
        assert set(suggestion.conversaciones) == {t.id for t in threads}
        assert len(model.seen["lost"]) == 1 and "987654321" not in model.seen["lost"][0]
        assert len(db.execute(select(LearningMark)).scalars().all()) == 3

    async def test_one_lost_sale_alone_waits_for_more(self, db: Session) -> None:
        tenant = _tenant(db, template="productos")
        _on(db, tenant)
        _order(db, tenant, _talk(db, tenant, TAKEOVER_CHAT), estado="descartado", confirmed=False)
        model = Model()

        assert await from_lost_sales(db, tenant.id, model) == 0 and model.seen["lost"] == []

    async def test_a_confirmed_order_is_not_a_lost_one(self, db: Session) -> None:
        tenant = _tenant(db, template="productos")
        _on(db, tenant)
        for _ in range(2):
            _order(
                db, tenant, _talk(db, tenant, TAKEOVER_CHAT), estado="descartado", confirmed=True
            )
        model = Model()

        assert await from_lost_sales(db, tenant.id, model) == 0


class TestTheSweepRunsThemInOrder:
    def _ctx(self, db: Session, model: Model) -> dict[str, Any]:
        return {"session_factory": lambda: _Borrowed(db), "llm": model}

    async def test_the_lost_sales_are_looked_at_on_mondays_only(self, db: Session) -> None:
        tenant = _tenant(db, template="productos")
        _on(db, tenant)
        for _ in range(2):
            _order(
                db,
                tenant,
                _talk(db, tenant, [("inbound", "customer", "Hola")]),
                estado="descartado",
                confirmed=False,
            )
        monday = datetime(2026, 10, 26, 12, tzinfo=UTC)

        tuesday = await learning_sweep(self._ctx(db, Model()), now=monday + timedelta(days=1))
        on_monday = await learning_sweep(self._ctx(db, Model()), now=monday)

        assert tuesday["venta_perdida"] == 0 and on_monday["venta_perdida"] == 1
