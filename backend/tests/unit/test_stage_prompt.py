"""The stage prompt builder (TASK-414, `plantillas.md` section 4).

Pure: every test builds the configuration and the facts by hand, so the sections,
the placeholders and the token budget are checked without a database. The
snapshots (`tests/fixtures/prompts/<template>.txt`) hold the prompt of every
stage of the three templates for one realistic business; regenerate them with
`UPDATE_PROMPT_SNAPSHOTS=1` after an intended change and review the diff.
"""

from __future__ import annotations

import os
import re
import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from types import MappingProxyType
from typing import Any

import pytest

from app.agent.prompts.stage import (
    SECTION_CAPS,
    TOKEN_LIMIT,
    build_stage_prompt,
    estimate_tokens,
    resolve_placeholders,
)
from app.agent.situations import SelectedSituation
from app.models.sales_config import Promotion
from app.process.facts import (
    Derivado,
    EntradaAnuncio,
    Facts,
    Pedido,
    PedidoItem,
    PromoCierre,
)
from app.process.service import (
    Behavior,
    BusinessInfoView,
    EffectiveProcessConfig,
)
from app.process.templates import TEMPLATES
from app.process.templates.base import Stage
from app.process.turn import TurnContext
from app.providers.llm.base import ToolSpec
from app.schemas.business_info import (
    Contacto,
    Devoluciones,
    Entregas,
    Local,
    Locales,
    TipoCliente,
    TiposCliente,
    Zona,
)
from app.schemas.process_options import ProcessOptions

NOW = datetime(2026, 10, 12, 15, 0, tzinfo=UTC)
SNAPSHOTS = Path(__file__).resolve().parent.parent / "fixtures" / "prompts"
# The registry's tool definitions get this much of the 6 000 (plan.md).
TOOLS_ALLOWANCE = SECTION_CAPS["herramientas"]


def _promotion(name: str, use: str = "automatica", **extra: Any) -> Promotion:
    values: dict[str, Any] = {
        "id": uuid.uuid4(),
        "nombre": name,
        "descripcion": f"Descripción de {name}",
        "uso": use,
        "tipo": "porcentaje",
        "valor": Decimal("10.00"),
        "vigencia_horas": 24 if use == "cierre" else None,
    }
    values.update(extra)
    return Promotion(**values)


def _info(*, full: bool = False) -> BusinessInfoView:
    locales = [
        Local(
            nombre=f"Sede {i}",
            direccion=f"Av. Principal {i}00, Chiclayo",
            referencia="frente al parque" if full else None,
            horarios={"mon": [["09:00", "18:00"]], "sat": [["09:00", "13:00"]]},
        )
        for i in range(1, 21 if full else 3)
    ]
    zonas = [
        Zona(nombre=f"Zona {i} de reparto", costo=Decimal("8.50"), tiempo="mismo día")
        for i in range(1, 31 if full else 3)
    ]
    types = [
        TipoCliente(
            nombre=f"Cliente tipo {i}",
            como_reconocerlo="Pide más de 12 unidades de cualquier producto",
            trato="Se le ofrece precio por mayor y entrega prioritaria",
        )
        for i in range(1, 7 if full else 2)
    ]
    return BusinessInfoView(
        locales=Locales(atiende_presencial=True, locales=locales),
        entregas=Entregas(
            hace_delivery=True,
            zonas=zonas,
            envio_gratis_desde=Decimal("150.00"),
            recojo_en_tienda=True,
            notas="Las entregas son de lunes a sábado.",
        ),
        tipos_cliente=TiposCliente(tipos=types),
        devoluciones=Devoluciones(
            acepta_cambios=True,
            acepta_devoluciones=False,
            plazo_dias=7,
            condiciones="Con boleta y en su empaque original. " * (8 if full else 1),
        ),
        contacto=Contacto(
            telefono_humano="+51967285628",
            correo="ventas@tienda.pe",
            web="https://tienda.pe",
        ),
    )


RUBRO_RULES = [
    "Habla como un asesor académico, no como un vendedor de tienda: el cliente invierte en su "
    "formación o en certificar su experiencia.",
    'Di certificado, curso, programa, tema o modalidad; evita "producto", "stock" o "carrito".',
    "Si pregunta para qué sirve, explica el valor del certificado con la información del negocio, "
    "sin prometer convalidaciones que no estén en ella.",
    "Nunca digas que un certificado tiene validez oficial, acreditación o horas que no estén en el "
    "catálogo o en el conocimiento del negocio.",
    "Si pregunta por modalidad, fechas u horarios, respóndelo con consultar_conocimiento; si no "
    "está, deriva sin inventarlo.",
]


def long_situations(n: int = 5) -> list[SelectedSituation]:
    return [
        SelectedSituation(
            id=uuid.uuid4(),
            titulo=f"Situación larga {i}",
            cuando_aplica="Cuando el cliente pregunta algo muy específico " * 6,
            que_hace="Responde con calma y detalle, paso por paso. " * 22,
            claves=("malla",),
            derivar=True,
            ejemplo_respuesta="Claro, te explico con gusto. " * 20,
            siempre=i == 0,
        )
        for i in range(n)
    ]


def make_config(
    template_key: str = "productos",
    *,
    full: bool = False,
    options: ProcessOptions | None = None,
    behavior: Behavior | None = None,
    info: BusinessInfoView | None = None,
    **overrides: Any,
) -> EffectiveProcessConfig:
    template = TEMPLATES[template_key]
    values: dict[str, Any] = {
        "tenant_id": uuid.uuid4(),
        "template": template,
        "options": template.resolve(options),
        "stage_instructions": MappingProxyType(
            {stage: template.stage(stage).instrucciones for stage in Stage}
        ),
        "behavior": behavior
        or Behavior(
            agent_name="Capacitín",
            tone="cercano",
            ritmo="equilibrado",
            detalle="moderado",
            tratamiento="usted" if full else "tu",
            saludo="¡Bienvenido a Escuela Capacita!" if full else None,
        ),
        "rubro_rules": tuple(RUBRO_RULES) if full else (),
        "business_name": "Escuela Capacita",
        "business_description": ("Certificaciones y cursos para el sector público. " * 12)
        if full
        else "Certificaciones y cursos para el sector público.",
        "industry": "instituto",
        "opening_hours": {"mon": [["09:00", "18:00"]]},
        "info": info or _info(full=full),
        "payment_methods": ("yape", "plin", "transferencia", "tarjeta", "efectivo", "link")
        if full
        else ("yape", "transferencia"),
        "promotions": tuple(
            [_promotion(f"Promo automática número {i}") for i in range(1, 13 if full else 2)]
            + [_promotion("Cupón 20 %", use="cierre")]
        ),
        # How the catalogue closes (feature 020): only the universal process reads it. A full
        # business uses all four, the worst case for the budget.
        "cierres": ("pedido", "cotizacion", "cita", "informar") if full else ("pedido",),
    }
    values.update(overrides)
    return EffectiveProcessConfig(**values)


def _order(items: int = 1, *, missing: int = 0, state: str = "borrador") -> Pedido:
    return Pedido(
        id=uuid.uuid4(),
        numero=42,
        estado=state,
        items=[
            PedidoItem(
                nombre=f"Paquete de certificaciones número {i}",
                cantidad=1,
                selecciones=["SIAF", "Gestión pública"],
            )
            for i in range(1, items + 1)
        ],
        completo=missing == 0,
        faltan_selecciones=missing,
        total=Decimal("500.00"),
    )


def full_facts(**overrides: Any) -> Facts:
    """A conversation that fills every line of the state block."""
    values: dict[str, Any] = {
        "saludo_enviado": True,
        "entrada_anuncio": EntradaAnuncio(
            headline="5 certificaciones x S/ 500 con el mejor precio del mercado", recibido_en=NOW
        ),
        "camino": "certificacion",
        "vio_productos": True,
        "recursos_enviados": [f"recurso_{i}" for i in range(25)],
        "opciones_mostradas": [f"opcion_{i}" for i in range(25)],
        "asesoria_derivada_en": NOW - timedelta(days=1),
        "nombre_cliente": "Ana María",
        "nombre_confirmado": True,
        "pedido": _order(items=10, missing=3),
        "datos_faltantes": ["dni", "nombres", "apellidos", "correo", "telefono", "tipo_entrega"],
        "pago_estado": "rechazado",
        "motivo_rechazo": "El monto del comprobante no coincide con el total del pedido. " * 3,
        "promos_cierre": [
            PromoCierre(id="1", nombre="Cupón 20 %", vigencia_horas=24),
            PromoCierre(
                id="2",
                nombre="Último llamado",
                vigencia_horas=12,
                ofrecida_vence=NOW + timedelta(hours=3),
            ),
            PromoCierre(id="3", nombre="Envío gratis", vigencia_horas=48),
        ],
    }
    values.update(overrides)
    return Facts(**values)


def _stage_facts(stage: Stage) -> Facts:
    """A believable conversation for each stage, for the snapshots."""
    greeted: dict[str, Any] = {
        "saludo_enviado": True,
        "nombre_cliente": "Ana",
        "nombre_confirmado": True,
    }
    promo = PromoCierre(id="p1", nombre="Cupón 20 %", vigencia_horas=24)
    scenarios: dict[Stage, dict[str, Any]] = {
        Stage.INICIO: {"nombre_cliente": "Ana", "nombre_confirmado": False},
        Stage.CALIFICAR: greeted,
        Stage.ANUNCIO: {
            **greeted,
            "entrada_anuncio": EntradaAnuncio(
                headline="5 certificaciones x S/ 500", recibido_en=NOW
            ),
        },
        Stage.IDENTIFICAR: {
            **greeted,
            "camino": "certificacion",
            "vio_productos": True,
            "recursos_enviados": ["saludo"],
            "opciones_mostradas": ["caminos"],
            "promos_cierre": [promo],
        },
        Stage.PEDIDO: {
            **greeted,
            "pedido": _order(missing=3),
            "datos_faltantes": ["dni", "correo"],
            "promos_cierre": [promo],
        },
        Stage.DATOS: {
            **greeted,
            "pedido": _order(),
            "datos_faltantes": ["dni", "correo"],
            "promos_cierre": [promo],
        },
        Stage.COBRO: {
            **greeted,
            "pedido": _order(),
            "datos_completos": True,
            "promos_cierre": [promo],
        },
        Stage.VERIFICACION: {
            **greeted,
            "pedido": _order(state="borrador"),
            "pago_estado": "rechazado",
            "motivo_rechazo": "el monto no coincide con el total",
        },
        Stage.CERRADO: {
            **greeted,
            "pedido": _order(state="confirmado"),
            "pago_estado": "confirmado",
        },
        Stage.ASESORIA: {
            **greeted,
            "camino": "programa",
            "asesoria_derivada_en": NOW - timedelta(hours=2),
        },
        Stage.DERIVADO: {
            **greeted,
            "derivado": Derivado(motivo="pide_humano", hasta=NOW + timedelta(hours=6)),
        },
    }
    return Facts(**scenarios[stage])


def _prompt(stage: Stage, facts: Facts | None = None, **config: Any) -> str:
    return build_stage_prompt(make_config(**config), facts or Facts(), stage, now=NOW).text


# ------------------------------------------------------------------ snapshots


def _render_template(template_key: str) -> str:
    config = make_config(template_key)
    blocks = []
    for stage in Stage:
        prompt = build_stage_prompt(config, _stage_facts(stage), stage, now=NOW)
        blocks.append(f"########## {template_key} / {stage.value} ##########\n{prompt.text}\n")
    return "\n".join(blocks)


@pytest.mark.parametrize("template_key", sorted(TEMPLATES))
def test_the_prompt_of_every_stage_matches_its_snapshot(template_key: str) -> None:
    rendered = _render_template(template_key)
    path = SNAPSHOTS / f"{template_key}.txt"
    if os.getenv("UPDATE_PROMPT_SNAPSHOTS"):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(rendered, encoding="utf-8", newline="\n")
    assert path.exists(), f"missing snapshot {path}; run with UPDATE_PROMPT_SNAPSHOTS=1"
    assert rendered == path.read_text(encoding="utf-8").replace("\r\n", "\n")


# --------------------------------------------------------------------- budget


@pytest.mark.parametrize("template_key", ["productos", "certificaciones", "servicios"])
def test_the_prompt_splits_into_what_stays_and_what_changes(template_key: str) -> None:
    """The head goes before the conversation and the tail after it (the provider's cache)."""
    config = make_config(template_key, full=True)
    prompt = build_stage_prompt(
        config, full_facts(), Stage.IDENTIFICAR, now=NOW, situations=long_situations()
    )

    assert f"{prompt.head}\n\n{prompt.tail}" == prompt.text
    assert "ESTADO DE LA CONVERSACIÓN" in prompt.tail and "ANTES DE RESPONDER" in prompt.tail
    assert "Hoy:" not in prompt.head and "SITUACIONES DEL NEGOCIO" not in prompt.head
    assert prompt.head.startswith("Eres ")


@pytest.mark.parametrize("template_key", sorted(TEMPLATES))
@pytest.mark.parametrize("stage", list(Stage))
def test_a_full_business_stays_under_6000_tokens_in_every_stage(
    template_key: str, stage: Stage
) -> None:
    config = make_config(template_key, full=True)
    prompt = build_stage_prompt(config, full_facts(), stage, now=NOW, situations=long_situations())

    for name in (
        "plataforma",
        "venta",
        "estilo",
        "negocio",
        "estado",
        "etapa",
        "rubro",
        "situaciones",
    ):
        # "venta" is absent once the sale is over (paid or paused for a person).
        used = prompt.sections.get(name, 0)
        assert used <= SECTION_CAPS[name], f"{name}: {used}"
    # What the registry's tool definitions may add on top.
    assert prompt.tokens + TOOLS_ALLOWANCE <= TOKEN_LIMIT


def test_the_tool_definitions_are_counted_against_the_budget() -> None:
    spec = ToolSpec(name="buscar_productos", description="x" * 700, parameters={"type": "object"})
    prompt = build_stage_prompt(
        make_config(), Facts(), Stage.IDENTIFICAR, now=NOW, tool_specs=[spec]
    )

    assert prompt.sections["herramientas"] > 200
    assert prompt.tokens == sum(prompt.sections.values())
    assert prompt.over_budget == []


def test_over_budget_names_the_section_that_went_over() -> None:
    texts = dict.fromkeys(Stage, "palabra " * 800)
    config = make_config(stage_instructions=MappingProxyType(texts))

    prompt = build_stage_prompt(config, Facts(), Stage.PEDIDO, now=NOW)

    assert "etapa" in prompt.over_budget


def test_the_business_section_drops_the_least_useful_lines_when_it_is_too_long() -> None:
    config = make_config(full=True, business_description="x " * 400)
    prompt = build_stage_prompt(config, full_facts(), Stage.IDENTIFICAR, now=NOW)

    assert prompt.sections["negocio"] <= SECTION_CAPS["negocio"]
    assert "Negocio: Escuela Capacita" in prompt.text
    assert "Métodos de pago" in prompt.text


def test_five_long_situations_are_cut_to_their_share_and_keep_every_title() -> None:
    prompt = build_stage_prompt(
        make_config(full=True), Facts(), Stage.IDENTIFICAR, now=NOW, situations=long_situations()
    )

    assert prompt.sections["situaciones"] <= SECTION_CAPS["situaciones"]
    for i in range(5):
        assert f"«Situación larga {i}»" in prompt.text
    assert "…" in prompt.text


def test_the_layers_go_from_the_least_to_the_most_specific() -> None:
    situation = long_situations(1)[0]
    text = build_stage_prompt(
        make_config(full=True), Facts(), Stage.COBRO, now=NOW, situations=[situation]
    ).text

    order = [
        text.index("PROCESO: INSTRUCCIONES DE LA ETAPA"),
        text.index("\nRUBRO"),
        text.index("\nNEGOCIO"),
        text.index("SITUACIONES DEL NEGOCIO"),
        text.index("PRIORIDAD:"),
    ]
    assert order == sorted(order)
    assert "Envía con enviar_recurso: malla" in text
    assert "usa derivar_a_humano" in text


def test_the_priority_line_is_there_even_without_situations() -> None:
    text = build_stage_prompt(make_config(), Facts(), Stage.COBRO, now=NOW).text

    assert "SITUACIONES DEL NEGOCIO" not in text
    assert "nunca se rompen." in text
    # The state of the conversation and the closing reminder come after it (gpt-4.1 follows the
    # later of two instructions that disagree, so the rules that decide a turn go last).
    assert text.index("PRIORIDAD:") < text.index("ETAPA ACTUAL:") < text.index("ANTES DE RESPONDER")
    assert text.rstrip().endswith("termina con el siguiente paso.")


def test_the_style_says_how_to_address_the_customer() -> None:
    plain = build_stage_prompt(make_config(), Facts(), Stage.INICIO, now=NOW).text
    full = build_stage_prompt(make_config(full=True), Facts(), Stage.INICIO, now=NOW).text

    assert "Trata al cliente de tú." in plain
    assert "Trata al cliente de usted en TODA la respuesta" in full
    assert "nunca de tú" in full
    assert "«¡Bienvenido a Escuela Capacita!»" in full


def test_estimate_tokens() -> None:
    assert estimate_tokens("") == 0
    assert estimate_tokens("a" * 35) == 10


# ------------------------------------------------------------- no stray braces

_BRACES = re.compile(r"\{[a-z][a-z0-9_.]*\}")


@pytest.mark.parametrize("template_key", sorted(TEMPLATES))
@pytest.mark.parametrize("stage", list(Stage))
@pytest.mark.parametrize("facts_kind", ["empty", "full"])
def test_no_placeholder_is_left_unresolved(
    template_key: str, stage: Stage, facts_kind: str
) -> None:
    facts = Facts() if facts_kind == "empty" else full_facts()
    text = _prompt(stage, facts, template_key=template_key)

    assert not _BRACES.findall(text)


# ----------------------------------------------------------------- placeholders


def test_a_missing_value_drops_the_whole_line() -> None:
    text = "Hola {nombre_cliente}\nSigue\nTel {telefono_humano} o {var.web}"

    assert resolve_placeholders(text, {"telefono_humano": "+51", "var.web": None}) == "Sigue"


def test_values_are_filled_in_a_single_pass() -> None:
    text = resolve_placeholders("Pregunta: {a}", {"a": "mira {b}", "b": "NO"})

    assert text == "Pregunta: mira {b}"


def test_the_platform_rules_name_the_assistant_and_the_business() -> None:
    text = _prompt(Stage.IDENTIFICAR)

    assert text.startswith("Eres Capacitín, asistente virtual de Escuela Capacita.")
    assert "9. Si usaste mostrar_opciones" in text
    # The model has the whole conversation and is told to use it (Giannier, 2026-10-26).
    assert "11. Tienes toda la conversación" in text
    assert text.count("\n10. ") == 1


def test_without_a_card_link_cobro_loses_that_line_and_no_sentence_is_cut() -> None:
    text = _prompt(Stage.COBRO)

    assert "pago con tarjeta" not in text
    assert "envíe la foto\n  del comprobante por aquí." in text


def test_with_a_card_link_cobro_mentions_it() -> None:
    options = ProcessOptions(link_pago_tarjeta="https://tienda.capacita.pe")
    text = _prompt(Stage.COBRO, options=options)

    assert "Si existe https://tienda.capacita.pe/" in text


def test_the_missing_data_fills_the_datos_stage() -> None:
    text = _prompt(Stage.DATOS, Facts(datos_faltantes=["dni", "correo"]))

    assert "Pide los datos que faltan (dni, correo)" in text


def test_the_paths_fill_the_qualification_stage() -> None:
    text = _prompt(Stage.CALIFICAR, template_key="certificaciones")

    assert "certificacion = Solo certificar" in text
    assert "programa = Programa completo" in text
    assert '"¿Quieres solo certificar la experiencia que ya tienes' in text


def test_a_tenant_variable_fills_its_placeholder() -> None:
    template = TEMPLATES["productos"]
    texts = {s: template.stage(s).instrucciones for s in Stage}
    texts[Stage.IDENTIFICAR] = "Destaca {var.temas}.\nSin variable {var.otra}."
    options = ProcessOptions(variables={"temas": "inglés o PMBOK"})
    config = make_config(options=options, stage_instructions=MappingProxyType(texts))

    text = build_stage_prompt(config, Facts(), Stage.IDENTIFICAR, now=NOW).text

    assert "Destaca inglés o PMBOK." in text
    assert "Sin variable" not in text


def test_the_edited_text_of_a_stage_replaces_the_default() -> None:
    template = TEMPLATES["productos"]
    texts = {s: template.stage(s).instrucciones for s in Stage}
    texts[Stage.PEDIDO] = "Texto propio del negocio."
    config = make_config(stage_instructions=MappingProxyType(texts))

    text = build_stage_prompt(config, Facts(), Stage.PEDIDO, now=NOW).text

    assert "Texto propio del negocio." in text
    assert "El pedido está en el ESTADO" not in text


# ------------------------------------------------------------------------ style


@pytest.mark.parametrize(
    ("ritmo", "phrase"),
    [
        ("directo", "Ve al grano."),
        ("equilibrado", "Una pregunta por mensaje."),
        ("paso_a_paso", "Explica cada paso"),
    ],
)
def test_the_pace_sets_the_style_text(ritmo: str, phrase: str) -> None:
    text = _prompt(Stage.IDENTIFICAR, behavior=Behavior(ritmo=ritmo))

    assert phrase in text


@pytest.mark.parametrize(
    ("detalle", "phrase"),
    [
        ("breve", "1 a 3 líneas (máximo 350 caracteres)"),
        ("moderado", "máximo 700 caracteres"),
        ("extenso", "hasta 1 400 caracteres"),
    ],
)
def test_the_detail_sets_the_style_text(detalle: str, phrase: str) -> None:
    text = _prompt(Stage.IDENTIFICAR, behavior=Behavior(detalle=detalle))

    assert phrase in text


def test_an_unknown_tone_pace_or_detail_falls_back_to_the_defaults() -> None:
    text = _prompt(Stage.IDENTIFICAR, behavior=Behavior(tone="x", ritmo="y", detalle="z"))

    assert "Tono cálido y natural." in text
    assert "Una pregunta por mensaje." in text
    assert "máximo 700 caracteres" in text


# --------------------------------------------------------------------- business


def test_the_business_section_has_one_line_per_topic() -> None:
    text = _prompt(Stage.IDENTIFICAR)

    for start in (
        "Negocio: Escuela Capacita (rubro instituto).",
        "Métodos de pago: Yape, transferencia bancaria; se paga antes de entregar "
        "(los datos para pagar se envían aparte).",
        "Entregas: delivery, zonas: Zona 1 de reparto S/ 8.50 (mismo día)",
        "recojo en tienda; envío gratis desde S/ 150.00.",
        "Locales: Sede 1: Av. Principal 100, Chiclayo · lunes: 09:00 a 18:00",
        "Horario de atención (America/Lima): lunes: 09:00 a 18:00.",
        "Cambios y devoluciones: cambios: sí; devoluciones: no; plazo 7 días",
        "Contacto: teléfono de una persona +51967285628; correo ventas@tienda.pe",
        "Tipos de cliente: Cliente tipo 1",
        "Promoción vigente: Promo automática número 1 — Descripción de Promo automática número 1",
    ):
        assert start in text, start


def test_cash_on_delivery_is_stated_in_the_payment_line() -> None:
    options = ProcessOptions(modalidad_pago="contra_entrega")

    assert "se paga al recibir el pedido" in _prompt(Stage.IDENTIFICAR, options=options)


def test_closing_promotions_are_not_announced_in_the_business_section() -> None:
    text = _prompt(Stage.IDENTIFICAR)
    business = text.split("NEGOCIO")[1].split("ESTADO DE LA CONVERSACIÓN")[0]

    assert "Cupón 20 %" not in business


def test_a_business_with_nothing_configured_still_builds() -> None:
    config = make_config(
        info=BusinessInfoView(),
        payment_methods=(),
        promotions=(),
        business_name=None,
        business_description=None,
        industry=None,
        opening_hours={},
    )

    text = build_stage_prompt(config, Facts(), Stage.INICIO, now=NOW).text

    assert "Negocio: el negocio." in text
    assert "asistente virtual de el negocio" in text
    assert "se paga antes de entregar" not in text


def test_no_delivery_and_no_pickup_is_said_plainly() -> None:
    info = BusinessInfoView(entregas=Entregas(hace_delivery=False, recojo_en_tienda=False))

    assert "Entregas: el negocio no hace entregas." in _prompt(Stage.IDENTIFICAR, info=info)


def test_delivery_without_zones_is_to_be_agreed() -> None:
    info = BusinessInfoView(entregas=Entregas(hace_delivery=True, recojo_en_tienda=False))

    assert "Entregas: delivery (costo a coordinar)." in _prompt(Stage.IDENTIFICAR, info=info)


def test_a_business_without_a_physical_place_says_so() -> None:
    info = BusinessInfoView(locales=Locales(atiende_presencial=False))

    assert "Locales: sin atención presencial (solo en línea)." in _prompt(
        Stage.IDENTIFICAR, info=info
    )


def test_a_long_list_is_cut_and_says_how_many_were_left_out() -> None:
    text = _prompt(Stage.IDENTIFICAR, full=True)

    assert re.search(r"\(y \d+ más\)", text)


def test_promotions_beyond_the_limit_are_counted() -> None:
    assert "(y 4 promociones más)" in _prompt(Stage.IDENTIFICAR, full=True)


# ----------------------------------------------------------------------- state


def test_a_name_from_the_profile_is_not_a_confirmed_name() -> None:
    facts = Facts(nombre_cliente="Ana", nombre_confirmado=False)

    text = _prompt(Stage.IDENTIFICAR, facts)

    assert "- Cliente: Ana (nombre de perfil, sin confirmar)" in text


def test_a_name_the_customer_gave_is_confirmed() -> None:
    facts = Facts(nombre_cliente="Ana", nombre_confirmado=True)

    assert "- Cliente: Ana (nombre confirmado)" in _prompt(Stage.IDENTIFICAR, facts)


def test_no_name_at_all() -> None:
    assert "- Cliente: nombre desconocido" in _prompt(Stage.IDENTIFICAR)


def test_the_state_shows_the_order_in_one_line() -> None:
    facts = Facts(pedido=_order(missing=3))

    text = _prompt(Stage.PEDIDO, facts)

    assert (
        "- Pedido #42 (borrador): 1× Paquete de certificaciones número 1 [SIAF, Gestión pública]"
        " · Total S/ 500.00 · faltan 3 selecciones"
    ) in text


def test_no_order_is_stated() -> None:
    assert "- Pedido: ninguno" in _prompt(Stage.IDENTIFICAR)


def test_a_long_order_lists_the_first_items_and_counts_the_rest() -> None:
    facts = Facts(pedido=_order(items=12))

    assert "y 4 más" in _prompt(Stage.PEDIDO, facts)


def test_missing_and_complete_customer_data() -> None:
    assert "- Datos del cliente: faltan dni, correo" in _prompt(
        Stage.DATOS, Facts(datos_faltantes=["dni", "correo"])
    )
    assert "- Datos del cliente: completos" in _prompt(Stage.COBRO, Facts())


def test_payment_methods_sent_or_not() -> None:
    assert "- Métodos de pago: no enviados" in _prompt(Stage.COBRO, Facts())
    assert "- Métodos de pago: enviados" in _prompt(Stage.COBRO, Facts(metodos_pago_enviados=True))


def test_resources_and_options_already_sent_are_listed_only_when_there_are_some() -> None:
    plain = _prompt(Stage.IDENTIFICAR, Facts())
    sent = _prompt(
        Stage.IDENTIFICAR,
        Facts(recursos_enviados=["saludo", "flyer"], opciones_mostradas=["caminos"]),
    )

    assert "Recursos ya enviados" not in plain
    assert "- Recursos ya enviados (NO reenviar): saludo, flyer" in sent
    assert "- Opciones ya mostradas: caminos" in sent


def test_the_path_is_shown_with_its_title() -> None:
    text = _prompt(Stage.IDENTIFICAR, Facts(camino="certificacion"), template_key="certificaciones")

    assert "- Camino: certificacion (Solo certificar)" in text


def test_a_recent_ad_is_shown_and_an_old_one_is_not() -> None:
    recent = Facts(entrada_anuncio=EntradaAnuncio(headline="5 x S/ 500", recibido_en=NOW))
    old = Facts(
        entrada_anuncio=EntradaAnuncio(headline="5 x S/ 500", recibido_en=NOW - timedelta(days=8))
    )

    assert '- Llegó por anuncio: "5 x S/ 500"' in _prompt(Stage.ANUNCIO, recent)
    assert "Llegó por anuncio" not in _prompt(Stage.IDENTIFICAR, old)


@pytest.mark.parametrize("stage", [Stage.IDENTIFICAR, Stage.PEDIDO, Stage.DATOS, Stage.COBRO])
def test_the_closing_promotion_is_shown_where_the_customer_weighs_the_price(stage: Stage) -> None:
    facts = Facts(promos_cierre=[PromoCierre(id="1", nombre="Cupón 20 %", vigencia_horas=24)])

    assert (
        '- Promoción de cierre disponible (solo si duda): "Cupón 20 %" (promocion_id=1)'
        " · vale 24 h desde que se ofrece · no ofrecida"
    ) in _prompt(stage, facts)


@pytest.mark.parametrize(
    "stage",
    [
        Stage.INICIO,
        Stage.CALIFICAR,
        Stage.ANUNCIO,
        Stage.VERIFICACION,
        Stage.CERRADO,
        Stage.ASESORIA,
    ],
)
def test_the_closing_promotion_is_hidden_in_the_other_stages(stage: Stage) -> None:
    facts = Facts(promos_cierre=[PromoCierre(id="1", nombre="Cupón 20 %", vigencia_horas=24)])

    assert "Promoción de cierre" not in _prompt(stage, facts)


def test_an_offered_promotion_shows_when_it_expires_in_the_business_time_zone() -> None:
    promo = PromoCierre(
        id="1", nombre="Cupón 20 %", vigencia_horas=24, ofrecida_vence=NOW + timedelta(hours=3)
    )

    text = _prompt(Stage.COBRO, Facts(promos_cierre=[promo]))

    # 18:00 UTC is 13:00 in Lima.
    assert "· ofrecida, vence 12/10 13:00" in text
    assert "no ofrecida" not in text


def test_advisory_handover_is_stated_in_the_words_the_stage_text_checks() -> None:
    derived = _prompt(Stage.ASESORIA, Facts(asesoria_derivada_en=NOW))
    pending = _prompt(Stage.ASESORIA, Facts())

    assert "- Asesoría: asesoría ya derivada" in derived
    assert "asesoría ya derivada a una persona" not in pending


@pytest.mark.parametrize(
    ("facts", "line"),
    [
        (Facts(), "- Pago: —"),
        (Facts(pago_estado="por_verificar"), "- Pago: por verificar (en revisión del negocio)"),
        (Facts(pago_estado="confirmado"), "- Pago: confirmado"),
        (
            Facts(pago_estado="rechazado", motivo_rechazo="el monto no coincide"),
            "- Pago: rechazado: el monto no coincide",
        ),
        (
            Facts(motivo_rechazo="falta la dirección"),
            "- Pago: — (el negocio rechazó antes: falta la dirección)",
        ),
        (
            Facts(modalidad_pago="contra_entrega"),
            "- Pago: contra entrega: resume el pedido y pídele que lo confirme; no hay métodos "
            "de pago que enviar",
        ),
        (
            Facts(modalidad_pago="contra_entrega", motivo_rechazo="falta la dirección"),
            "que enviar (el negocio rechazó antes: falta la dirección)",
        ),
    ],
)
def test_the_payment_line(facts: Facts, line: str) -> None:
    assert line in _prompt(Stage.COBRO, facts)


def test_this_turn_lists_what_only_holds_for_the_turn() -> None:
    turn = TurnContext(
        opcion="El cliente eligió: Paquete 5 (producto_id=abc)",
        comprobante="la imagen NO es un comprobante",
    )
    prompt = build_stage_prompt(make_config(), Facts(), Stage.COBRO, now=NOW, turn=turn)

    assert (
        "ESTE TURNO\n- El cliente eligió: Paquete 5 (producto_id=abc)\n"
        "- la imagen NO es un comprobante\nETAPA ACTUAL: COBRO"
    ) in prompt.text


def test_an_image_alone_is_announced() -> None:
    turn = TurnContext(imagen_recibida=True)
    text = build_stage_prompt(make_config(), Facts(), Stage.IDENTIFICAR, now=NOW, turn=turn).text

    assert "ESTE TURNO\n- [El cliente envió una imagen]" in text


def test_without_turn_context_there_is_no_this_turn_block() -> None:
    text = _prompt(Stage.IDENTIFICAR)

    assert "ESTE TURNO" not in text
    assert "ETAPA ACTUAL: IDENTIFICAR" in text


def test_the_current_stage_ends_the_state_block_and_the_closing_reminder_follows() -> None:
    text = _prompt(Stage.DATOS, Facts(datos_faltantes=["dni"]))

    assert "ETAPA ACTUAL: DATOS\n\nANTES DE RESPONDER" in text
    # The stage's instructions come before, after the general rules and before the specific ones.
    assert text.index("PROCESO: INSTRUCCIONES DE LA ETAPA\nEl pedido está completo.") < text.index(
        "\nNEGOCIO"
    )


def test_customer_data_and_payment_status_are_shown_only_with_an_order() -> None:
    """«Datos del cliente: faltan DNI» before any order made gpt-4.1-mini ask for the DNI in its
    greeting and refuse to add the product until it had it."""
    before = _prompt(Stage.IDENTIFICAR, Facts(datos_faltantes=["dni", "apellidos"]))
    during = _prompt(Stage.DATOS, Facts(datos_faltantes=["dni", "apellidos"]))

    assert "Datos del cliente" not in before and "- Métodos de pago:" not in before
    assert "Datos del cliente: faltan dni, apellidos" in during


def test_an_unknown_time_zone_does_not_break_the_state_block() -> None:
    promo = PromoCierre(
        id="1", nombre="Cupón", vigencia_horas=24, ofrecida_vence=NOW + timedelta(hours=1)
    )

    text = _prompt(Stage.COBRO, Facts(promos_cierre=[promo]), timezone="Marte/Olimpo")

    assert "· ofrecida, vence" in text


@pytest.mark.parametrize("tone", ["profesional", "cercano", "juvenil"])
@pytest.mark.parametrize(
    ("tratamiento", "emojis"), [("tu", "si"), ("usted", "no"), ("tu", "pocos")]
)
def test_the_style_says_treatment_and_emojis_once_and_as_the_business_set_them(
    tone: str, tratamiento: str, emojis: str
) -> None:
    """The tone texts used to carry their own «trata de tú» and emoji rule, against the settings."""
    text = _prompt(
        Stage.IDENTIFICAR, behavior=Behavior(tone=tone, tratamiento=tratamiento, emojis=emojis)
    )
    style = text.split("ESTILO", 1)[1].split("\n\n", 1)[0]

    assert style.count("Trata ") == 1
    assert style.lower().count("emoji") == (0 if emojis == "no" else 1) + (
        1 if emojis == "no" else 0
    )


def test_a_long_description_is_cut_at_a_sentence_not_mid_word() -> None:
    description = (
        "Bebidas Yana Yacu es una marca peruana de agua de mesa. Ofrecemos distintas líneas de "
        "productos, con presentaciones para hogares, empresas y distribuidores. Atendemos a "
        "clientes particulares y empresas en todo Lima, con reparto propio y pedidos recurrentes "
        "para negocios que necesitan abastecerse cada semana sin preocuparse por nada más."
    )
    text = _prompt(Stage.IDENTIFICAR, business_description=description)
    line = next(line for line in text.splitlines() if line.startswith("Negocio:"))

    assert line.endswith(".") or line.endswith("…")
    assert "empres\n" not in text and not line.endswith(" empres")


class TestTheUniversalChargeReadsOnlyTheBusinessWay:
    """Feature 020, T2.7: COBRO carried both ways; a cash-on-delivery shop read about a tool it
    does not have."""

    def test_paid_up_front(self) -> None:
        prompt = _prompt(Stage.COBRO, Facts(), template_key="universal")

        assert "- Pago previo: si los métodos de pago no se enviaron" in prompt
        assert "contra entrega:" not in prompt.split("ESTADO DE LA")[0]

    def test_cash_on_delivery(self) -> None:
        prompt = _prompt(
            Stage.COBRO, Facts(modalidad_pago="contra_entrega"), template_key="universal"
        )
        stage_text = prompt.split("ESTADO DE LA")[0]

        assert "- Pago contra entrega: si aún no confirmó, resume el pedido" in stage_text
        assert "enviar_metodos_pago" not in stage_text and "Pago previo" not in stage_text


def test_a_long_package_choice_is_cut_at_a_word() -> None:
    from app.agent.prompts.stage import _choice

    assert _choice("Certificado en Seguridad y salud en el trabajo") == (
        "Certificado en Seguridad y salud en el…"
    )
    assert _choice("Inventarios") == "Inventarios"
