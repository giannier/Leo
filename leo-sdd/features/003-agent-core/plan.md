# Plan Técnico — Agent-core Completo

**Feature ID:** 003

## Constitution Check ✓
✅ Núcleo único + tools registradas; `escalar_a_humano` como tool del núcleo. ✅ LLM solo vía `LLMProvider` (failover dentro del adaptador compuesto). ✅ "El agente no inventa": guardrails por diseño (respuestas ancladas a tools). ✅ Evals obligatorios en CI para cambios de prompt (Restricciones IA).

## Arquitectura
```
AgentRunner(channel, conversation)
 ├─ ContextBuilder: system prompt (plantilla rubro + capa tenant + contexto dinámico)
 │                 + summary de conversación + perfil de contacto + últimos N mensajes
 ├─ ToolLoop: LLMProvider.chat(tools) ─► dispatch a ToolRegistry ─► máx 6 iter / budget tokens
 ├─ Guardrails: pre (takeover? tenant paused?) · post (¿respuesta sin fuente para precio? → reescritura segura)
 └─ Tracer: agent_traces enriquecidas + costo estimado
LLMProvider compuesto: Primary → CircuitBreaker → Fallback (config en model_policies)
Jobs: summarize_conversation (al cerrar / umbral), update_contact_profile
```

### Decisiones Técnicas
| Decisión | Elegida | Razón |
|---|---|---|
| Memoria larga | Rolling summary + perfil de contacto (texto), NO RAG de conversaciones | Simple, barato, suficiente; RAG conversacional es post-MVP |
| Plantillas de prompt | Jinja2 versionadas en repo + overrides del tenant en BD | Cambios de plantilla pasan por PR + evals; overrides sin deploy |
| Política de modelos | Tabla `model_policies` (scope: global/plan/tenant; task: chat/vision/summary) | Palanca de costos sin deploy |
| Circuit breaker | Estado en Redis, half-open tras 60s | Compartido entre workers |
| Evals | YAML declarativo (mensajes + aserciones) + runner pytest con LLM real económico | Legible por Giannier, versionado en git |
| Detección de frustración | Instrucción al modelo + heurísticas (mayúsculas/insultos/repetición) como refuerzo | Sin modelo aparte; barato |

## Data Model (migraciones)
```sql
ALTER TABLE conversations ADD COLUMN summary TEXT,
  ADD COLUMN needs_human BOOLEAN NOT NULL DEFAULT FALSE,
  ADD COLUMN takeover_user_id UUID REFERENCES users(id);   -- usado a fondo en F007

CREATE TABLE contact_profiles (
  contact_id UUID PRIMARY KEY REFERENCES contacts(id) ON DELETE CASCADE,
  tenant_id UUID NOT NULL REFERENCES tenants(id),
  profile TEXT NOT NULL,                 -- resumen operativo
  updated_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE model_policies (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  scope TEXT NOT NULL CHECK (scope IN ('global','plan','tenant')),
  scope_ref TEXT,                        -- plan_id o tenant_id según scope
  task TEXT NOT NULL CHECK (task IN ('chat','vision','summary','embed')),
  primary_model TEXT NOT NULL, fallback_model TEXT,
  max_context_tokens INT, max_tool_iterations INT NOT NULL DEFAULT 6,
  updated_at TIMESTAMPTZ DEFAULT NOW(),
  UNIQUE (scope, scope_ref, task)
);

CREATE TABLE prompt_versions (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  industry TEXT NOT NULL, version TEXT NOT NULL, checksum TEXT NOT NULL,
  released_at TIMESTAMPTZ DEFAULT NOW(), UNIQUE (industry, version)
);
```

## API
- `GET/PUT /platform-admin/model-policies` — gestión de política de modelos (audit log).
- `GET /tenant/agent/trace/:message_id` — traza detallada (ya expuesta en sandbox; aquí para canal real, visible según rol).
- Evals: sin API; comando `make evals` + job CI.

## Estrategia de Testing
Unit: ContextBuilder (todas las capas), presupuesto del loop, circuit breaker, heurísticas de escalamiento. Integration: conversación larga con resumen; failover con primario caído; takeover bloquea generación. Evals: 15 doradas × rubros activos (restaurante, instituto, retail al inicio) con umbral 90%.

## Riesgos
| Riesgo | Mitigación |
|---|---|
| Resumen pierde datos críticos (dirección, acuerdo) | Instrucción de resumen con campos obligatorios + eval específico |
| Evals costosos en CI | Modelo económico + caching de respuestas por checksum de prompt+dataset |
| Overrides de tenant rompen el prompt | Sanitización + longitud máxima + eval smoke por tenant al guardar overrides |

## Estimación
2 semanas. Complejidad: Alta.
