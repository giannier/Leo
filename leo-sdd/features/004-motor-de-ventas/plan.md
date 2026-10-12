# Plan — Motor de ventas (004 v2)

**Spec:** `spec.md` · **Decisiones:** `clarifications.md` · **Contenido del motor:** `plantillas.md`
**Datos:** `data-model.md` · **API y herramientas:** `contracts/api.md`

## Resumen técnico

El turno del agente deja de ser "prompt grande + todas las herramientas" y pasa a un **pipeline**:
1. Se cargan los hechos de la conversación.
2. Una función pura calcula la etapa.
3. El prompt lleva solo las instrucciones y herramientas de esa etapa.
4. Las herramientas escriben hechos y datos reales (pedidos, contactos).
5. Un formateador sin LLM arma 1 o 2 burbujas.
6. Se recalcula la etapa para registrar el avance.

Es el mismo patrón que ya funciona en Capacita v21 (n8n), llevado a código con tests.

`TurnContext` guarda lo que solo vale para el turno (opción tocada, resultado del comprobante, imagen recibida). No se persiste; el PromptBuilder lo muestra en el ESTADO como "ESTE TURNO".

## Constitution Check

| Principio (CLAUDE.md / constitution de Leo) | Cumple | Cómo |
|---|---|---|
| Aislamiento multi-tenant | ✅ | `tenant_id` en todas las tablas nuevas y tests en `tests/isolation` (cada migración + TASK-446) |
| Cobertura por módulo (agent-core ≥ 80) | ✅ | `process`, `agent`, `orders` ≥ 80 %; motor y pricing con tests de tabla |
| mypy + ruff limpios | ✅ | Gate en cada task |
| Evals adjuntos al PR cuando cambia el prompt | ✅ | Suite nueva "flujos de venta" (TASK-447) |
| Proveedores detrás de adaptadores | ✅ | Interactivos y media solo en `providers/whatsapp/ycloud.py`; visión vía el perfil LLM |
| Secretos cifrados, sin leer `.env` | ✅ | No hay secretos nuevos; usa los perfiles LLM existentes |
| Sin dependencias nuevas sin aprobación | ✅ | No se agrega ninguna librería. Si Claude Code ve que hace falta una, la propone en el PR |
| Fase 2 (011–016) solo con ratificación | ✅ | Esto es MVP 004 (C-01) |

## Arquitectura

### Pipeline del turno

```
webhook YCloud ─▶ workers/channel.py (debounce SET NX EX 8, ya existe)
                     │
                     ▼
             TurnPipeline.run(conversation_id)
  1. load      conversación, contacto, config efectiva (ProcessConfigService: plantilla + opciones +
               overrides, caché 60 s por tenant)
  2. facts     FactsLoader.load() = guardados + derivados
  3. previa    stage_previa = compute_stage(..., ignorar_derivacion=True)
  4. inbound   referral → facts.entrada_anuncio
               interactive → camino (hecho) o línea de contexto (TurnContext)
               imagen: si stage_previa = COBRO, o VERIFICACION con rechazo → ProofPipeline
                       (visión → payment_proof → pedido por_confirmar → aviso al operador);
                       si no → TurnContext "[El cliente envió una imagen]"
               (todo esto corre AUNQUE haya derivación: un comprobante nunca se pierde)
  5. reload    FactsLoader.reload_derived() si el paso 4 cambió algo
  6. stage     compute_stage() + funnel_for() → StateRepo.save(..., reason="turno" | "comprobante")
  7. gate      asistente pausado · opt-out · derivación vigente (aunque la etapa sea VERIFICACION)
               → guardar y salir sin responder
               circuit breaker abierto → unsupported_fallback y salir (C-02)
  8. prompt    PromptBuilder: reglas plataforma │ estilo │ negocio compacto │ ESTADO (+ ESTE TURNO) │ etapa
  9. tools     ToolRegistry.for_stage(template, stage, facts) + TurnBudget(3, reserva_texto=1)
 10. llm       agent/core.py (existente) con modelo según nivel de inteligencia
 11. format    formatter.to_bubbles(texto, detalle, budget) → 1–2 burbujas
 12. send      adaptador (o salientes sandbox); las herramientas ya enviaron sus media e interactivos
 13. after     facts.saludo_enviado = true · recompute stage → evento si cambió · reset seguimiento
 14. trace     agent_traces (+ stage, funnel, tools_enabled, outbound_count, model_tier), datos enmascarados
```

**Orden de los mensajes salientes:** los media y los interactivos se envían en el momento en que la herramienta los llama (como en n8n). El texto final sale al último. Esto respeta el orden natural ("aquí tienes el flyer… ¿cuál prefieres?").

**Concurrencia:** el debounce ya serializa los mensajes por conversación. Además, `StateRepo.save` usa `version` (UPDATE … WHERE version = :v); si choca, recarga los hechos y recalcula una vez.

### Módulos nuevos y cambios

```
backend/app/
├── process/                         NUEVO
│   ├── templates/
│   │   ├── base.py                  Stage, Template, ToolPolicy, ProcessOptions (pydantic) + textos [base]
│   │   ├── productos.py
│   │   ├── certificaciones.py
│   │   └── servicios.py
│   ├── engine.py                    compute_stage(), funnel_for()  — puras
│   ├── facts.py                     Facts (modelo), FactsLoader
│   ├── state.py                     StateRepo (save con versión + evento)
│   ├── interactive.py               parseo de IDs con prefijo
│   └── service.py                   ProcessConfigService: lectura de config efectiva + caché (TASK-414);
│                                    escritura de opciones, overrides y restaurar (TASK-435)
├── agent/
│   ├── core.py                      CAMBIA: recibe tools y prompt del pipeline
│   ├── pipeline.py                  NUEVO: TurnPipeline
│   ├── prompt_builder.py            CAMBIA: secciones nuevas, presupuesto de tokens
│   ├── budget.py                    NUEVO: TurnBudget
│   ├── formatter.py                 NUEVO: port de "Formato WhatsApp" v21
│   ├── circuit.py                   NUEVO: circuit breaker por tenant (Redis)
│   ├── model_policy.py              NUEVO: nivel → modelo, limitado por plan
│   └── tools/
│       ├── registry.py              NUEVO: catálogo de tools + habilitación por etapa
│       ├── catalog.py               CAMBIA: campos nuevos en resultados
│       ├── resources.py             enviar_recurso
│       ├── options.py               mostrar_opciones
│       ├── orders.py                gestionar_pedido
│       ├── customer.py              registrar_datos_cliente
│       ├── payments.py              enviar_metodos_pago
│       ├── path.py                  elegir_camino
│       └── handoff.py               derivar_a_humano
├── orders/                          NUEVO
│   ├── models.py  service.py        OrderService (transiciones), numeración
│   ├── pricing.py                   función pura: ítems + promociones → totales
│   ├── proofs.py  vision.py         ProofPipeline + extracción estructurada
│   └── router.py
├── business_info/                   NUEVO: secciones, FAQ (→ knowledge), promociones, router
├── media_library/                   NUEVO: subida a MinIO, validación, router, refresco de media_id
├── followups/                       NUEVO: jobs ARQ (seguimiento, descarte, fin de pausa)
└── providers/whatsapp/ycloud.py     CAMBIA: send_media, send_buttons, send_list, upload_media,
                                             parseo de interactive / referral / BSUID
frontend/src/app/(panel)/
├── asistente/                       secciones: info básica, negocio, comportamiento, proceso,
│                                    multimedia (+ simulador con modo depuración)
├── pedidos/                         lista + resumen + detalle con comprobante
└── components/ActivationChecklist   widget del inicio
```

Las rutas exactas del frontend siguen la estructura que ya tenga el repo.

### Prompt: presupuesto de tokens

| Sección | Tope |
|---|---|
| Reglas de plataforma | 450 |
| Estilo (ritmo, detalle, tono, reglas adicionales) | 600 |
| Negocio compacto (métodos, locales, entregas, tipos, devoluciones, contacto y promociones vigentes en 1 línea c/u) | 1 800 |
| ESTADO | 500 |
| Instrucciones de la etapa | 1 200 |
| Definiciones de herramientas habilitadas | 1 400 |
| **Total base** | **≤ 6 000** |

Las FAQ no van en el prompt: van por `consultar_conocimiento`. El catálogo tampoco: va por `buscar_productos`. Hay un test que arma el prompt de cada etapa de las 3 plantillas con un negocio "lleno" y falla si se pasa de 6 000 tokens.

### Formateador

Es un port directo del nodo *Formato WhatsApp1* de Capacita v21, que ya está probado con 7 casos:
- Markdown → WhatsApp (`**` → `*`, listas → `•`, links `[t](u)` → `t: u`).
- Quita las líneas de "nota interna" y los bloques JSON.
- Corta en máximo 2 burbujas solo entre párrafos y nunca a mitad de frase.
- El límite depende del nivel de detalle y del presupuesto restante.

Los casos de `harness21.js` se traducen a pytest.

### Comprobantes (visión)

- Llamada al perfil LLM con imagen y salida estructurada (JSON schema).
- Con el perfil `openai` usa el modelo del nivel "medio" (la lectura de montos necesita precisión). Si el tenant usa su propio LLM sin visión, el comprobante se guarda con `observaciones=['sin_datos']` y el operador lo revisa a ojo.
- Tiempo máximo de 8 s; si se pasa, se guarda sin datos y el turno sigue.

## Decisiones técnicas

| Decisión | Alternativas | Razón |
|---|---|---|
| Etapa calculada por función pura en código | Que el LLM decida la etapa; máquina de estados editable en UI | Determinista y testeable; ya probado en Capacita v21. La UI edita textos y opciones, no la lógica (C-05) |
| Hechos guardados + derivados | Todo en `facts` | Evita desalineación con pedidos y contactos (C-03) |
| Plantillas en código con versión | Plantillas en BD | Cambios revisables en PR y con tests; el tenant solo personaliza textos |
| Comprobante por pipeline, no por tool | Tool `registrar_pago` llamada por el modelo | El modelo se puede olvidar de llamarla; el pipeline no (C-08) |
| Media e interactivos enviados por la tool | Acumular y enviar al final | Mantiene el orden natural y es igual a v21; el presupuesto evita excesos |
| Formateador sin LLM | Format Chain con LLM (v19) | Menos latencia, cero costo y resultado predecible |
| FAQ indexada en conocimiento | FAQ en el prompt | No crece el prompt; reutiliza el RAG existente |
| Promociones por función pura `pricing` | Que el modelo aplique descuentos | Regla 2 de plataforma: el modelo nunca calcula precios |

## Riesgos

| Riesgo | Probabilidad | Impacto | Mitigación |
|---|---|---|---|
| Campos de YCloud para interactivos, referral o BSUID distintos a lo asumido | Media | Alto | TASK-418 empieza por la doc de YCloud y fixtures reales; adaptador con tests de contrato |
| El modelo no llama `elegir_camino` cuando el cliente escribe | Media | Medio | Botones primero; instrucción explícita; eval dedicado (escenario 2 de Capacita) |
| Prompt de etapa > 6 000 tokens con negocios grandes | Baja | Medio | Topes por sección + test de presupuesto |
| Visión lee mal montos | Media | Bajo | Nunca confirma: solo marca observaciones; el operador decide |
| Costo de evals con modelo real | Alta | Bajo | Evals por plantilla (24 conversaciones), solo antes del PR |
| Cambio de plantilla con conversaciones en curso | Baja | Medio | Recalculo en el siguiente turno; hechos compatibles entre plantillas |
| Scope grande para una sola feature | Media | Alto | Fases con milestone; Fase A (motor + tools + Capacita) ya entrega valor solo |

## Fases y milestones

| Fase | Contenido | Milestone (lo valida Giannier) |
|---|---|---|
| **A · Motor** | Conciliación, migraciones, motor de etapas, hechos, prompt, registry (con las 10 herramientas declaradas; las de venta como stub `no_disponible`), formateador, presupuesto, adaptador YCloud (interactivos, media, referral) | En el simulador, Capacita recorre INICIO → CALIFICAR → IDENTIFICAR con botones y flyer (sin pedidos todavía) |
| **B · Venta** | Pedidos, pricing, promociones, datos del cliente, métodos de pago, comprobantes, página Pedidos, avisos | Pedido completo en el simulador con comprobante, confirmado desde Pedidos |
| **C · Configuración** | Secciones del negocio, comportamiento, proceso, multimedia, checklist, modo depuración | Un tercero configura una tienda en ≤ 30 min |
| **D · Autonomía** | Seguimiento, derivación con pausa, circuit breaker, niveles de inteligencia | Seguimiento y derivación verificados en el simulador |
| **E · Cierre** | Evals, aislamiento, cobertura, e2e, 3 negocios de aceptación | Criterios de aceptación de la spec en verde |

## Estimación

Con Claude Code y una persona revisando, son 49 tasks (4 son milestones tuyos):
- Fase A: 5–6 días.
- Fase B: 5 días.
- Fase C: 5 días.
- Fase D: 2–3 días.
- Fase E: 3 días.

**Total: 4 a 5 semanas.** La Fase A sola ya permite probar Capacita en Leo.

## Lo que no cambia

- El webhook, el debounce, la idempotencia de `webhook_events` y el opt-out (`contacts.opted_out_at` y `opt_out_keywords`) siguen igual.
- El `catalog_guard` sigue activo, pero las selecciones libres del pedido activo (`bundle_permite_libres`, ej. temas de certificación) entran en su lista blanca. Si no, bloquearía el "certificamos cualquier tema" de Capacita. TASK-401 confirma cómo funciona hoy el guard y TASK-426 agrega la lista blanca.
- El simulador sigue siendo el mismo componente: solo recibe salientes tipados y el panel de depuración.

## Decisiones Posteriores

### 2026-10-12 — `consultar_catalogo` frente a `buscar_productos` y `ver_producto` (TASK-417)

**Estado: pendiente de ratificar por Giannier.** Se implementó para no bloquear la Fase A; si no te convence, se revierte cambiando `LEGACY_ALIASES` y `build_default_registry`.

**Problema.** `TOOL_NAMES` (`contracts/api.md §7`) nombra `buscar_productos` y `ver_producto`; el agente de hoy tiene una sola tool, `consultar_catalogo`, que mencionan 19 pruebas de `test_agent_core`, `test_prompt_builder`, `test_sandbox`, `test_agent_isolation`, los evals y el texto del prompt viejo (`conciliacion.md §3.4`). Renombrar sin más rompe todo eso antes de que exista el pipeline que lo reemplaza.

| Punto | Elegido | Razón | Alternativa descartada |
|---|---|---|---|
| Registry del agente de hoy (`build_default_registry`, sin pipeline) | Sin cambios: `consultar_catalogo` y `consultar_conocimiento`, con la salida de texto de siempre para el catálogo | Las pruebas y los evals actuales siguen siendo válidos hasta TASK-421 | Renombrar ya (rompe la suite y los 5 casos dorados) |
| Registry por etapa (`ToolCatalog.for_stage`) | Ofrece `buscar_productos` y `ver_producto`, nunca `consultar_catalogo` | Es el contrato de `§7` y de las plantillas | Ofrecer también el nombre viejo (el modelo vería dos tools para lo mismo) |
| Llamadas al nombre viejo dentro de un registry por etapa | Un alias (`ToolRegistry.alias`) lo resuelve a `buscar_productos`; no aparece en `specs()` ni en `names()` | Un historial de conversación o un prompt escrito antes del cambio sigue funcionando; el argumento `consulta` es el mismo | Responder "la herramienta no existe" (el modelo reintentaría o inventaría) |
| `consultar_conocimiento` | Mantiene el parámetro `consulta` (la spec decía `pregunta`) y migra al sobre `ToolResult` | `conciliacion.md §3.4`: el repo manda; los dos parámetros llaman lo mismo | Renombrar el parámetro (sin ganancia, rompe el prompt viejo) |
| Sobre `ToolResult` | `AgentCore._run_tool` acepta `ToolResult` o texto. Un `ToolResult` se entrega al modelo como texto que **empieza con `estado: hecho\|omitido\|no_disponible`** (un error con `ERROR: …`, como detecta la traza hoy) | La regla 4 de plataforma ("nunca digas que hiciste algo si la herramienta no devolvió `hecho`") solo funciona si el modelo ve el estado. Prosa y no JSON: el comentario de `catalog.py` ya explica por qué | JSON puro (más errores de transcripción en precios) |
| Herramientas aún sin construir | Las 10 están declaradas con su schema final; las 7 que llegan en TASK-420 y fases B/D devuelven `no_disponible` ("no la menciones") | El presupuesto de tokens y las reglas por etapa se prueban con los schemas reales; cada task posterior solo reemplaza el handler | Declarar solo las implementadas (el test de presupuesto subestimaría) |

**Consecuencia para TASK-421.** El pipeline arma el registry con `registry_for_stage(...)` y deja de usar `build_default_registry()`. Cuando eso pase, `consultar_catalogo` queda solo como alias y se puede retirar su handler de texto (y reescribir las pruebas que lo nombran).

**Pendiente de TASK-420.** `buscar_productos` ya devuelve `modo_venta`, `bundle_size` y `destacado`, y ordena los destacados primero; faltan los recursos vinculados, las promociones automáticas aplicables (necesitan `pricing`, TASK-424) y fijar `vio_productos` (los tools ya reciben `ToolContext.facts_patch` para eso).
