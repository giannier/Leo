# Spec + Plan + Tasks — Campañas, Plantillas y Follow-ups

**Feature ID:** 011 · **Módulo:** 11-campaigns · **Estado:** Ratificada y construida (ver `clarifications.md`, C-101..C-110)
**Depende de:** 002 (canal, contactos, opt-out), 007 (bandeja), 009 (límites por plan)

## Resumen
Habilita la mensajería proactiva de Leo cumpliendo las reglas de Meta al pie de la letra:
plantillas aprobadas, broadcasts segmentados solo a contactos con opt-in, y follow-ups
automáticos del agente a conversaciones frías. Es la feature que convierte a Leo de
"respondedor" a "vendedor que persigue la venta" — y también la más peligrosa para los
números de los clientes si se hace mal. El principio rector: **compliance primero,
alcance después**; límites conservadores por defecto.

## User Stories

### HU-01: Gestión de plantillas
**Como** dueño, **quiero** crear y aprobar plantillas de WhatsApp desde el panel.
- Editor con variables (`{{1}}`, `{{2}}`), categorías de Meta (utility/marketing), preview.
- WHEN envío a aprobación, THEN la plantilla viaja vía `WhatsAppProvider.submit_template`
  y su estado se sincroniza (pending → approved | rejected con motivo de Meta).
- Biblioteca de plantillas prediseñadas por rubro (recordatorio de pedido, seguimiento de
  cotización, aviso de promoción) que el tenant clona y adapta.
- WHEN una plantilla es rechazada, THEN veo el motivo y sugerencias de corrección.

### HU-02: Opt-in verificable
**Como** plataforma, **quiero** probar que cada destinatario aceptó recibir mensajes.
- Registro de opt-in por contacto con fuente y evidencia: `conversation` (escribió primero
  y aceptó seguir recibiendo info — el agente lo pregunta con tool `registrar_optin` cuando
  el contexto lo amerita), `import` (el dueño declara consentimiento previo al importar
  lista, con checkbox de responsabilidad legal + texto de la declaración guardado), `form`
  (futuro widget web).
- WHEN un contacto responde STOP/BAJA o toca el botón de baja, THEN opt-out inmediato
  global (ya existe en F002) y exclusión automática de toda campaña activa.

### HU-03: Broadcasts segmentados
**Como** dueño, **quiero** enviar una promoción a un segmento de mis contactos.
- Segmentos por: etiquetas manuales, actividad (compró/consultó producto X — datos de
  F004/F012), última interacción, opt-in requerido siempre (no desactivable).
- WHEN programo una campaña, THEN veo estimación de costo (tarifa Meta por categoría,
  pass-through) y el sistema la envía en lotes ajustados al tier del número.
- WHEN el quality rating del número está en riesgo (YELLOW/RED), THEN las campañas se
  pausan automáticamente con alerta (protección no desactivable).
- Resultados por campaña: enviados, entregados, leídos, respuestas, bajas, ventas
  atribuidas (respuesta que termina en pedido dentro de 72h).
- WHEN un destinatario responde, THEN la conversación entra al flujo normal
  (agente/bandeja) marcada con la campaña de origen.

### HU-04: Follow-ups automáticos del agente
**Como** dueño, **quiero** que el agente retome leads que dejaron de responder.
- Regla por tenant (default OFF): si una conversación con intención de compra detectada
  queda sin respuesta del cliente N horas (default 24h), enviar 1 follow-up; máximo 1 por
  conversación y 2 por contacto/mes (topes duros de plataforma).
- Dentro de ventana 24h: mensaje libre generado por el agente (retoma el contexto). Fuera
  de ventana: solo con plantilla utility aprobada elegida por el tenant.
- WHEN el contacto no responde al follow-up, THEN silencio (nunca insistir).

### HU-05: Límites por plan
- Campañas/mes y destinatarios/campaña según plan (tabla `plans`, extendida): Emprende
  2 campañas × 500 dest., Crece 8 × 2 000, Pro 20 × 10 000 (referencia, ratificar).
  Follow-ups incluidos en todos los planes (consumen cuota de mensajes IA normal).

## Requerimientos Funcionales
1. [RF-001] `WhatsAppProvider` extendido: `submit_template`, `send_template(to, template, vars)`, sincronización de estados de plantilla (webhook o polling según YCloud).
2. [RF-002] `contact_optins` con fuente + evidencia + timestamp; broadcasts filtran por opt-in vigente y opt-out en tiempo de envío (no al crear la campaña).
3. [RF-003] Motor de envío por lotes: rate según tier del número, reintentos, pausa/reanudación, cancelación de lo no enviado.
4. [RF-004] Idempotencia por destinatario-campaña (un contacto jamás recibe dos veces la misma campaña).
5. [RF-005] Etiquetas de contactos (CRUD) como base de segmentación (F012 las reusa).
6. [RF-006] Detección de "intención de compra" para follow-ups: señal del agente (tool interna al cerrar cada respuesta: `interes: alto|medio|bajo`) persistida en la conversación.
7. [RF-007] Atribución de ventas a campaña: ventana 72h, pedido/comprobante aprobado (métrica honesta, alineada a F010 C-001).
8. [RF-008] Costos de plantillas Meta visibles antes de confirmar (tabla de tarifas por categoría/país mantenida por admin, `meta_rates`).
9. [RF-009] Todo envío proactivo queda excluido para contactos opt-out, sin excepciones ni override manual.

## Decisiones y Edge Cases (Clarify integrado)
- **C-001 Import de listas frías:** PROHIBIDO por producto. El import exige declaración de consentimiento previo y aún así arranca con límite bajo (máx 200 contactos importados con opt-in declarado por semana) — proteger el número vale más que el volumen. Meta prohíbe contacto en frío; Leo no lo facilita.
- **C-002 Campaña en curso cuando cae el quality:** se pausa el lote pendiente; lo enviado queda; el tenant decide reanudar solo cuando el rating se recupere (con aviso de riesgo).
- **C-003 Zona horaria de envío:** campañas se programan en la TZ del tenant; ventana de envío por defecto 9:00–20:00 local (configurable dentro de 8:00–21:00; nunca madrugada).
- **C-004 Variables sin valor para un contacto:** el destinatario se omite del lote con motivo en el reporte (no enviar plantillas rotas).
- **C-005 Follow-up vs takeover:** conversaciones en takeover o needs_human quedan excluidas de follow-ups automáticos (el humano manda).
- **C-006 Plantillas con media:** soportadas (header imagen/PDF desde assets del tenant) desde v1 de esta feature — el mercado las usa intensivamente.

## Fuera de Scope
- Widget de opt-in web (form) — F015; A/B testing de campañas — F013; secuencias multi-paso (drips) — futuro, requiere madurez de compliance.

## Plan técnico

### Constitution Check ✓
✅ Compliance WhatsApp como requisito (opt-in/opt-out/quality/ventanas — todo forzado por sistema). ✅ Proveedor solo vía adaptador. ✅ Nunca fallar en silencio (pausas con alerta, reportes de omitidos). ✅ Límites en `plans`, no en código.

### Arquitectura
```
CampaignService ─► segmentación (SQL sobre contacts+tags+optins+actividad)
   └► campaign_recipients (snapshot al lanzar) ─► BatchSender (ARQ, rate por tier,
        re-chequea opt-out por destinatario al enviar) ─► WhatsAppProvider.send_template
Webhooks de estado (F002) actualizan campaign_recipients.status
QualityGuard: listener del rating (F002) ─► pausa campañas activas del número
FollowUpEngine (job cada 30 min): conversaciones con interes=alto, sin respuesta N h,
   sin takeover, dentro de topes ─► en ventana: agente; fuera: plantilla configurada
TemplateService: CRUD + submit + sync de estados + biblioteca por rubro (seeds)
```

### Data Model (migraciones)
```sql
CREATE TABLE templates (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id UUID NOT NULL REFERENCES tenants(id),
  name TEXT NOT NULL, category TEXT NOT NULL CHECK (category IN ('utility','marketing')),
  language TEXT NOT NULL DEFAULT 'es', body TEXT NOT NULL, header_media_key TEXT,
  variables INT NOT NULL DEFAULT 0,
  status TEXT NOT NULL DEFAULT 'draft'
    CHECK (status IN ('draft','pending','approved','rejected','paused_by_meta')),
  provider_template_id TEXT, rejection_reason TEXT,
  created_at TIMESTAMPTZ DEFAULT NOW(), updated_at TIMESTAMPTZ DEFAULT NOW(),
  UNIQUE (tenant_id, name, language)
);

CREATE TABLE contact_tags (
  tenant_id UUID NOT NULL REFERENCES tenants(id),
  contact_id UUID NOT NULL REFERENCES contacts(id) ON DELETE CASCADE,
  tag TEXT NOT NULL, created_at TIMESTAMPTZ DEFAULT NOW(),
  PRIMARY KEY (tenant_id, contact_id, tag)
);

CREATE TABLE contact_optins (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id UUID NOT NULL REFERENCES tenants(id),
  contact_id UUID NOT NULL REFERENCES contacts(id) ON DELETE CASCADE,
  source TEXT NOT NULL CHECK (source IN ('conversation','import','form')),
  evidence JSONB NOT NULL,               -- message_id | declaración+usuario | form ref
  created_at TIMESTAMPTZ DEFAULT NOW(),
  UNIQUE (tenant_id, contact_id)
);

CREATE TABLE campaigns (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id UUID NOT NULL REFERENCES tenants(id),
  name TEXT NOT NULL, template_id UUID NOT NULL REFERENCES templates(id),
  segment JSONB NOT NULL,                -- definición declarativa del filtro
  scheduled_at TIMESTAMPTZ, send_window JSONB NOT NULL DEFAULT '{"from":"09:00","to":"20:00"}',
  status TEXT NOT NULL DEFAULT 'draft'
    CHECK (status IN ('draft','scheduled','sending','paused','completed','canceled')),
  estimated_cost_usd NUMERIC(10,4), created_by UUID NOT NULL REFERENCES users(id),
  created_at TIMESTAMPTZ DEFAULT NOW(), updated_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE campaign_recipients (
  campaign_id UUID NOT NULL REFERENCES campaigns(id) ON DELETE CASCADE,
  contact_id UUID NOT NULL REFERENCES contacts(id),
  tenant_id UUID NOT NULL,
  status TEXT NOT NULL DEFAULT 'pending'
    CHECK (status IN ('pending','sent','delivered','read','replied','failed','skipped')),
  skip_reason TEXT, message_id UUID REFERENCES messages(id),
  attributed_sale BOOLEAN NOT NULL DEFAULT FALSE,
  PRIMARY KEY (campaign_id, contact_id)
);

CREATE TABLE followup_rules (
  tenant_id UUID PRIMARY KEY REFERENCES tenants(id),
  enabled BOOLEAN NOT NULL DEFAULT FALSE,
  hours_idle INT NOT NULL DEFAULT 24 CHECK (hours_idle BETWEEN 4 AND 72),
  out_of_window_template_id UUID REFERENCES templates(id),
  updated_at TIMESTAMPTZ DEFAULT NOW()
);
ALTER TABLE conversations ADD COLUMN purchase_interest TEXT
  CHECK (purchase_interest IN ('alto','medio','bajo')),
  ADD COLUMN followups_sent INT NOT NULL DEFAULT 0,
  ADD COLUMN origin_campaign_id UUID REFERENCES campaigns(id);

CREATE TABLE meta_rates (  -- tarifas Meta por país/categoría (admin)
  country TEXT NOT NULL, category TEXT NOT NULL, rate_usd NUMERIC(8,5) NOT NULL,
  valid_from DATE NOT NULL, PRIMARY KEY (country, category, valid_from)
);
ALTER TABLE plans ADD COLUMN max_campaigns_month INT NOT NULL DEFAULT 0,
  ADD COLUMN max_recipients_per_campaign INT NOT NULL DEFAULT 0;
```

### API
- Plantillas: `GET/POST /tenant/templates` · `POST /tenant/templates/:id/submit` · `GET /tenant/templates/library?industry=` · `POST /tenant/templates/library/:id/clone`.
- Etiquetas/opt-in: `POST/DELETE /tenant/contacts/:id/tags` · `POST /tenant/contacts/import-optin` (CSV + declaración) · `GET /tenant/contacts/:id/optin`.
- Campañas: `POST /tenant/campaigns` (con `estimate: true` para previsualizar costo/alcance) · `POST .../:id/schedule|pause|resume|cancel` · `GET .../:id/report`.
- Follow-ups: `GET/PUT /tenant/followup-rules`.
- Admin: `GET/PUT /platform-admin/meta-rates`.

### Testing
Unit: segmentación declarativa, ventana de envío con TZ, topes de follow-up, omisiones (variables faltantes, opt-out). Integration: campaña completa con provider mock (lotes, pausa por quality, idempotencia destinatario); follow-up dentro/fuera de ventana; exclusión por takeover. Evals: follow-up del agente retoma contexto sin sonar insistente (dataset nuevo). Carga: campaña de 10k destinatarios en staging sin degradar el canal conversacional (prioridad de colas: conversación > campaña).

### Riesgos
| Riesgo | Mitigación |
|---|---|
| Daño al quality rating de números de clientes | QualityGuard automático, topes duros, ventanas horarias, import restringido (C-001) |
| Campañas saturan la cola y degradan respuestas en vivo | Colas separadas con prioridad; BatchSender con rate propio |
| Cambios de tarifas/reglas de Meta | meta_rates versionada por fecha; sync de estados de plantilla tolerante |

### Estimación
2 semanas. Complejidad: Alta (compliance + envío masivo).

## Tasks
Commits: `feat(campaigns): ... [TASK-11XX]`
- [x] **TASK-1101: Migraciones + tarifas Meta + límites en plans.**
- [x] **TASK-1102: Provider de plantillas** (submit/send_template/sync estados) + contract tests. Depends: 1101.
- [x] **TASK-1103: TemplateService + biblioteca por rubro + UI de plantillas.** Depends: 1102.
- [x] **TASK-1104: Opt-in/etiquetas** (~~tool registrar_optin del agente~~ → botón tras venta confirmada, C-102; import con declaración y límite semanal, UI). Depends: 1101.
- [x] **TASK-1105: CampaignService + BatchSender** (snapshot, lotes por tier, re-chequeo opt-out, idempotencia, pausa/reanudar, prioridad de colas). Depends: 1102, 1104.
- [x] **TASK-1106: QualityGuard + estimación de costos + reportes de campaña.** Depends: 1105.
- [x] **TASK-1107: FollowUpEngine** (~~señal de interés en agent-core~~ → etapas del proceso, C-104/C-110, sin tocar `agent/` ni evals; topes, ventana, exclusiones) + evals. Depends: 1102; toca `agent/` → evals obligatorios.
- [x] **TASK-1108: UI de campañas** (builder de segmento, programación, reporte) + atribución de ventas. Depends: 1105–1107.
- [ ] **TASK-1109: Prueba de carga + endurecimiento.** Milestone humano: primera campaña real con un tenant piloto de ≤100 contactos, supervisada por Giannier de punta a punta.

Dependencias: 1101 → 1102 → 1103/1105; 1101 → 1104 → 1105 → 1106 → 1108; 1102 → 1107 → 1108 → 1109.
