"""A warm handover: the person knows more, and the customer knows when it comes (019, C)."""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.agent.tools.handover import derivar_a_humano, handover_text
from app.agent.tools.registry import ToolContext
from app.models.business import BusinessProfile
from app.providers.llm import MockLLMProvider
from tests.integration.test_turn_pipeline import _conversation, _tenant

LIMA = ZoneInfo("America/Lima")
HOURS = {d: [["09:00", "18:00"]] for d in ("mon", "tue", "wed", "thu", "fri")}


def _context(db: Session, *, hours: dict | None = None) -> ToolContext:  # type: ignore[type-arg]
    tenant = _tenant(db, template="servicios")
    profile = db.execute(
        select(BusinessProfile).where(BusinessProfile.tenant_id == tenant.id)
    ).scalar_one()
    profile.opening_hours = hours if hours is not None else HOURS
    profile.timezone = "America/Lima"
    db.flush()
    conversation = _conversation(db, tenant, channel="whatsapp")
    return ToolContext(
        session=db, tenant_id=tenant.id, llm=MockLLMProvider(), conversation_id=conversation.id
    )


def local(year: int, month: int, day: int, hour: int) -> datetime:
    return datetime(year, month, day, hour, 0, tzinfo=LIMA)


class TestWhenItWillBeAnswered:
    def test_closed_the_customer_is_told_when_the_team_opens(self, db: Session) -> None:
        context = _context(db)

        sunday_night = handover_text(context, now=local(2026, 10, 25, 20))
        friday_evening = handover_text(context, now=local(2026, 10, 23, 19))

        assert sunday_night.endswith(
            "Ahora estamos fuera de horario: te escribimos mañana a las 09:00."
        )
        assert friday_evening.endswith("te escribimos el lunes a las 09:00.")

    def test_open_or_without_known_hours_nothing_is_added(self, db: Session) -> None:
        open_now = handover_text(_context(db), now=local(2026, 10, 26, 10))
        unknown = handover_text(_context(db, hours={}), now=local(2026, 10, 25, 20))

        assert "fuera de horario" not in open_now and "fuera de horario" not in unknown


class TestWhatThePersonReceives:
    async def test_the_summary_carries_what_was_offered_and_the_mood(self, db: Session) -> None:
        context = _context(db)

        await derivar_a_humano(
            context,
            {
                "motivo": "queja",
                "resumen": "Su pedido llegó incompleto",
                "ofrecido": "Reponer lo que falta",
                "animo": "molesto",
            },
        )

        summary = context.facts_patch["derivado"]["resumen"]
        assert (
            summary
            == "Su pedido llegó incompleto Se le ofreció: Reponer lo que falta. Ánimo: molesto."
        )

    async def test_an_invented_mood_is_dropped_and_a_postsale_reason_is_kept(
        self, db: Session
    ) -> None:
        context = _context(db)

        await derivar_a_humano(
            context, {"motivo": "postventa", "resumen": "Quiere devolver", "animo": "furioso"}
        )

        handed = context.facts_patch["derivado"]
        assert handed["motivo"] == "postventa" and handed["resumen"] == "Quiere devolver"
