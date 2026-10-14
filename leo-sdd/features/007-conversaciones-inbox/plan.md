# Plan Técnico — Conversaciones e Inbox

**Feature ID:** 007

## Constitution Check ✓
✅ "El humano siempre puede tomar el control" — takeover instantáneo con guard a nivel de pipeline. ✅ Roles respetados. ✅ Ley 29733: eliminación de datos del contacto auditada. ✅ Monolito: WebSocket dentro del mismo FastAPI (sin servicio aparte).

## Arquitectura
```
FastAPI WebSocket /ws (JWT) ── Redis Pub/Sub (canal por tenant) ── workers publican eventos
Eventos: message.new | conversation.updated | takeover.changed | receipt.updated | presence
TakeoverService: lock atómico (UPDATE ... WHERE takeover_user_id IS NULL) + publicación
CancelGuard: al tomar, se marca conversación; el pipeline de F002 verifica el flag ANTES
de enviar la respuesta generada (descarta si cambió) — corte <1s sin matar el proceso LLM
AutoReturnJob: devuelve takeovers inactivos (aviso 5 min antes vía WS)
Frontend: TanStack Query + capa WS (invalidación selectiva), lista virtualizada, push API
```

### Decisiones Técnicas
| Decisión | Elegida | Razón |
|---|---|---|
| Tiempo real | WebSocket nativo FastAPI + Redis Pub/Sub | Sin dependencias nuevas pesadas; multi-worker OK |
| Corte de generación en curso | Check del flag justo antes del send (no cancelación del stream LLM) | Simple y suficiente: el cliente nunca ve el mensaje; el costo del token ya gastado es aceptable |
| Presencia | TTL keys en Redis (heartbeat 15s) | Efímero por naturaleza |
| Push | Web Push API (VAPID) | Sin apps nativas; funciona en Android/desktop; iOS Safari con limitaciones documentadas al tenant |
| Búsqueda | tsvector español + GIN sobre messages.content | Suficiente; sin Elasticsearch (equipo de 1) |
| Eventos del hilo | messages.author='system' + msg_type='event' + payload JSONB | Un solo hilo cronológico simple |

## Data Model (migraciones)
```sql
ALTER TABLE conversations
  ADD COLUMN closed_at TIMESTAMPTZ,
  ADD COLUMN escalation_reason TEXT,
  ADD COLUMN takeover_started_at TIMESTAMPTZ,
  ADD COLUMN unread_count INT NOT NULL DEFAULT 0;

CREATE TABLE conversation_notes (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id UUID NOT NULL REFERENCES tenants(id),
  conversation_id UUID NOT NULL REFERENCES conversations(id),
  author_id UUID NOT NULL REFERENCES users(id),
  content TEXT NOT NULL, created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE push_subscriptions (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id UUID NOT NULL REFERENCES users(id),
  tenant_id UUID NOT NULL REFERENCES tenants(id),
  endpoint TEXT NOT NULL, keys JSONB NOT NULL,
  created_at TIMESTAMPTZ DEFAULT NOW(), UNIQUE (user_id, endpoint)
);

CREATE TABLE notification_settings (
  tenant_id UUID PRIMARY KEY REFERENCES tenants(id),
  unattended_queue_minutes INT NOT NULL DEFAULT 10,
  auto_return_minutes INT,               -- NULL = nunca
  updated_at TIMESTAMPTZ DEFAULT NOW()
);

-- messages.payload YA EXISTE (004, migración 0021): la 007 la reutiliza y no la vuelve a crear
-- (Giannier, Milestone B de la 004, decisión 2). Solo agrega el tipo 'event' al CHECK de msg_type.
CREATE INDEX idx_messages_search ON messages
  USING GIN (to_tsvector('spanish', content));
```

## API (REST + WS)
- `GET /tenant/conversations` (filtros, cursor) · `GET /tenant/conversations/:id` (hilo paginado) · `POST .../close|reopen`.
- `POST /tenant/conversations/:id/takeover` → 200 | `409 already_taken {by}` · `POST .../release` · `POST .../steal` (owner/tenant_admin).
- `POST /tenant/conversations/:id/messages` (mensaje humano; guard ventana 24h → `403 outside_24h_window`).
- `POST /tenant/conversations/:id/notes` · `POST /tenant/contacts/:id/opt-out` · `POST /tenant/contacts/:id/erase` (owner, auditado).
- `GET/PUT /tenant/notification-settings` · `POST /tenant/push-subscriptions`.
- `WS /ws?token=` — suscripción a eventos del tenant; mensajes de presencia.

## Testing
Unit: lock de takeover (carrera), cálculo de ventana en compositor, auto-return. Integration: escalamiento→notificación→takeover→mensaje humano→release con contexto (verifica que el agente lee lo humano); CancelGuard con generación en curso (mensaje descartado); WS reconexión con re-sync. E2E Playwright: bandeja con dos sesiones de operador simultáneas.

## Riesgos
| Riesgo | Mitigación |
|---|---|
| Pérdida de eventos WS en reconexión | Re-sync por REST al reconectar (estado, no historial de eventos) |
| Push poco confiable en iOS | Email de cola desatendida como respaldo; documentado al tenant |
| Hilos enormes degradan UI | Cursor + virtualización + carga perezosa de media |

## Estimación
2 semanas. Complejidad: Alta (tiempo real + concurrencia).

---

# Tasks — Conversaciones e Inbox
Commits: `feat(inbox): ... [TASK-7XX]`

- [x] **TASK-701: Migraciones + eventos de sistema en el hilo.**
- [x] **TASK-702: WebSocket + Redis Pub/Sub + presencia** (auth, canales por tenant, re-sync). Depends: 701.
- [x] **TASK-703: TakeoverService + CancelGuard** (lock, steal, auto-return job, integración con pipeline F002/F003). Depends: 701. Verificación: test de carrera <1s.
- [x] **TASK-704: API de bandeja** (listados/filtros/cursor, hilo, mensaje humano con guard 24h, notas, close/reopen, opt-out/erase auditados). Depends: 701. *Erase propuesto, no construido: C-102.*
- [x] **TASK-705: Notificaciones** (Web Push VAPID, email de cola desatendida, settings). Depends: 702. *Web Push propuesto, no construido: C-101.*
- [x] **TASK-706: Búsqueda** (tsvector español, endpoint, aislamiento verificado). Depends: 701.
- [ ] **TASK-707: UI de bandeja** (lista virtualizada en vivo, hilo con media/trazas/eventos, compositor con estado de ventana, takeover/presencia, aprobación de comprobantes F004). Depends: 702–705.
- [ ] **TASK-708: E2E multi-operador + endurecimiento.** Depends: todo. Milestone humano: Giannier y un ayudante simulan operación real 30 min con tráfico del número de prueba.

Dependencias: 701 → (702,703,704,706) → 705/707 → 708.

## Heredado de la 004 (Milestone B)
- `messages.payload` (migración 0021) ya existe: se reutiliza para los eventos del hilo.
- `contacts.posible_duplicado_de` (migración 0024): el inbox muestra la marca de "posible cliente
  duplicado" (un BSUID y un teléfono de dos contactos distintos; no se fusionan).
