# Tasks — Agent-core Completo

**Feature ID:** 003 · **Estimación:** 2 semanas · Commits: `feat(agent): ... [TASK-3XX]`

> **Cerradas el 2026-10-14 en versión mínima por la 004 (C-02, TASK-445).** Lo que existe: circuit breaker
> por tenant en Redis (3 fallas en 60 s → 2 min abierto, aviso `llm_caido`), nivel de inteligencia → modelo
> desde settings (`agent/model_policy.py`, sin tabla `model_policies` ni proveedor de respaldo), ventana de
> 20 mensajes más los hechos de la conversación, prompt por etapa y capas (017) en lugar de Jinja por rubro,
> trazas por turno, evals (`make evals` y evals de catálogo), derivación (`derivar_a_humano`, TASK-443).
> **No se construyó** (fuera del MVP por C-02): resumen continuo y perfil de contacto (304), proveedor de
> respaldo y panel admin de `model_policies` (302, 308), costo estimado por traza (306).

- [x] **TASK-301: Migraciones** — contact_profiles, model_policies (+seed global), prompt_versions, ALTERs a conversations. Depends: F002 completa.
- [x] **TASK-302: LLMProvider compuesto + circuit breaker** — primary/fallback desde model_policies, breaker en Redis, alertas. Verificación: contract tests con primario simulado caído.
- [x] **TASK-303: Prompt builder por rubro** — plantillas Jinja2 (6 rubros), capa de overrides del tenant, contexto dinámico (horarios/moneda/fecha), versionado con checksum. Depends: 301.
- [x] **TASK-304: Memoria — rolling summary + perfil de contacto** — job de resumen con campos obligatorios, inclusión en contexto, job update_contact_profile al cerrar. Depends: 303. Verificación: test de conversación 40+ mensajes.
- [x] **TASK-305: Guardrails + escalamiento** — pre/post guards, tool escalar_a_humano (needs_human + notificación), heurísticas de frustración, budget de loop, manejo de tool_error. Depends: 303.
- [x] **TASK-306: Trazas enriquecidas + costo estimado** — iteraciones, budget, versión de prompt, costo por tokens según modelo. Depends: 302, 305.
- [x] **TASK-307: Runner de evals + datasets por rubro** — formato YAML, 15 doradas × 3 rubros, umbral 90%, job CI requerido para agent/ y prompts/. Depends: 305. Milestone humano: Giannier revisa y ajusta las conversaciones doradas (conoce a los clientes reales).
- [x] **TASK-308: Panel admin de model_policies (API+UI) + integración canal real** — CRUD con audit, smoke en staging con failover manual. Depends: 302, 306.

Dependencias: 301 → 303 → 304/305 → 306/307 → 308; 302 en paralelo tras 301.
