"""The stage engine (Feature 004, TASK-410).

The 34 cases of `referencias/engine_referencia.py` - the 18 scenarios of the
Capacita v21 harness plus 16 written for Leo - ported to pytest and run against
the real templates, so the table that was validated on paper is the table the
code answers to. Around them, the priorities of C-04 and every edge of the
functions, because `engine.py` is held to full coverage.
"""

from __future__ import annotations

import itertools
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from app.models.conversation_state import FunnelCode
from app.process.engine import (
    ANUNCIO_VIGENCIA,
    anuncio_vigente,
    camino_efectivo,
    compute_stage,
    funnel_for,
)
from app.process.facts import Derivado, EntradaAnuncio, Facts, Pedido, PedidoItem
from app.process.templates import TEMPLATES
from app.process.templates.base import EffectiveOptions, Stage

NOW = datetime(2026, 10, 10, 15, 0, tzinfo=UTC)

# The reference's two option sets, which are the real defaults of two templates.
CERT_TEMPLATE = TEMPLATES["certificaciones"]
SHOP_TEMPLATE = TEMPLATES["productos"]
CERT = CERT_TEMPLATE.resolve()
TIENDA = SHOP_TEMPLATE.resolve()


def _facts(**overrides: Any) -> Facts:
    """Facts as the reference builds them: greeted, nothing else, unless told."""
    values: dict[str, Any] = {"saludo_enviado": True}
    values.update(overrides)
    return Facts(**values)


def _pedido(*, estado: str = "borrador", items: int = 1, completo: bool = True) -> Pedido:
    return Pedido(
        estado=estado,
        items=[PedidoItem(nombre=f"Item {n}") for n in range(items)],
        completo=completo,
    )


def _ad(at: datetime) -> EntradaAnuncio:
    return EntradaAnuncio(recibido_en=at)


def _derivado(hasta: datetime) -> Derivado:
    return Derivado(motivo="pide_humano", hasta=hasta)


AD_TODAY = NOW - timedelta(hours=1)
PAQ_INCOMPLETO = _pedido(completo=False)
PAQ_COMPLETO = _pedido()

# (name, template, options, facts, expected stage, expected funnel)
CASES: list[tuple[str, Any, EffectiveOptions, Facts, str, str]] = [
    # --- the 18 scenarios of Capacita v21 (harness21.js)
    (
        "v21-01 conversación nueva",
        CERT_TEMPLATE,
        CERT,
        _facts(saludo_enviado=False),
        "INICIO",
        "NUEVO",
    ),
    ("v21-02 saludó, sin camino", CERT_TEMPLATE, CERT, _facts(), "CALIFICAR", "NUEVO"),
    (
        "v21-03 viene de anuncio",
        CERT_TEMPLATE,
        CERT,
        _facts(entrada_anuncio=_ad(AD_TODAY)),
        "ANUNCIO",
        "NUEVO",
    ),
    (
        "v21-04 camino por botón",
        CERT_TEMPLATE,
        CERT,
        _facts(camino="certificacion"),
        "IDENTIFICAR",
        "ATENDIDO",
    ),
    (
        "v21-05 camino escrito (elegir_camino)",
        CERT_TEMPLATE,
        CERT,
        _facts(camino="certificacion"),
        "IDENTIFICAR",
        "ATENDIDO",
    ),
    (
        "v21-06 sin camino pero vio paquetes",
        CERT_TEMPLATE,
        CERT,
        _facts(vio_productos=True),
        "IDENTIFICAR",
        "ATENDIDO",
    ),
    (
        "v21-07 camino programa",
        CERT_TEMPLATE,
        CERT,
        _facts(camino="programa"),
        "ASESORIA",
        "DERIVADO",
    ),
    (
        "v21-08 programa y luego certifica",
        CERT_TEMPLATE,
        CERT,
        _facts(camino="certificacion"),
        "IDENTIFICAR",
        "ATENDIDO",
    ),
    (
        "v21-09 paquete 5 con 2 temas",
        CERT_TEMPLATE,
        CERT,
        _facts(pedido=PAQ_INCOMPLETO),
        "PEDIDO",
        "NEGOCIACION",
    ),
    (
        "v21-10 pedido completo sin datos",
        CERT_TEMPLATE,
        CERT,
        _facts(pedido=PAQ_COMPLETO),
        "DATOS",
        "NEGOCIACION",
    ),
    (
        "v21-11 pedido completo con datos",
        CERT_TEMPLATE,
        CERT,
        _facts(pedido=PAQ_COMPLETO, datos_completos=True),
        "COBRO",
        "NEGOCIACION",
    ),
    (
        "v21-12 métodos de pago enviados",
        CERT_TEMPLATE,
        CERT,
        _facts(pedido=PAQ_COMPLETO, datos_completos=True, metodos_pago_enviados=True),
        "COBRO",
        "CUENTAS_ENVIADAS",
    ),
    (
        "v21-13 un producto suelto sin datos",
        CERT_TEMPLATE,
        CERT,
        _facts(pedido=_pedido()),
        "DATOS",
        "NEGOCIACION",
    ),
    (
        "v21-14 pago en verificación",
        CERT_TEMPLATE,
        CERT,
        _facts(pedido=_pedido(estado="por_confirmar"), pago_estado="por_verificar"),
        "VERIFICACION",
        "EN_VERIFICACION",
    ),
    (
        "v21-15 pago rechazado",
        CERT_TEMPLATE,
        CERT,
        _facts(pedido=PAQ_COMPLETO, datos_completos=True, pago_estado="rechazado"),
        "VERIFICACION",
        "EN_VERIFICACION",
    ),
    (
        "v21-16 pago confirmado",
        CERT_TEMPLATE,
        CERT,
        _facts(pedido=_pedido(estado="confirmado"), pago_estado="confirmado"),
        "CERRADO",
        "GANADO",
    ),
    # v21 left the stage (IDENTIFICAR) and only the funnel at DERIVADO; in Leo
    # the stage is DERIVADO too (C-04)
    (
        "v21-17 derivado a mitad de venta",
        CERT_TEMPLATE,
        CERT,
        _facts(camino="certificacion", derivado=_derivado(NOW + timedelta(hours=3))),
        "DERIVADO",
        "DERIVADO",
    ),
    (
        "v21-18 derivado con pago en verificación",
        CERT_TEMPLATE,
        CERT,
        _facts(pago_estado="por_verificar", derivado=_derivado(NOW + timedelta(hours=3))),
        "VERIFICACION",
        "EN_VERIFICACION",
    ),
    # --- the 16 written for Leo
    (
        "tienda: sin calificación va a IDENTIFICAR",
        SHOP_TEMPLATE,
        TIENDA,
        _facts(),
        "IDENTIFICAR",
        "ATENDIDO",
    ),
    (
        "tienda: anuncio sin calificación",
        SHOP_TEMPLATE,
        TIENDA,
        _facts(entrada_anuncio=_ad(AD_TODAY)),
        "ANUNCIO",
        "NUEVO",
    ),
    (
        "anuncio de hace 8 días ya no cuenta",
        CERT_TEMPLATE,
        CERT,
        _facts(entrada_anuncio=_ad(NOW - timedelta(days=8))),
        "CALIFICAR",
        "NUEVO",
    ),
    (
        "tienda: anuncio y ya vio productos",
        SHOP_TEMPLATE,
        TIENDA,
        _facts(entrada_anuncio=_ad(AD_TODAY), vio_productos=True),
        "IDENTIFICAR",
        "ATENDIDO",
    ),
    (
        "anuncio + pedido: manda el pedido",
        SHOP_TEMPLATE,
        TIENDA,
        _facts(entrada_anuncio=_ad(AD_TODAY), pedido=_pedido(completo=False)),
        "PEDIDO",
        "NEGOCIACION",
    ),
    (
        "pausa de derivación vencida",
        CERT_TEMPLATE,
        CERT,
        _facts(camino="certificacion", derivado=_derivado(NOW - timedelta(minutes=1))),
        "IDENTIFICAR",
        "ATENDIDO",
    ),
    (
        "contra entrega confirmado por cliente",
        SHOP_TEMPLATE,
        TIENDA,
        _facts(pedido=_pedido(estado="por_confirmar"), pago_estado="por_verificar"),
        "VERIFICACION",
        "EN_VERIFICACION",
    ),
    (
        "contra entrega rechazado vuelve a COBRO",
        SHOP_TEMPLATE,
        TIENDA,
        _facts(pedido=PAQ_COMPLETO, datos_completos=True),
        "COBRO",
        "NEGOCIACION",
    ),
    (
        "nuevo pedido después de CERRADO",
        SHOP_TEMPLATE,
        TIENDA,
        _facts(pedido=_pedido(completo=False)),
        "PEDIDO",
        "NEGOCIACION",
    ),
    (
        "pedido descartado vuelve a IDENTIFICAR",
        SHOP_TEMPLATE,
        TIENDA,
        _facts(pedido=_pedido(estado="descartado")),
        "IDENTIFICAR",
        "ATENDIDO",
    ),
    (
        "borrador vacío no cuenta como pedido",
        SHOP_TEMPLATE,
        TIENDA,
        _facts(pedido=_pedido(items=0, completo=False)),
        "IDENTIFICAR",
        "ATENDIDO",
    ),
    (
        "asesoría derivada y pausa vencida: sigue en ASESORIA",
        CERT_TEMPLATE,
        CERT,
        _facts(camino="programa", derivado=_derivado(NOW - timedelta(hours=1))),
        "ASESORIA",
        "DERIVADO",
    ),
    (
        "desde ASESORIA cambia a certificar (elegir_camino)",
        CERT_TEMPLATE,
        CERT,
        _facts(camino="certificacion"),
        "IDENTIFICAR",
        "ATENDIDO",
    ),
    (
        "rechazo vigente con pedido completo",
        CERT_TEMPLATE,
        CERT,
        _facts(pedido=PAQ_COMPLETO, datos_completos=True, pago_estado="rechazado"),
        "VERIFICACION",
        "EN_VERIFICACION",
    ),
    # when the order changes OrderService turns rechazo_vigente off, so FactsLoader
    # gives pago_estado=None
    (
        "tras rechazo cambia el pedido y queda incompleto",
        CERT_TEMPLATE,
        CERT,
        _facts(pedido=PAQ_INCOMPLETO, pago_estado=None),
        "PEDIDO",
        "NEGOCIACION",
    ),
    (
        "asesoría con pedido abierto: manda el pedido",
        CERT_TEMPLATE,
        CERT,
        _facts(camino="programa", pedido=PAQ_INCOMPLETO),
        "PEDIDO",
        "NEGOCIACION",
    ),
]


def test_the_reference_table_has_its_34_cases() -> None:
    assert len(CASES) == 34


@pytest.mark.parametrize(
    ("template", "options", "facts", "stage", "funnel"),
    [case[1:] for case in CASES],
    ids=[case[0] for case in CASES],
)
def test_reference_case(
    template: Any, options: EffectiveOptions, facts: Facts, stage: str, funnel: str
) -> None:
    computed = compute_stage(template, options, facts, NOW)

    assert (computed, funnel_for(template, computed, facts)) == (stage, funnel)


class TestPriorityBetweenStages:
    """C-04: CERRADO > VERIFICACION > DERIVADO > INICIO > PEDIDO > DATOS > COBRO
    > ASESORIA > ANUNCIO / CALIFICAR > IDENTIFICAR."""

    def _stage(self, **facts: Any) -> Stage:
        return compute_stage(CERT_TEMPLATE, CERT, _facts(**facts), NOW)

    def test_a_confirmed_payment_beats_a_handover_and_an_unfinished_order(self) -> None:
        stage = self._stage(
            pago_estado="confirmado",
            derivado=_derivado(NOW + timedelta(hours=1)),
            pedido=_pedido(completo=False),
        )

        assert stage is Stage.CERRADO

    def test_a_payment_under_review_beats_a_handover(self) -> None:
        stage = self._stage(
            pago_estado="por_verificar", derivado=_derivado(NOW + timedelta(hours=1))
        )

        assert stage is Stage.VERIFICACION

    def test_a_handover_beats_the_greeting(self) -> None:
        stage = self._stage(saludo_enviado=False, derivado=_derivado(NOW + timedelta(hours=1)))

        assert stage is Stage.DERIVADO

    def test_the_greeting_beats_an_order(self) -> None:
        assert self._stage(saludo_enviado=False, pedido=PAQ_INCOMPLETO) is Stage.INICIO

    def test_an_order_beats_the_advisory_path(self) -> None:
        assert self._stage(camino="programa", pedido=PAQ_COMPLETO) is Stage.DATOS

    def test_the_advisory_path_beats_an_ad(self) -> None:
        assert self._stage(camino="programa", entrada_anuncio=_ad(AD_TODAY)) is Stage.ASESORIA

    def test_an_ad_beats_qualification(self) -> None:
        assert self._stage(entrada_anuncio=_ad(AD_TODAY)) is Stage.ANUNCIO

    def test_qualification_beats_identifying(self) -> None:
        assert self._stage() is Stage.CALIFICAR

    def test_an_incomplete_order_beats_missing_data(self) -> None:
        assert self._stage(pedido=PAQ_INCOMPLETO, datos_completos=False) is Stage.PEDIDO

    def test_complete_data_without_a_complete_order_is_still_pedido(self) -> None:
        assert self._stage(pedido=PAQ_INCOMPLETO, datos_completos=True) is Stage.PEDIDO


class TestTheHandoverPause:
    def _facts(self, hasta: datetime) -> Facts:
        return _facts(camino="certificacion", derivado=_derivado(hasta))

    def test_a_pause_that_ends_exactly_now_is_over(self) -> None:
        """`hasta > now`, strictly: at the very second it ends the bot is back."""
        stage = compute_stage(CERT_TEMPLATE, CERT, self._facts(NOW), NOW)

        assert stage is Stage.IDENTIFICAR

    def test_a_pause_with_a_second_left_is_active(self) -> None:
        facts = self._facts(NOW + timedelta(seconds=1))

        assert compute_stage(CERT_TEMPLATE, CERT, facts, NOW) is Stage.DERIVADO

    def test_ignoring_the_handover_gives_the_stage_without_it(self) -> None:
        """What the pipeline asks before deciding whether a proof that arrives
        during a pause must be processed."""
        facts = _facts(
            pedido=PAQ_COMPLETO, datos_completos=True, derivado=_derivado(NOW + timedelta(hours=3))
        )

        assert compute_stage(CERT_TEMPLATE, CERT, facts, NOW) is Stage.DERIVADO
        assert (
            compute_stage(CERT_TEMPLATE, CERT, facts, NOW, ignorar_derivacion=True) is Stage.COBRO
        )

    def test_ignoring_the_handover_changes_nothing_when_there_is_none(self) -> None:
        facts = _facts(camino="certificacion")

        assert compute_stage(CERT_TEMPLATE, CERT, facts, NOW, ignorar_derivacion=True) is (
            compute_stage(CERT_TEMPLATE, CERT, facts, NOW)
        )

    def test_a_payment_still_wins_when_the_handover_is_ignored(self) -> None:
        facts = _facts(pago_estado="por_verificar", derivado=_derivado(NOW + timedelta(hours=3)))

        stage = compute_stage(CERT_TEMPLATE, CERT, facts, NOW, ignorar_derivacion=True)

        assert stage is Stage.VERIFICACION

    def test_the_handover_flag_follows_the_clock(self) -> None:
        facts = _facts(derivado=_derivado(NOW))

        assert facts.derivado_activo(NOW - timedelta(seconds=1)) is True
        assert facts.derivado_activo(NOW) is False
        assert _facts().derivado_activo(NOW) is False


class TestTheAd:
    def test_it_counts_for_exactly_seven_days(self) -> None:
        on_the_limit = _facts(entrada_anuncio=_ad(NOW - ANUNCIO_VIGENCIA))
        past_the_limit = _facts(entrada_anuncio=_ad(NOW - ANUNCIO_VIGENCIA - timedelta(seconds=1)))

        assert anuncio_vigente(on_the_limit, NOW) is True
        assert anuncio_vigente(past_the_limit, NOW) is False

    def test_no_ad_is_not_current(self) -> None:
        assert anuncio_vigente(_facts(), NOW) is False

    def test_a_chosen_path_takes_the_customer_out_of_the_ad_stage(self) -> None:
        facts = _facts(entrada_anuncio=_ad(AD_TODAY), camino="certificacion")

        assert compute_stage(CERT_TEMPLATE, CERT, facts, NOW) is Stage.IDENTIFICAR

    def test_having_seen_products_takes_the_customer_out_of_the_ad_stage(self) -> None:
        facts = _facts(entrada_anuncio=_ad(AD_TODAY), vio_productos=True)

        assert compute_stage(CERT_TEMPLATE, CERT, facts, NOW) is Stage.IDENTIFICAR


class TestTheEffectivePath:
    def test_the_chosen_path_is_returned(self) -> None:
        camino = camino_efectivo(CERT, _facts(camino="programa"))

        assert camino is not None and camino.clave == "programa"

    def test_a_chosen_path_that_no_longer_exists_is_no_path(self) -> None:
        """The tenant removed a path a customer had chosen earlier."""
        assert camino_efectivo(CERT, _facts(camino="borrada")) is None

    def test_viewing_products_implies_the_first_sale_path(self) -> None:
        camino = camino_efectivo(CERT, _facts(vio_productos=True))

        assert camino is not None and camino.clave == "certificacion"

    def test_having_an_order_implies_the_first_sale_path(self) -> None:
        camino = camino_efectivo(CERT, _facts(pedido=PAQ_INCOMPLETO))

        assert camino is not None and camino.clave == "certificacion"

    def test_a_chosen_path_wins_over_the_implied_one(self) -> None:
        camino = camino_efectivo(CERT, _facts(camino="programa", vio_productos=True))

        assert camino is not None and camino.clave == "programa"

    def test_nothing_chosen_nothing_seen_is_no_path(self) -> None:
        assert camino_efectivo(CERT, _facts()) is None

    def test_a_shop_without_paths_never_has_one(self) -> None:
        assert camino_efectivo(TIENDA, _facts(vio_productos=True)) is None
        assert camino_efectivo(TIENDA, _facts(camino="certificacion")) is None


class TestTheFunnel:
    @pytest.mark.parametrize("template", list(TEMPLATES.values()), ids=list(TEMPLATES))
    def test_every_stage_has_a_funnel_column_in_every_template(self, template: Any) -> None:
        for stage in Stage:
            assert isinstance(funnel_for(template, stage, _facts()), FunnelCode)

    @pytest.mark.parametrize("template", list(TEMPLATES.values()), ids=list(TEMPLATES))
    def test_only_cobro_follows_the_payment_methods_flag(self, template: Any) -> None:
        sent = _facts(metodos_pago_enviados=True)
        not_sent = _facts(metodos_pago_enviados=False)

        for stage in Stage:
            if stage is Stage.COBRO:
                assert funnel_for(template, stage, sent) is FunnelCode.CUENTAS_ENVIADAS
                assert funnel_for(template, stage, not_sent) is FunnelCode.NEGOCIACION
            else:
                assert funnel_for(template, stage, sent) is funnel_for(template, stage, not_sent)

    def test_methods_sent_in_another_stage_do_not_move_the_funnel(self) -> None:
        """The flag is per active order and per rejection; outside COBRO the
        column is the stage's own."""
        facts = _facts(metodos_pago_enviados=True)

        assert funnel_for(CERT_TEMPLATE, Stage.VERIFICACION, facts) is FunnelCode.EN_VERIFICACION
        assert funnel_for(CERT_TEMPLATE, Stage.CERRADO, facts) is FunnelCode.GANADO

    def test_perdido_is_never_produced(self) -> None:
        for template, stage in itertools.product(TEMPLATES.values(), Stage):
            assert funnel_for(template, stage, _facts()) is not FunnelCode.PERDIDO


class TestEveryStageIsReachable:
    """A stage no facts can reach is dead text in a template."""

    REACH: dict[Stage, Facts] = {
        Stage.INICIO: _facts(saludo_enviado=False),
        Stage.CALIFICAR: _facts(),
        Stage.ANUNCIO: _facts(entrada_anuncio=_ad(AD_TODAY)),
        Stage.IDENTIFICAR: _facts(camino="certificacion"),
        Stage.PEDIDO: _facts(pedido=PAQ_INCOMPLETO),
        Stage.DATOS: _facts(pedido=PAQ_COMPLETO),
        Stage.COBRO: _facts(pedido=PAQ_COMPLETO, datos_completos=True),
        Stage.VERIFICACION: _facts(pago_estado="por_verificar"),
        Stage.CERRADO: _facts(pago_estado="confirmado"),
        Stage.ASESORIA: _facts(camino="programa"),
        Stage.DERIVADO: _facts(derivado=_derivado(NOW + timedelta(hours=1))),
    }

    @pytest.mark.parametrize("stage", list(Stage))
    def test_the_certification_template_reaches_it(self, stage: Stage) -> None:
        assert compute_stage(CERT_TEMPLATE, CERT, self.REACH[stage], NOW) is stage

    def test_the_table_covers_every_stage(self) -> None:
        assert set(self.REACH) == set(Stage)


class TestNothingBreaksTheEngine:
    def test_every_combination_of_the_flags_gives_a_stage_of_the_template(self) -> None:
        """No combination of facts may leave the engine without an answer."""
        orders = [
            None,
            _pedido(items=0),
            PAQ_INCOMPLETO,
            PAQ_COMPLETO,
            _pedido(estado="confirmado"),
        ]
        for (
            greeted,
            order,
            data_ok,
            payment,
            path,
            ad,
            seen,
            paused,
        ) in itertools.product(
            (True, False),
            orders,
            (True, False),
            (None, "por_verificar", "rechazado", "confirmado"),
            (None, "certificacion", "programa", "borrada"),
            (None, AD_TODAY),
            (True, False),
            (None, NOW + timedelta(hours=1)),
        ):
            facts = Facts(
                saludo_enviado=greeted,
                pedido=order,
                datos_completos=data_ok,
                pago_estado=payment,
                camino=path,
                entrada_anuncio=_ad(ad) if ad else None,
                vio_productos=seen,
                derivado=_derivado(paused) if paused else None,
            )
            for template, options in ((CERT_TEMPLATE, CERT), (SHOP_TEMPLATE, TIENDA)):
                stage = compute_stage(template, options, facts, NOW)
                assert stage in template.stages
                assert isinstance(funnel_for(template, stage, facts), FunnelCode)

    def test_the_engine_is_deterministic(self) -> None:
        facts = _facts(pedido=PAQ_COMPLETO, datos_completos=True)

        results = {compute_stage(CERT_TEMPLATE, CERT, facts, NOW) for _ in range(5)}

        assert results == {Stage.COBRO}

    def test_the_engine_does_not_change_its_input(self) -> None:
        facts = _facts(pedido=PAQ_COMPLETO, derivado=_derivado(NOW + timedelta(hours=1)))
        before = facts.model_dump()

        compute_stage(CERT_TEMPLATE, CERT, facts, NOW)
        funnel_for(CERT_TEMPLATE, Stage.COBRO, facts)

        assert facts.model_dump() == before
