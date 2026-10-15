"""When a follow-up is due (Feature 004, TASK-444, C-13), with a frozen clock.

Each condition that stops a follow-up has its own case; the base case sends.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any

import pytest

from app.process.facts import Derivado, Facts, Seguimiento
from app.process.templates.base import Stage
from app.services.followups import FollowUpPlan, why_not

# 15:00 in Lima (UTC-5).
NOW = datetime(2026, 10, 14, 20, 0, tzinfo=UTC)


def _config(**overrides: Any) -> Any:
    values = {"seguimiento_activo": True, "seguimiento_horas": 4, "seguimiento_max": 2}
    return SimpleNamespace(**{**values, **overrides})


def _contact(**overrides: Any) -> Any:
    values = {"opted_out_at": None, "last_inbound_at": NOW - timedelta(hours=5)}
    return SimpleNamespace(**{**values, **overrides})


def _ours(hours_ago: float = 5) -> Any:
    return SimpleNamespace(direction="outbound", created_at=NOW - timedelta(hours=hours_ago))


def _check(**overrides: Any) -> Any:
    values: dict[str, Any] = {
        "now": NOW,
        "stage": Stage.PEDIDO,
        "facts": Facts(),
        "config": _config(),
        "sandbox": False,
        "may_reply": True,
        "contact": _contact(),
        "last_message": _ours(),
        "zone": "America/Lima",
    }
    values.update(overrides)
    return why_not(**values)


class TestWhenAFollowUpIsDue:
    def test_the_base_case_sends_the_first_attempt(self) -> None:
        assert _check() == FollowUpPlan(attempt=1, last=False)

    def test_the_second_attempt_is_the_last(self) -> None:
        facts = Facts(seguimiento=Seguimiento(intentos=1, ultimo_en=NOW - timedelta(hours=5)))

        assert _check(facts=facts) == FollowUpPlan(attempt=2, last=True)

    @pytest.mark.parametrize("stage", [Stage.IDENTIFICAR, Stage.PEDIDO, Stage.DATOS, Stage.COBRO])
    def test_every_selling_stage_qualifies(self, stage: Stage) -> None:
        assert isinstance(_check(stage=stage), FollowUpPlan)

    def test_verification_only_with_the_payment_rejected(self) -> None:
        assert _check(stage=Stage.VERIFICACION) == "etapa"
        assert isinstance(
            _check(stage=Stage.VERIFICACION, facts=Facts(pago_estado="rechazado")), FollowUpPlan
        )


class TestEachConditionStopsIt:
    def test_turned_off(self) -> None:
        assert _check(config=_config(seguimiento_activo=False)) == "apagado"
        assert _check(config=None) == "apagado"

    def test_the_simulator(self) -> None:
        assert _check(sandbox=True) == "simulador"

    @pytest.mark.parametrize(
        "stage", [Stage.INICIO, Stage.CALIFICAR, Stage.CERRADO, Stage.ASESORIA, Stage.DERIVADO]
    )
    def test_other_stages(self, stage: Stage) -> None:
        assert _check(stage=stage) == "etapa"

    def test_a_handover_in_force(self) -> None:
        facts = Facts(derivado=Derivado(motivo="queja", hasta=NOW + timedelta(hours=1)))

        assert _check(facts=facts) == "derivado"

    def test_an_expired_handover_does_not_stop_it(self) -> None:
        facts = Facts(derivado=Derivado(motivo="queja", hasta=NOW - timedelta(minutes=1)))

        assert isinstance(_check(facts=facts), FollowUpPlan)

    def test_the_assistant_may_not_reply(self) -> None:
        assert _check(may_reply=False) == "asistente_inactivo"

    def test_opt_out(self) -> None:
        assert _check(contact=_contact(opted_out_at=NOW - timedelta(days=1))) == "opt_out"

    def test_the_customer_spoke_last(self) -> None:
        inbound = SimpleNamespace(direction="inbound", created_at=NOW - timedelta(hours=5))

        assert _check(last_message=inbound) == "sin_respuesta_nuestra"

    def test_too_soon(self) -> None:
        assert _check(last_message=_ours(hours_ago=3.9)) == "espera"

    def test_too_soon_after_the_last_follow_up(self) -> None:
        """The follow-up message itself may not be the newest row yet; the claim is."""
        facts = Facts(seguimiento=Seguimiento(intentos=1, ultimo_en=NOW - timedelta(hours=1)))

        assert _check(facts=facts) == "espera"

    def test_no_attempts_left(self) -> None:
        facts = Facts(seguimiento=Seguimiento(intentos=2, ultimo_en=NOW - timedelta(hours=5)))

        assert _check(facts=facts) == "intentos"

    def test_the_window_closes_within_thirty_minutes(self) -> None:
        closing = _contact(last_inbound_at=NOW - timedelta(hours=23, minutes=31))
        open_enough = _contact(last_inbound_at=NOW - timedelta(hours=23, minutes=29))

        assert _check(contact=closing) == "ventana"
        assert isinstance(_check(contact=open_enough), FollowUpPlan)

    @pytest.mark.parametrize(
        ("utc_hour", "allowed"),
        [(12, False), (13, True), (20, True), (1, True), (2, False), (3, False)],
    )
    def test_only_from_eight_to_nine_in_the_business_zone(
        self, utc_hour: int, allowed: bool
    ) -> None:
        """Lima is UTC-5: 13:00 UTC is 08:00 there; 02:00 UTC is 21:00."""
        now = NOW.replace(hour=utc_hour)
        result = _check(
            now=now,
            last_message=SimpleNamespace(direction="outbound", created_at=now - timedelta(hours=5)),
            contact=_contact(last_inbound_at=now - timedelta(hours=5)),
        )

        assert isinstance(result, FollowUpPlan) is allowed, result
