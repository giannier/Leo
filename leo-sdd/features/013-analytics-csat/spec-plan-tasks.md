# Spec + Plan + Tasks — Analytics Avanzado + CSAT

**Feature ID:** 013 · **Módulo:** 13-analytics · **Estado:** Draft para ratificación
**Depende de:** 010 (metrics_daily, rollup), 011 (plantillas para encuestas), 012 (leads)

## Resumen
Extiende el dashboard básico (F010) a analítica que responde preguntas de negocio:
¿cuánto vende el agente?, ¿dónde se caen los clientes?, ¿están satisfechos?, ¿qué campaña
funcionó? Incluye CSAT del cliente final por WhatsApp y NPS del tenant hacia Leo (insumo
directo para reducir churn del propio SaaS).

## User Stories

### HU-01: Embudo de conversión
- Embudo por período: conversaciones → con intención (lead creado) → por_pagar → ganado,
  con tasas entre pasos y comparación vs período anterior.
- Segmentable por: campaña de origen, producto de interés, horario, día de semana.

### HU-02: Ventas y productos
- Ventas atribuidas (criterio honesto de F010 C-001) por producto, categoría, franja
  horaria; ticket promedio; productos consultados que nunca se venden (oportunidad de
  precio/stock) y agotados más pedidos (oportunidad de reposición).

### HU-03: CSAT del cliente final
- WHEN una conversación se cierra como `ganado` o la cierra un operador, THEN (si el
  tenant lo activó) se envía encuesta CSAT: dentro de ventana 24h como mensaje del agente;
  fuera, con plantilla utility aprobada. Formato: "Del 1 al 5, ¿qué tal la atención?" +
  comentario opcional.
- Topes: máx 1 encuesta por contacto cada 30 días; jamás a opt-out; respuesta se registra
  y agradece; no-respuesta en 48h = silencio.
- Panel: CSAT promedio, distribución, comentarios recientes, CSAT por operador vs agente
  (¿el bot atiende mejor o peor que los humanos? — dato de oro para el dueño).

### HU-04: NPS del tenant hacia Leo (plataforma)
- WHEN un tenant cumple 30 días activo (y luego cada 90), THEN el panel muestra una vez
  el widget NPS (0–10 + comentario); resultados solo en panel admin de Giannier con
  segmentación por plan/rubro/antigüedad. Sin popups insistentes: se puede descartar y
  no reaparece hasta el siguiente ciclo.

### HU-05: Reportes y comparativas
- Reporte mensual automático por email al dueño (resumen: conversaciones, ventas, CSAT,
  top productos) con link al panel — el email que justifica la renovación.
- A/B simple de campañas (F011): dividir un segmento en A/B con dos plantillas y comparar
  respuesta/ventas (sin significancia estadística formal en v1; se muestra n y tasas).

## Requerimientos Funcionales
1. [RF-001] Embudo calculado desde lead_events + metrics_daily (sin tablas nuevas de hechos; el rollup de F010 se extiende con contadores de embudo).
2. [RF-002] Encuestas CSAT como flujo del sistema (no del agente conversacional): envío, captura de respuesta 1–5 (parser tolerante: "5", "cinco", "⭐⭐⭐⭐⭐"), timeout 48h.
3. [RF-003] `csat_responses` ligado a conversación, operador de takeover (si hubo) y lead.
4. [RF-004] NPS: `nps_responses` por tenant/ciclo; widget con estado descartable.
5. [RF-005] Reporte mensual: job el día 1, idempotente, con opt-out del email.
6. [RF-006] A/B: campaña con `variant_of` y split aleatorio estable por contacto.
7. [RF-007] Todos los agregados respetan aislamiento y roles (operator sin dinero ni NPS).

## Decisiones y Edge Cases (Clarify integrado)
- **C-001 Respuesta CSAT ambigua** ("más o menos", emoji raro): se guarda el texto sin score; no se insiste pidiendo número (la encuesta no puede volverse molestia).
- **C-002 Cliente responde la encuesta con otra consulta:** el mensaje entra al pipeline normal del agente además de cerrarse la encuesta sin score — nunca dejar una consulta real sin atender por estar "en modo encuesta".
- **C-003 CSAT por operador:** visible solo para owner/tenant_admin (no comparar operadores públicamente entre sí); se presenta como promedio con n mínimo de 5 para evitar juicios con 1 dato.
- **C-004 A/B con segmentos chicos:** si n por variante < 30, el reporte muestra advertencia de muestra insuficiente.
- **C-005 Retención:** csat/nps crudos 24 meses (igual que metrics_daily).

## Fuera de Scope
- Análisis de sentimiento automático de todas las conversaciones (costo/beneficio dudoso en v1; los comentarios CSAT ya dan señal), dashboards custom por tenant, BI export (más allá de CSV).

## Plan técnico

### Constitution Check ✓
✅ Compliance: encuestas respetan ventana/plantillas/opt-out/topes. ✅ Sin fallar en silencio (advertencias de muestra). ✅ Reusa rollup y colas existentes.

### Arquitectura
```
CsatFlow (sistema): trigger (ganado / cierre operador) ─► elegibilidad (topes, opt-in ventana)
  ─► envío (agente-mensaje o plantilla) ─► CsatListener en pipeline inbound (F002):
  si conversación en estado awaiting_csat y llega mensaje ─► parse score ─► registrar
  ─► agradecer ─► SIEMPRE pasar el mensaje también al flujo normal si contiene consulta (C-002)
NpsWidget (panel) ─► nps_responses
metrics_rollup (F010) extendido: contadores de embudo, csat_avg, ventas por producto
MonthlyReportJob ─► render HTML email + link
A/B: CampaignService (F011) gana split determinista (hash(contact_id) % 2)
```

### Data Model (migraciones)
```sql
CREATE TABLE csat_surveys (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id UUID NOT NULL REFERENCES tenants(id),
  conversation_id UUID NOT NULL REFERENCES conversations(id),
  contact_id UUID NOT NULL REFERENCES contacts(id),
  lead_id UUID REFERENCES leads(id),
  operator_id UUID REFERENCES users(id),        -- si hubo takeover
  status TEXT NOT NULL DEFAULT 'sent' CHECK (status IN ('sent','answered','expired')),
  score INT CHECK (score BETWEEN 1 AND 5), comment TEXT,
  sent_at TIMESTAMPTZ DEFAULT NOW(), answered_at TIMESTAMPTZ
);
CREATE INDEX idx_csat_tenant ON csat_surveys(tenant_id, sent_at);
ALTER TABLE conversations ADD COLUMN awaiting_csat BOOLEAN NOT NULL DEFAULT FALSE;

CREATE TABLE nps_responses (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id UUID NOT NULL REFERENCES tenants(id),
  user_id UUID NOT NULL REFERENCES users(id),
  cycle DATE NOT NULL,                          -- primer día del ciclo
  score INT CHECK (score BETWEEN 0 AND 10), comment TEXT,
  dismissed BOOLEAN NOT NULL DEFAULT FALSE,
  created_at TIMESTAMPTZ DEFAULT NOW(),
  UNIQUE (tenant_id, user_id, cycle)
);

ALTER TABLE campaigns ADD COLUMN variant_of UUID REFERENCES campaigns(id),
  ADD COLUMN variant_label TEXT CHECK (variant_label IN ('A','B'));
-- metrics_daily: nuevas columnas de embudo/csat en migración de F013.
```

### API
- `GET /tenant/analytics/funnel?from&to&segment=` · `GET /tenant/analytics/products` · `GET /tenant/analytics/csat` · `GET/PUT /tenant/csat-settings` (activar, plantilla, mensaje).
- `POST /tenant/nps` (responder/descartar widget) · `GET /platform-admin/nps`.
- `GET /tenant/reports/monthly/:yyyymm` (HTML del email) · `PUT /tenant/report-settings`.
- F011 extendida: `POST /tenant/campaigns/:id/ab-variant`.

### Testing
Unit: parser de scores (fixtures reales: números, palabras, emojis, ruido), elegibilidad con topes, split A/B determinista, ventanas del NPS. Integration: flujo CSAT completo dentro y fuera de ventana; C-002 (respuesta con consulta se atiende); rollup extendido reconcilia contra conteos directos. Snapshot del email mensual.

### Riesgos
| Riesgo | Mitigación |
|---|---|
| Encuestas percibidas como spam | Topes duros, un solo intento, opt-out respetado, default OFF |
| Métricas de embudo inconsistentes con kanban | Ambas desde lead_events (misma fuente); test de reconciliación |
| Parser de score falla con jerga local | Dataset de fixtures peruanas real; ambiguo = sin score (C-001) |

### Estimación
1.5 semanas. Complejidad: Media.

## Tasks
Commits: `feat(analytics): ... [TASK-13XX]`
- [ ] **TASK-1301: Migraciones + extensión de metrics_rollup (embudo/csat/productos) + reconciliación.**
- [ ] **TASK-1302: CsatFlow + parser + listener + settings + topes.** Depends: 1301, F011 plantillas.
- [ ] **TASK-1303: NPS widget + panel admin de NPS.** Depends: 1301.
- [ ] **TASK-1304: APIs y UI de analytics (embudo, productos, CSAT con vista por operador C-003).** Depends: 1301, 1302.
- [ ] **TASK-1305: Reporte mensual por email.** Depends: 1304.
- [ ] **TASK-1306: A/B de campañas (split + reporte comparado con advertencia de muestra).** Depends: F011, 1301. Milestone humano: revisar con el tenant piloto que el CSAT no genere quejas y que el reporte mensual "se entienda en 30 segundos".

Dependencias: 1301 → 1302/1303 → 1304 → 1305; 1301 → 1306.
