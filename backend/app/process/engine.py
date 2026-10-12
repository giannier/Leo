"""The stage engine: where a conversation is in the sale (Feature 004).

Two pure functions, no I/O and no clock of their own (`now` is a parameter), so
every rule is a table-driven test. The order of the checks is the priority
between stages and it is fixed (C-04):

    CERRADO > VERIFICACION > DERIVADO > INICIO > PEDIDO > DATOS > COBRO
            > ASESORIA > ANUNCIO / CALIFICAR > IDENTIFICAR

A pending payment outranks a handover: the funnel shows EN_VERIFICACION while
the bot stays quiet (the pause is a gate in the pipeline, not a stage).
"""

from __future__ import annotations

from datetime import datetime, timedelta

from app.models.conversation_state import FunnelCode
from app.process.facts import Facts
from app.process.templates.base import EffectiveOptions, Stage, Template
from app.schemas.process_options import Camino

# An ad counts as the reason for the conversation for this long (`plantillas.md`
# section 1: "anuncio de los últimos 7 días").
ANUNCIO_VIGENCIA = timedelta(days=7)


def camino_efectivo(options: EffectiveOptions, facts: Facts) -> Camino | None:
    """The path the customer is on.

    The chosen one. If they chose none but already looked at products or have an
    order, the first sale path: that is how Capacita v21 avoids asking the
    qualification question again of someone already buying.
    """
    if facts.camino:
        return options.camino(facts.camino)
    if facts.vio_productos or facts.pedido is not None:
        return options.primer_camino_de_venta()
    return None


def anuncio_vigente(facts: Facts, now: datetime) -> bool:
    """Whether the customer arrived from an ad recently enough to matter."""
    entrada = facts.entrada_anuncio
    return entrada is not None and now - entrada.recibido_en <= ANUNCIO_VIGENCIA


def compute_stage(
    template: Template,
    options: EffectiveOptions,
    facts: Facts,
    now: datetime,
    *,
    ignorar_derivacion: bool = False,
) -> Stage:
    """The stage a conversation is in, from its facts.

    `ignorar_derivacion` answers "what would the stage be if nobody had taken
    the conversation": the pipeline uses it before deciding whether a payment
    proof that arrives during a pause must be processed (plan step 3).

    `template` is part of the signature of `plantillas.md` and reserved for a
    template that one day drops a stage; today all three share the same rules,
    which differ only through `options`.
    """
    del template
    if facts.pago_estado == "confirmado":
        return Stage.CERRADO
    if facts.pago_estado in ("por_verificar", "rechazado"):
        return Stage.VERIFICACION
    if not ignorar_derivacion and facts.derivado_activo(now):
        return Stage.DERIVADO
    if not facts.saludo_enviado:
        return Stage.INICIO

    pedido = facts.pedido
    if pedido is not None and pedido.estado == "borrador" and pedido.items:
        if not pedido.completo:
            return Stage.PEDIDO
        if not facts.datos_completos:
            return Stage.DATOS
        return Stage.COBRO

    camino = camino_efectivo(options, facts)
    if camino is not None and camino.tipo == "asesoria":
        return Stage.ASESORIA
    if camino is None and anuncio_vigente(facts, now) and not facts.vio_productos:
        return Stage.ANUNCIO
    if options.calificacion_activa and camino is None:
        return Stage.CALIFICAR
    return Stage.IDENTIFICAR


def funnel_for(template: Template, stage: Stage, facts: Facts) -> FunnelCode:
    """The funnel column of a stage.

    Almost always fixed by the template; COBRO moves to CUENTAS_ENVIADAS once
    the payment methods were sent for the active order.
    """
    spec = template.stage(stage)
    if facts.metodos_pago_enviados and spec.funnel_with_methods_sent is not None:
        return spec.funnel_with_methods_sent
    return spec.funnel
