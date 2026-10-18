# Spec + Plan + Tasks — Mini-CRM de Leads

**Feature ID:** 012 · **Módulo:** 12-crm · **Estado:** Ratificada y en construcción (2026-10-18). Adaptaciones en [clarifications.md](clarifications.md)
**Depende de:** 003 (agente), 007 (bandeja), 011 (etiquetas; follow-ups conectados)

## Resumen
Un pipeline de ventas simple donde cada conversación con intención comercial se vuelve un
lead que avanza por etapas — actualizado automáticamente por el agente y corregible por
humanos. Convierte a Leo en la fuente de verdad comercial del negocio: el dueño deja de
preguntarse "¿a quién le debo escribir?" porque el kanban se lo dice. Filosofía: CRM
mínimo y automático; nada de campos infinitos que nadie llena.

## User Stories

### HU-01: Pipeline configurable
- Etapas default: `nuevo → interesado → por_pagar → ganado | perdido`; el tenant puede
  renombrar, agregar (máx 8) o desactivar etapas intermedias; `ganado`/`perdido` son fijas.
- Motivos de pérdida configurables (default: precio, sin stock, dejó de responder, otro).

### HU-02: Creación y avance automático por el agente
- WHEN el agente detecta intención comercial (señal `purchase_interest` de F011 ≥ medio),
  THEN crea el lead en `nuevo` ligado al contacto y la conversación (tool `actualizar_lead`).
- WHEN se registra pedido informal (F004), THEN el lead avanza a `por_pagar` con monto.
- WHEN un comprobante es aprobado (F004), THEN `ganado` con monto real.
- WHEN el contacto no responde tras follow-up agotado (F011), THEN `perdido: dejó de responder`
  (automático solo para ese motivo; los demás son humanos).
- Toda transición automática queda en el historial del lead con su causa (mensaje/evento).

### HU-03: Kanban y gestión humana
- Vista kanban con drag & drop (respeta etapas fijas), tarjetas con: contacto, monto
  estimado/real, producto de interés, antigüedad en etapa, campaña de origen (F011),
  próximo follow-up programado.
- WHEN muevo una tarjeta manualmente, THEN el agente respeta la etapa humana (no la
  retrocede; solo puede avanzarla con evidencia — pedido/pago).
- Acciones desde la tarjeta: abrir conversación (deep-link a bandeja F007), asignar a
  operador, nota, marcar perdido con motivo.

### HU-04: Vistas de trabajo
- Filtros: etapa, asignado, antigüedad, monto, etiqueta, campaña de origen.
- Lista "hoy": leads con más de N días sin actividad en etapas activas (default 3) —
  la lista de llamadas/mensajes del día del dueño.

### HU-05: Métricas (alimenta F010/F013)
- Conversión por etapa, tiempo medio por etapa, valor del pipeline (suma por_pagar),
  ganados/perdidos por semana con motivos.

## Requerimientos Funcionales
1. [RF-001] Tool `actualizar_lead` en el registro (no desactivable si el objetivo del tenant incluye "vender"); presupuesto del agente sin cambios (la tool es barata).
2. [RF-002] Un lead activo por contacto (unique parcial); nuevo interés tras `ganado/perdido` crea lead nuevo enlazado al anterior.
3. [RF-003] Historial de transiciones append-only (`lead_events`) con actor (agente/usuario/sistema) y causa.
4. [RF-004] Reglas de precedencia: humano > agente (RF de HU-03); sistema solo el auto-perdido de follow-ups.
5. [RF-005] Asignación de leads a operadores con notificación (reusa push/eventos F007).
6. [RF-006] WebSocket: el kanban se actualiza en vivo (reusa canal de F007).
7. [RF-007] Export CSV del pipeline.
8. [RF-008] Al eliminar datos de un contacto (Ley 29733, F007), sus leads se anonimizan (montos y eventos quedan para métricas; datos personales fuera).

## Decisiones y Edge Cases (Clarify integrado)
- **C-001 Doble conversación del mismo contacto** (escribe, se cierra, vuelve a escribir): el lead activo se reutiliza y se liga a la nueva conversación; la tarjeta muestra la última.
- **C-002 Falsos leads** (curiosos sin intención): umbral en `interes=medio` como default; configurable a `alto` para tenants con mucho ruido; el dueño puede descartar en un clic ("no es lead" → borra sin contar como perdido).
- **C-003 Montos:** estimado = suma del pedido informal; real = comprobante aprobado; si difieren, manda el real y la diferencia queda visible en el historial.
- **C-004 Etapas personalizadas y agente:** el agente solo opera sobre las etapas semánticas fijas (nuevo/interesado/por_pagar/ganado/perdido); etapas custom intermedias son territorio humano — el mapeo se declara al crearlas (¿antes o después de qué etapa fija?).
- **C-005 Kanban con miles de tarjetas:** columnas paginadas (50 por carga) + búsqueda; el kanban es herramienta de trabajo, no archivo histórico (ganados/perdidos >30 días solo en lista/export).

## Fuera de Scope
- Multi-pipeline por tenant, previsión de ventas (forecasting), automatizaciones custom tipo Zapier interno (F015 cubre webhooks), campos personalizados ilimitados.

## Plan técnico

### Constitution Check ✓
✅ Tool nueva registrada sin tocar núcleo. ✅ Configuración sobre código (etapas en BD). ✅ Aislamiento y roles estándar. ✅ El humano manda (precedencia RF-004).

### Arquitectura
```
LeadService ─► máquina de transiciones (valida precedencia y evidencia) ─► lead_events
Tool actualizar_lead (agent) ─► LeadService (actor=agent)
Listeners de eventos existentes: pedido_informal (F004) / receipt.approved (F004) /
  followup.exhausted (F011) ─► transiciones automáticas
Kanban UI ─► REST + WS (lead.updated en canal del tenant)
```

### Decisiones Técnicas
| Decisión | Elegida | Razón |
|---|---|---|
| Etapas | Tabla `pipeline_stages` por tenant con `semantic` (fija) o `custom` + orden | Config sobre código; el agente solo ve semánticas |
| Transiciones | Servicio único con matriz de permisos por actor | Un solo lugar donde vive la regla "humano manda" |
| Eventos entre features | Redis pub/sub interno ya existente (F007) con handlers idempotentes | Sin bus nuevo |
| Kanban | dnd-kit en frontend (dependencia nueva — aprobar) | Estándar accesible para drag & drop |

### Data Model (migraciones)
```sql
CREATE TABLE pipeline_stages (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id UUID NOT NULL REFERENCES tenants(id),
  key TEXT NOT NULL,                    -- 'nuevo','interesado',... o slug custom
  label TEXT NOT NULL, position INT NOT NULL,
  semantic TEXT CHECK (semantic IN ('nuevo','interesado','por_pagar','ganado','perdido')),
  is_active BOOLEAN NOT NULL DEFAULT TRUE,
  UNIQUE (tenant_id, key)
);

CREATE TABLE leads (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id UUID NOT NULL REFERENCES tenants(id),
  contact_id UUID NOT NULL REFERENCES contacts(id),
  conversation_id UUID REFERENCES conversations(id),   -- la última ligada
  stage_id UUID NOT NULL REFERENCES pipeline_stages(id),
  assigned_to UUID REFERENCES users(id),
  amount_estimated NUMERIC(12,2), amount_real NUMERIC(12,2),
  product_interest TEXT, origin_campaign_id UUID REFERENCES campaigns(id),
  lost_reason TEXT, previous_lead_id UUID REFERENCES leads(id),
  status TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active','won','lost','discarded')),
  stage_entered_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  created_at TIMESTAMPTZ DEFAULT NOW(), updated_at TIMESTAMPTZ DEFAULT NOW()
);
CREATE UNIQUE INDEX uq_active_lead_per_contact ON leads(tenant_id, contact_id)
  WHERE status = 'active';

CREATE TABLE lead_events (
  id BIGSERIAL PRIMARY KEY,
  tenant_id UUID NOT NULL, lead_id UUID NOT NULL REFERENCES leads(id) ON DELETE CASCADE,
  actor TEXT NOT NULL CHECK (actor IN ('agent','user','system')),
  actor_user_id UUID REFERENCES users(id),
  from_stage TEXT, to_stage TEXT, cause TEXT, detail JSONB NOT NULL DEFAULT '{}',
  created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE lost_reasons (
  tenant_id UUID NOT NULL REFERENCES tenants(id),
  key TEXT NOT NULL, label TEXT NOT NULL, is_active BOOLEAN NOT NULL DEFAULT TRUE,
  PRIMARY KEY (tenant_id, key)
);
```

### API
- `GET/PUT /tenant/pipeline/stages` · `GET/PUT /tenant/pipeline/lost-reasons`.
- `GET /tenant/leads` (filtros/kanban por etapa con paginación) · `GET /tenant/leads/today`.
- `POST /tenant/leads/:id/move` `{stage_id, lost_reason?}` · `POST .../assign` · `POST .../discard` · `POST .../notes` (reusa notas F007 ligadas al lead).
- `GET /tenant/leads/export.csv` · `GET /tenant/leads/:id/history`.
- WS: evento `lead.updated`.

### Testing
Unit: matriz de transiciones completa por actor (tabla de verdad), unique de lead activo, anonimización RF-008. Integration: cadena pedido→por_pagar→pago→ganado con eventos reales de F004; auto-perdido por follow-up; precedencia humana (agente intenta retroceder → rechazado). Evals: `actualizar_lead` se usa con criterio (no crea leads de "hola, ¿horario?"). E2E: kanban drag & drop con dos sesiones.

### Riesgos
| Riesgo | Mitigación |
|---|---|
| Ruido de leads basura degrada confianza | Umbral configurable + descarte 1-clic + eval específico |
| Desincronización kanban vs eventos | WS + refetch al foco; transiciones idempotentes |
| Sobre-complejidad de etapas custom | Máx 8, semánticas fijas, mapeo obligatorio (C-004) |

### Estimación
1.5 semanas. Complejidad: Media.

## Tasks
Commits: `feat(crm): ... [TASK-12XX]`
- [x] **TASK-1201: Migraciones + seed de etapas/motivos por tenant existente.**
- [x] **TASK-1202: LeadService + matriz de transiciones + lead_events.** Depends: 1201.
- [ ] **TASK-1203: Tool actualizar_lead + listeners de F004/F011.** Depends: 1202; toca `agent/` → evals.
- [ ] **TASK-1204: API de leads/pipeline + WS + export + anonimización.** Depends: 1202.
- [ ] **TASK-1205: UI kanban (dnd-kit — aprobar dependencia) + lista "hoy" + config de etapas.** Depends: 1204.
- [ ] **TASK-1206: Métricas hacia metrics_daily (F010) + endurecimiento.** Depends: 1204. Milestone humano: una semana de uso real con tenant piloto; revisar si el umbral de creación de leads genera ruido.

Dependencias: 1201 → 1202 → 1203/1204 → 1205 → 1206.
