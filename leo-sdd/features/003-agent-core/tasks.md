# Tasks — Agent-core Completo

**Feature ID:** 003 · **Estimación:** 2 semanas · Commits: `feat(agent): ... [TASK-3XX]`

- [ ] **TASK-301: Migraciones** — contact_profiles, model_policies (+seed global), prompt_versions, ALTERs a conversations. Depends: F002 completa.
- [ ] **TASK-302: LLMProvider compuesto + circuit breaker** — primary/fallback desde model_policies, breaker en Redis, alertas. Verificación: contract tests con primario simulado caído.
- [ ] **TASK-303: Prompt builder por rubro** — plantillas Jinja2 (6 rubros), capa de overrides del tenant, contexto dinámico (horarios/moneda/fecha), versionado con checksum. Depends: 301.
- [ ] **TASK-304: Memoria — rolling summary + perfil de contacto** — job de resumen con campos obligatorios, inclusión en contexto, job update_contact_profile al cerrar. Depends: 303. Verificación: test de conversación 40+ mensajes.
- [ ] **TASK-305: Guardrails + escalamiento** — pre/post guards, tool escalar_a_humano (needs_human + notificación), heurísticas de frustración, budget de loop, manejo de tool_error. Depends: 303.
- [ ] **TASK-306: Trazas enriquecidas + costo estimado** — iteraciones, budget, versión de prompt, costo por tokens según modelo. Depends: 302, 305.
- [ ] **TASK-307: Runner de evals + datasets por rubro** — formato YAML, 15 doradas × 3 rubros, umbral 90%, job CI requerido para agent/ y prompts/. Depends: 305. Milestone humano: Giannier revisa y ajusta las conversaciones doradas (conoce a los clientes reales).
- [ ] **TASK-308: Panel admin de model_policies (API+UI) + integración canal real** — CRUD con audit, smoke en staging con failover manual. Depends: 302, 306.

Dependencias: 301 → 303 → 304/305 → 306/307 → 308; 302 en paralelo tras 301.
