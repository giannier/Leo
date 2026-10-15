"""Evals a catalogue version must pass before it is published (017, C-04, TASK-466).

A new version of a process template or a rubro reaches every business that runs
it, so it is checked first, against the **draft** content:

* **Structure** (no model): every stage has rules, every placeholder is known,
  the texts fit their prompt sections, a rubro has FAQs, objections and
  suggested situations within the situation limits, and a switched-off rule
  really leaves the prompt.
* **Behaviour** (the platform model, `economico`): a process version still makes
  the agent send the payment methods at COBRO; a rubro version lets a
  business's situation win over it and does not make the agent invent a price.

Without a usable model the run **fails** with the reason; it never passes by
default. The result is stored on the version with the hash of the content it
judged, and publishing requires both: evals that passed, on this exact content.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
import uuid
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from decimal import Decimal
from types import MappingProxyType
from typing import Any

from sqlalchemy.orm import Session

from app.agent.prompts.stage import SECTION_CAPS, build_stage_prompt
from app.agent.situations import SelectedSituation
from app.agent.tools.definitions import registry_for_stage
from app.models.catalog import CatalogVersion
from app.process.facts import Facts, Pedido, PedidoItem
from app.process.rule_split import join_rules
from app.process.service import Behavior, EffectiveProcessConfig
from app.process.templates import TEMPLATES
from app.process.templates.base import Stage, is_known_placeholder, placeholders_in
from app.providers.llm.base import ChatMessage, LLMError, LLMProvider
from app.repositories.catalog import CatalogRepository

logger = logging.getLogger(__name__)

EVALS_JOB = "run_catalog_evals"
MARKER = "@negocio_prueba"
_PRICE = re.compile(r"S/\s?\d")


@dataclass
class EvalCase:
    nombre: str
    ok: bool
    detalle: str = ""


@dataclass
class EvalRun:
    ok: bool
    hash: str
    modelo: str | None
    corrido_en: str
    casos: list[EvalCase] = field(default_factory=list)

    def as_json(self) -> dict[str, Any]:
        return asdict(self)


def content_hash(session: Session, version: CatalogVersion) -> str:
    """What the evals judged: changing the draft afterwards voids them."""
    catalog = CatalogRepository(session)
    payload = {
        "rules": [(r.rule_key, r.etapa, r.texto) for r in catalog.rules(version.id)],
        "faqs": [(f.pregunta, f.respuesta) for f in catalog.faqs(version.id)],
        "objections": [(o.objecion, o.manejo) for o in catalog.objections(version.id)],
        "situations": [
            (s.titulo, s.cuando_aplica, s.que_hace, sorted(s.campos.items()))
            for s in catalog.suggested_situations(version.id)
        ],
    }
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True).encode()
    return hashlib.sha256(raw).hexdigest()


def _config(
    template_key: str,
    *,
    stage_texts: dict[Stage, str] | None = None,
    rubro_rules: tuple[str, ...] = (),
) -> EffectiveProcessConfig:
    template = TEMPLATES[template_key]
    texts = stage_texts or {s: template.stage(s).instrucciones for s in Stage}
    return EffectiveProcessConfig(
        tenant_id=uuid.uuid4(),
        template=template,
        options=template.resolve(),
        stage_instructions=MappingProxyType(texts),
        behavior=Behavior(agent_name="Leo"),
        business_name="Negocio de prueba",
        industry=None,
        payment_methods=("yape",),
        rubro_rules=rubro_rules,
    )


def _cobro_facts() -> Facts:
    return Facts(
        saludo_enviado=True,
        nombre_cliente="Ana",
        nombre_confirmado=True,
        pedido=Pedido(
            items=[PedidoItem(nombre="Producto de prueba")],
            completo=True,
            total=Decimal("50.00"),
        ),
        datos_faltantes=[],
    )


# ----------------------------------------------------------------- structure


def _process_structure(
    session: Session, version: CatalogVersion
) -> tuple[list[EvalCase], dict[Stage, str]]:
    cases: list[EvalCase] = []
    template = TEMPLATES.get(version.key)
    if template is None:
        return [EvalCase("plantilla_existe", False, f"no hay plantilla {version.key}")], {}
    by_stage: dict[str, list[str]] = {}
    for rule in CatalogRepository(session).rules(version.id):
        by_stage.setdefault(rule.etapa or "", []).append(rule.texto)
    missing = [s.value for s in Stage if not by_stage.get(s.value)]
    cases.append(EvalCase("cada_etapa_tiene_reglas", not missing, ", ".join(missing)))
    texts = {s: join_rules(by_stage.get(s.value, [""])) for s in Stage}
    unknown = sorted(
        {p for text in texts.values() for p in placeholders_in(text) if not is_known_placeholder(p)}
    )
    cases.append(EvalCase("marcadores_conocidos", not unknown, ", ".join(unknown)))
    config = _config(version.key, stage_texts=texts)
    over = [
        s.value
        for s in Stage
        if build_stage_prompt(config, Facts(), s, now=datetime.now(UTC)).sections["etapa"]
        > SECTION_CAPS["etapa"]
    ]
    cases.append(EvalCase("cada_etapa_cabe_en_su_tope", not over, ", ".join(over)))
    return cases, texts


def _rubro_structure(
    session: Session, version: CatalogVersion
) -> tuple[list[EvalCase], tuple[str, ...]]:
    catalog = CatalogRepository(session)
    rules = tuple(r.texto for r in catalog.rules(version.id))
    cases = [
        EvalCase("tiene_reglas", bool(rules)),
        EvalCase("tiene_faqs", bool(catalog.faqs(version.id))),
        EvalCase("tiene_objeciones", bool(catalog.objections(version.id))),
    ]
    rubro = catalog.rubro(version.key)
    cases.append(
        EvalCase(
            "plantilla_sugerida_existe",
            rubro is not None and rubro.plantilla_sugerida in TEMPLATES,
            rubro.plantilla_sugerida if rubro else "rubro sin fila",
        )
    )
    bad = [
        s.titulo
        for s in catalog.suggested_situations(version.id)
        if not (
            0 < len(s.titulo) <= 80
            and 0 < len(s.cuando_aplica) <= 300
            and 0 < len(s.que_hace) <= 1000
        )
    ]
    cases.append(EvalCase("situaciones_sugeridas_en_limites", not bad, ", ".join(bad)))
    prompt = build_stage_prompt(
        _config("productos", rubro_rules=rules), Facts(), Stage.IDENTIFICAR, now=datetime.now(UTC)
    )
    cases.append(
        EvalCase(
            "reglas_caben_en_su_tope",
            prompt.sections.get("rubro", 0) <= SECTION_CAPS["rubro"],
            str(prompt.sections.get("rubro", 0)),
        )
    )
    if rules:
        dropped = build_stage_prompt(
            _config("productos", rubro_rules=rules[1:]),
            Facts(),
            Stage.IDENTIFICAR,
            now=datetime.now(UTC),
        ).text
        first = " ".join(rules[0].split())[:60]
        cases.append(EvalCase("regla_apagada_sale_del_prompt", first not in dropped))
    return cases, rules


# ----------------------------------------------------------------- behaviour


async def _ask(
    llm: LLMProvider, prompt: str, message: str, tools: Any = ()
) -> tuple[str, list[str]]:
    result = await llm.chat(
        [ChatMessage(role="system", content=prompt), ChatMessage(role="user", content=message)],
        tools=tools,
        temperature=0.0,
        max_tokens=400,
    )
    return result.content or "", [call.name for call in result.tool_calls]


async def _process_behaviour(
    llm: LLMProvider, template_key: str, texts: dict[Stage, str]
) -> list[EvalCase]:
    config = _config(template_key, stage_texts=texts)
    facts = _cobro_facts()
    registry = registry_for_stage(config.template, Stage.COBRO, facts, config.options)
    prompt = build_stage_prompt(
        config, facts, Stage.COBRO, now=datetime.now(UTC), tool_specs=registry.specs()
    )
    _, called = await _ask(llm, prompt.text, "Listo, ¿cómo te pago?", registry.specs())
    return [
        EvalCase(
            "cobro_envia_metodos_de_pago",
            "enviar_metodos_pago" in called,
            f"herramientas: {called}",
        )
    ]


async def _rubro_behaviour(
    llm: LLMProvider, template_key: str, rules: tuple[str, ...]
) -> list[EvalCase]:
    """A business's situation wins over a rubro rule that says otherwise, and an
    unconditional one is kept; neither makes the agent invent a price."""
    conflicting = (
        *rules,
        "Si preguntan por envíos, di que los coordina una persona del negocio y no des más "
        "detalles.",
    )
    shipping = SelectedSituation(
        id=uuid.uuid4(),
        titulo="Envíos",
        cuando_aplica="Preguntan por el envío o la entrega",
        que_hace="Di que el envío es gratis a todo el Perú.",
        distancia=0.2,
    )
    always = SelectedSituation(
        id=uuid.uuid4(),
        titulo="Instagram",
        cuando_aplica="En cada mensaje",
        que_hace=f"Termina cada mensaje invitando a seguirnos en Instagram: {MARKER}.",
        siempre=True,
    )
    now = datetime.now(UTC)
    facts = Facts(saludo_enviado=True)
    first = build_stage_prompt(
        _config(template_key, rubro_rules=conflicting),
        facts,
        Stage.IDENTIFICAR,
        now=now,
        situations=[shipping],
    )
    shipping_reply, _ = await _ask(llm, first.text, "Hola, ¿hacen envíos?")
    second = build_stage_prompt(
        _config(template_key, rubro_rules=rules),
        facts,
        Stage.IDENTIFICAR,
        now=now,
        situations=[always],
    )
    always_reply, _ = await _ask(llm, second.text, "Hola, buenas tardes. ¿Cuánto cuesta?")
    return [
        EvalCase(
            "situacion_gana_sobre_el_rubro",
            "gratis" in shipping_reply.lower(),
            shipping_reply[:160],
        ),
        EvalCase("situacion_siempre_se_cumple", MARKER in always_reply, always_reply[:160]),
        EvalCase(
            "no_inventa_precios",
            not _PRICE.search(shipping_reply) and not _PRICE.search(always_reply),
            always_reply[:160],
        ),
    ]


async def run_evals(session: Session, version: CatalogVersion, llm: LLMProvider | None) -> EvalRun:
    """Run every case on the version and store the result on it."""
    if version.kind == "proceso":
        cases, texts = _process_structure(session, version)
    else:
        cases, rules = _rubro_structure(session, version)
    model = getattr(llm, "chat_model", None) if llm is not None else None
    if llm is None:
        cases.append(EvalCase("modelo_disponible", False, "no hay un modelo configurado"))
    else:
        try:
            if version.kind == "proceso":
                if texts:
                    cases += await _process_behaviour(llm, version.key, texts)
            else:
                rubro = CatalogRepository(session).rubro(version.key)
                template = (
                    rubro.plantilla_sugerida
                    if rubro and rubro.plantilla_sugerida in TEMPLATES
                    else "productos"
                )
                cases += await _rubro_behaviour(llm, template, rules)
        except LLMError as exc:
            cases.append(EvalCase("modelo_disponible", False, f"{type(exc).__name__}: {exc}"))
    run = EvalRun(
        ok=all(case.ok for case in cases),
        hash=content_hash(session, version),
        modelo=str(model) if model else None,
        corrido_en=datetime.now(UTC).isoformat(),
        casos=cases,
    )
    version.evals = run.as_json()
    session.flush()
    return run


def evals_allow_publishing(session: Session, version: CatalogVersion) -> bool:
    """Passed, and on the content the draft has now."""
    result = version.evals or {}
    return bool(result.get("ok")) and result.get("hash") == content_hash(session, version)
