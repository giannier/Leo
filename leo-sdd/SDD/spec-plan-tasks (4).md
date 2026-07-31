# Spec + Plan + Tasks — Integraciones y API Pública

**Feature ID:** 015 · **Módulo:** 15-integrations · **Estado:** Draft para ratificación
**Depende de:** estabilidad del MVP (001–010); eventos de 004/012/014 para webhooks

## Resumen
Abre Leo hacia afuera: webhooks salientes por eventos de negocio, API pública de solo
lectura primero (con API keys por tenant), export continuo a Google Sheets (el "CRM" real
de la PyME latina) y widget web de opt-in/chat. Objetivo: que Leo conviva con lo que el
cliente ya usa en lugar de exigirle migrar — y que agencias/partners (incluido Zentek360)
puedan construir encima.

## User Stories

### HU-01: Webhooks salientes
**Como** dueño técnico (o su agencia), **quiero** recibir eventos de Leo en mi sistema.
- Suscripciones por evento: `lead.created|updated`, `order.informal_created`,
  `receipt.approved|rejected`, `appointment.booked|canceled|no_show`,
  `conversation.needs_human`, `campaign.completed`, `contact.opted_out`.
- Entrega: POST JSON firmado (HMAC-SHA256 con secret por endpoint), reintentos con
  backoff (5 intentos / 24h), y panel de entregas con payload, respuesta y re-envío manual.
- WHEN un endpoint falla >50 entregas seguidas, THEN se auto-desactiva con alerta
  (nunca fallar en silencio, tampoco martillar un servidor caído).

### HU-02: API pública v1 (lectura)
**Como** desarrollador del tenant, **quiero** consultar datos de Leo desde mi sistema.
- Recursos GET: contacts, conversations (+messages), leads, appointments, products,
  metrics (resumen); paginación por cursor; filtros básicos por fecha/estado.
- Auth: API keys por tenant (crear/rotar/revocar desde el panel; scopes de lectura por
  recurso; hash en BD, se muestra una sola vez).
- Rate limit por key (default 120 req/min) con headers estándar (`X-RateLimit-*`).
- Escritura mínima y segura en v1: `POST /public/v1/contacts/{id}/tags` y
  `POST /public/v1/products` (alta/actualización de catálogo desde sistemas del cliente
  — el caso real más pedido: sincronizar precios desde su sistema). Nada más de
  escritura hasta v2.

### HU-03: Export continuo a Google Sheets
**Como** dueño no técnico, **quiero** mis leads y ventas en una hoja de cálculo viva.
- Conectar Google (OAuth, reusa patrón de F014) y elegir qué sincronizar: leads,
  citas, ventas (comprobantes aprobados), contactos con opt-in.
- Sincronización por append cada 15 min (una fila por evento; sin ediciones destructivas
  de filas existentes — la hoja es del cliente, Leo solo agrega).
- WHEN la hoja se borra o el token vence, THEN alerta y pausa con instrucciones.

### HU-04: Widget web de opt-in + chat
**Como** dueño con página web, **quiero** captar contactos desde mi web hacia WhatsApp.
- Snippet embebible (JS) con botón "Escríbenos por WhatsApp": abre wa.me del número del
  tenant con mensaje prellenado + registra la fuente; variante formulario (nombre +
  teléfono + consentimiento explícito) que crea contacto con opt-in `form` (cierra el
  pendiente de F011) y dispara plantilla de bienvenida si está fuera de ventana.
- Configurable: colores, texto, posición; sin cookies de tracking de terceros.

### HU-05: Directorio de recetas (documentación)
- Página de docs públicas de la API (OpenAPI publicado + guías: "conecta tu Google Sheet",
  "recibe leads en tu sistema", "sincroniza tu catálogo") — la documentación ES parte de
  la feature; sin ella la API no existe para el usuario.

## Requerimientos Funcionales
1. [RF-001] Bus de eventos interno formalizado: todo evento de dominio (ya emitidos en F004/007/011/012/014 vía Redis) pasa por `domain_events` (tabla append-only) — fuente única para webhooks, Sheets y auditoría; los publishers existentes migran a escribir aquí (outbox pattern) y el pub/sub Redis se deriva de la tabla.
2. [RF-002] Entrega de webhooks desde `domain_events` con offset por suscripción (at-least-once documentado; consumidores deben ser idempotentes — se explica en docs).
3. [RF-003] API keys: prefijo visible + hash argon2; scopes; última actividad; revocación inmediata.
4. [RF-004] API pública versionada bajo `/public/v1` con OpenAPI propio (separado del interno) y compatibilidad garantizada dentro de v1.
5. [RF-005] Rate limiting por key en Redis; 429 con `Retry-After`.
6. [RF-006] Sheets: `SheetsProvider` (adaptador; reusa OAuth Google) con mapeos declarativos evento→fila.
7. [RF-007] Widget servido desde CDN propio (Caddy) con versionado (`widget.v1.js`); el formulario postea a endpoint público con anti-spam (honeypot + rate por IP + verificación simple).
8. [RF-008] Todo lo expuesto respeta aislamiento (la key encapsula tenant), roles (solo owner/tenant_admin gestionan integraciones) y opt-out (contacts exportados marcan su estado).

## Decisiones y Edge Cases (Clarify integrado)
- **C-001 PII en webhooks/Sheets:** los payloads incluyen teléfono y nombre del contacto (necesarios para el caso de uso), bajo responsabilidad del tenant como controlador de datos (términos actualizados); NUNCA incluyen contenido completo de conversaciones por defecto (opt-in explícito por suscripción con advertencia).
- **C-002 Orden de eventos:** no garantizado entre tipos; garantizado por entidad (secuencia por lead/cita) — documentado.
- **C-003 Replay:** el panel permite re-enviar un evento puntual o un rango de fechas a un endpoint (máx 7 días hacia atrás).
- **C-004 Sheets alcanza límites de Google (10M celdas):** rotación automática a hoja nueva "Leo — leads (2)" con aviso.
- **C-005 Abuso del widget (spam de formularios):** además del anti-spam, los contactos creados por form entran como `unverified` y la plantilla de bienvenida se envía solo tras validar formato E.164 y no-duplicado; picos anómalos alertan al tenant.
- **C-006 API keys filtradas:** rotación en un clic + registro de IPs recientes por key; runbook de respuesta.

## Fuera de Scope
- Escritura amplia en API (v2), OAuth de terceros para apps (marketplace), Zapier/Make oficial (los webhooks ya lo permiten manualmente; app oficial cuando haya demanda), import continuo desde Sheets (solo export).

## Plan técnico

### Constitution Check ✓
✅ Adaptador `SheetsProvider`; Google solo dentro de él. ✅ Outbox = nunca perder eventos (nunca fallar en silencio). ✅ Credenciales (keys, tokens, secrets) cifradas/hasheadas. ✅ Monolito intacto: entregadores son workers ARQ.

### Arquitectura
```
Dominio ─► domain_events (outbox, append-only) ─► dispatcher ARQ:
   ├─ WebhookDeliverer (por suscripción: filtro, firma HMAC, backoff, auto-off)
   ├─ SheetsSync (cada 15 min: eventos nuevos → filas por mapeo)
   └─ (futuro) otros consumidores
/public/v1 (router separado, auth por API key, OpenAPI público)
Widget: estático versionado + POST /public/widget/leads (anti-spam)
Docs: sitio estático (Next.js route) con OpenAPI render + recetas
```

### Decisiones Técnicas
| Decisión | Elegida | Razón |
|---|---|---|
| Outbox en Postgres | Tabla + dispatcher con offsets | Durabilidad y replay sin Kafka (equipo de 1) |
| Firma de webhooks | HMAC-SHA256 header `X-Leo-Signature` + timestamp (ventana 5 min) | Estándar de industria, anti-replay |
| API pública separada del panel | Router y OpenAPI propios | Evolucionan a ritmos distintos; el interno puede romper, el público no |
| Sheets append-only | Solo agregar filas | La hoja es del cliente; jamás pisar sus ediciones |
| Rate limit | Token bucket en Redis por key | Ya hay Redis; simple |

### Data Model (migraciones)
```sql
CREATE TABLE domain_events (
  id BIGSERIAL PRIMARY KEY,
  tenant_id UUID NOT NULL,
  type TEXT NOT NULL,                       -- 'lead.created', 'receipt.approved', ...
  entity TEXT NOT NULL, entity_id UUID NOT NULL,
  payload JSONB NOT NULL,
  created_at TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX idx_devents_tenant_type ON domain_events(tenant_id, type, id);

CREATE TABLE webhook_endpoints (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id UUID NOT NULL REFERENCES tenants(id),
  url TEXT NOT NULL, secret_enc BYTEA NOT NULL,
  events TEXT[] NOT NULL, include_message_content BOOLEAN NOT NULL DEFAULT FALSE, -- C-001
  is_active BOOLEAN NOT NULL DEFAULT TRUE, disabled_reason TEXT,
  consecutive_failures INT NOT NULL DEFAULT 0,
  created_at TIMESTAMPTZ DEFAULT NOW(), updated_at TIMESTAMPTZ DEFAULT NOW()
);
CREATE TABLE webhook_deliveries (
  id BIGSERIAL PRIMARY KEY,
  endpoint_id UUID NOT NULL REFERENCES webhook_endpoints(id) ON DELETE CASCADE,
  event_id BIGINT NOT NULL REFERENCES domain_events(id),
  tenant_id UUID NOT NULL,
  status TEXT NOT NULL CHECK (status IN ('pending','delivered','failed','dead')),
  attempts INT NOT NULL DEFAULT 0, last_code INT, last_response TEXT,
  next_retry_at TIMESTAMPTZ, delivered_at TIMESTAMPTZ, created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE api_keys (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id UUID NOT NULL REFERENCES tenants(id),
  prefix TEXT NOT NULL UNIQUE,              -- 'leo_pk_ab12...'
  key_hash TEXT NOT NULL,
  scopes TEXT[] NOT NULL,
  last_used_at TIMESTAMPTZ, last_ips INET[],
  revoked_at TIMESTAMPTZ, created_by UUID REFERENCES users(id),
  created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE sheets_syncs (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id UUID NOT NULL REFERENCES tenants(id),
  google_tokens_enc BYTEA NOT NULL, spreadsheet_id TEXT NOT NULL,
  datasets TEXT[] NOT NULL,                 -- ['leads','sales','appointments','contacts']
  last_event_id BIGINT NOT NULL DEFAULT 0,  -- offset sobre domain_events
  status TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active','paused','error')),
  error_msg TEXT, updated_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE widget_configs (
  tenant_id UUID PRIMARY KEY REFERENCES tenants(id),
  enabled BOOLEAN NOT NULL DEFAULT FALSE, mode TEXT NOT NULL DEFAULT 'button'
    CHECK (mode IN ('button','form')),
  theme JSONB NOT NULL DEFAULT '{}', welcome_template_id UUID REFERENCES templates(id),
  allowed_domains TEXT[] NOT NULL DEFAULT '{}',
  updated_at TIMESTAMPTZ DEFAULT NOW()
);
ALTER TABLE contacts ADD COLUMN verification TEXT NOT NULL DEFAULT 'verified'
  CHECK (verification IN ('verified','unverified'));  -- C-005 (whatsapp = verified)
```

### API
- Tenant: `GET/POST/PUT/DELETE /tenant/integrations/webhooks` · `GET .../webhooks/:id/deliveries` · `POST .../deliveries/:id/redeliver` · `POST .../webhooks/:id/replay {from,to}`.
- Keys: `GET/POST /tenant/integrations/api-keys` · `POST .../:id/revoke|rotate`.
- Sheets: `POST /tenant/integrations/sheets/connect` (OAuth) · `GET/PUT/DELETE /tenant/integrations/sheets`.
- Widget: `GET/PUT /tenant/integrations/widget` · público: `GET /widget/v1.js` · `POST /public/widget/leads`.
- Pública: `GET /public/v1/{contacts|conversations|conversations/:id/messages|leads|appointments|products|metrics/summary}` · `POST /public/v1/contacts/:id/tags` · `POST /public/v1/products`.

### Testing
Unit: firma+ventana anti-replay, backoff y auto-desactivación, scopes de keys, mapeos de Sheets, anti-spam del widget. Integration: outbox→entrega→reintento→dead con servidor mock; replay de rango; migración de publishers existentes a outbox sin perder eventos (test de paridad); API pública con key revocada/scope insuficiente; Sheets append + rotación C-004. Contract: `SheetsProvider`. Docs: CI valida que el OpenAPI público compile y los ejemplos de las recetas ejecuten contra staging.

### Riesgos
| Riesgo | Mitigación |
|---|---|
| Migración a outbox rompe consumidores internos (F007 WS, etc.) | Doble escritura temporal + test de paridad antes de apagar el camino viejo |
| Webhooks hacia servidores lentos atascan workers | Timeout 10s, pool dedicado, auto-off |
| Fuga de datos por API key expuesta del cliente | Scopes mínimos, rotación fácil, IPs registradas, runbook C-006 |

### Estimación
2 semanas. Complejidad: Media-Alta.

## Tasks
Commits: `feat(integrations): ... [TASK-15XX]`
- [ ] **TASK-1501: Outbox domain_events + migración de publishers (doble escritura + paridad).**
- [ ] **TASK-1502: Webhooks salientes (endpoints, firma, entregas, backoff, auto-off, panel).** Depends: 1501.
- [ ] **TASK-1503: API keys + router /public/v1 (lectura) + rate limit + OpenAPI público.** Depends: 1501.
- [ ] **TASK-1504: Escrituras mínimas públicas (tags, products) con validaciones fuertes.** Depends: 1503.
- [ ] **TASK-1505: SheetsProvider + sync append + rotación + panel.** Depends: 1501. Dependencia SDK Google — reusar aprobación F014.
- [ ] **TASK-1506: Widget (JS versionado + endpoint público anti-spam + bienvenida con plantilla) + opt-in `form` (cierra F011).** Depends: F011.
- [ ] **TASK-1507: Replay/redeliver + docs públicas con recetas + CI de ejemplos.** Depends: 1502, 1503.
- [ ] **TASK-1508: Endurecimiento + apagado del camino viejo de eventos.** Milestone humano: Giannier conecta un webhook a un flujo n8n propio (dogfooding perfecto) y un Sheet real de un cliente piloto.

Dependencias: 1501 → 1502/1503/1505 → 1504/1507 → 1508; 1506 independiente tras F011.
