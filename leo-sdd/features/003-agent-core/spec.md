# Spec — Agent-core Completo

**Feature ID:** 003 · **Módulo:** 03-agent-core · **Estado:** Draft para ratificación
**Depende de:** 001 (v0), 002 (canal real)

## Resumen
Evoluciona el agent-core v0 a un motor de ventas maduro: memoria conversacional útil,
prompt por rubro, política de modelos por plan/tarea, guardrails reforzados, escalamiento
inteligente y evals por rubro en CI. Es el corazón del producto: aquí se decide si Leo
"vende como un buen empleado" o "responde como un bot".

## User Stories

### HU-01: Memoria conversacional
**Como** cliente final, **quiero** que el agente recuerde lo que ya le dije en la
conversación y en visitas anteriores.
- WHEN la conversación supera el contexto útil, THEN se resume incrementalmente (rolling summary) preservando: intención, productos discutidos, datos dados por el cliente (nombre, dirección de entrega), acuerdos.
- WHEN el mismo contacto vuelve días después, THEN el agente dispone de un "perfil de contacto" (resumen persistente: qué compró/preguntó antes) — sin inventar familiaridad excesiva.
- WHEN el humano tomó la conversación y la devolvió, THEN el agente conoce lo que el humano acordó (lee esos mensajes).

### HU-02: Prompt por rubro y por tenant
**Como** dueño, **quiero** que mi agente hable como mi negocio.
- Sistema de plantillas de prompt por rubro (restaurante, instituto, retail, servicios, salud, otros) + capa de personalización del tenant (tono, restricciones, reglas) + contexto dinámico (horario actual vs horarios de atención, moneda).
- WHEN es fuera de horario, THEN el agente lo dice y gestiona expectativas (configurable: responder igual / tomar pedido para mañana).

### HU-03: Política de modelos
**Como** plataforma, **quiero** controlar costo y calidad por plan y por tarea.
- Config por tenant/plan: modelo principal (económico), modelo de visión (F004), modelo de resumen (el más barato). Cambiable sin deploy (tabla de configuración).
- WHEN el proveedor primario falla (5xx/timeout), THEN failover automático al secundario configurado y alerta al admin.

### HU-04: Guardrails y escalamiento
**Como** dueño, **quiero** confiar en que el agente no me hará quedar mal.
- El agente solo afirma precios/stock/condiciones que devuelven sus tools; si la tool no lo cubre, dice que no tiene el dato y ofrece escalar.
- WHEN detecta frustración, insulto reiterado, tema fuera de dominio (legal/médico/quejas graves) o petición explícita de humano, THEN ejecuta la tool `escalar_a_humano`: marca la conversación `needs_human`, notifica a la bandeja (F007) y responde un mensaje de transición.
- WHEN el cliente final envía contenido inapropiado, THEN el agente corta cortésmente y escala; nunca discute.
- Presupuesto por respuesta: máx N iteraciones de tools (default 6) y tope de tokens; al superarlo, respuesta segura + traza marcada `budget_exceeded`.

### HU-05: Evals por rubro
**Como** operador de plataforma, **quiero** detectar regresiones antes de producción.
- Dataset de conversaciones doradas por rubro (mínimo 15 por rubro activo) con aserciones: exactitud de precios, no-invención, tono, escalamiento correcto, manejo de "no sé".
- Corren en CI ante cambios de `agent/`, prompts o política de modelos; reporte comparativo vs baseline.

## Requerimientos Funcionales
1. [RF-001] Rolling summary automático al superar umbral de tokens; el resumen es parte del contexto y se persiste en `conversations.summary`.
2. [RF-002] `contact_profiles`: resumen persistente por contacto, actualizado al cerrar conversación (job).
3. [RF-003] Prompt builder versionado: cambios de plantilla base = migración de datos + evals obligatorios (Constitution).
4. [RF-004] Registro de política de modelos en BD (`model_policies`), editable desde panel admin.
5. [RF-005] Failover de proveedor LLM con circuit breaker (abre tras 3 fallos/60s).
6. [RF-006] Tool `escalar_a_humano` disponible siempre, no desactivable por tenant.
7. [RF-007] Trazas enriquecidas: iteraciones, budget, motivo de escalamiento, versión de prompt.
8. [RF-008] Runner de evals con dataset en repo (`evals/datasets/*.yaml`) + comando local y job CI.

## Decisiones y Edge Cases (Clarify integrado)
- **C-001 Privacidad del perfil de contacto**: guarda solo datos operativos dados por el cliente (nombre, preferencias de compra, dirección si la dio para delivery); nunca inferencias sensibles. Eliminable a solicitud (Ley 29733) desde la bandeja.
- **C-002 Idiomas**: v1 responde en español; si el cliente escribe en otro idioma, el agente responde en ese idioma solo si el modelo lo maneja bien, sin prometer soporte multilingüe (config del tenant: forzar español sí/no).
- **C-003 Alucinación de tools**: si el modelo llama una tool inexistente o con args inválidos ×2, se corta el loop con respuesta segura y traza `tool_error`.
- **C-004 Mensajes simultáneos con humano activo**: si la conversación está en takeover (F007), el agente no genera nada (guard a nivel de pipeline, no de prompt).
- **C-005 Contexto máximo**: presupuesto de contexto por plan (trial/emprende más corto) — palanca de costo.

## Fuera de Scope
- Tools de media y visión (F004), campañas/follow-ups (F011), voz/llamadas (no roadmap actual).

## Criterios de Aceptación
- [ ] Evals: ≥90% de aserciones en verde por rubro activo antes de cada release.
- [ ] Conversación de 40+ mensajes mantiene coherencia (test guiado con resumen).
- [ ] Failover probado apagando el proveedor primario en staging.
- [ ] Escalamiento correcto en los 4 disparadores definidos.
- [ ] Cobertura ≥80%.
