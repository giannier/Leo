# Plan Técnico — Canal WhatsApp Real

**Feature ID:** 002

## Constitution Check ✓
✅ Adaptador `YCloudProvider` implementa `WhatsAppProvider` (envío + parseo de webhooks) — nada llama a YCloud fuera de él. ✅ Webhook firmado + idempotente (Seguridad). ✅ Nunca fallar en silencio: dead-letter visible y alertas. ✅ Cola en Redis/ARQ ya existente.

## Arquitectura
```
YCloud ► POST /webhooks/ycloud ─► verificación HMAC ─► tabla webhook_events (raw, idempotente)
                                        └► encola process_inbound(event_id)
ARQ worker: process_inbound ─► upsert contact ─► resolver conversation ─► debounce buffer (Redis)
            └► al cerrar buffer: agent-core (F001 v0/F003) ─► WhatsAppProvider.send ─►
               outbound_message(status=sent) ◄─ webhooks de estado actualizan delivered/read/failed
```

### Decisiones Técnicas
| Decisión | Elegida | Razón |
|---|---|---|
| Persistencia del webhook crudo | Tabla `webhook_events` (JSONB) antes de procesar | Auditoría, reproceso, idempotencia barata |
| Debounce de ráfagas | Sorted set en Redis por contacto + job diferido 8s | Evita N respuestas a N mensajes seguidos |
| Orden por contacto | Lock Redis por `conversation_id` durante el procesamiento | FIFO por conversación, paralelismo entre conversaciones |
| Estados de entrega | Columnas en `messages` + historial en `message_status_events` | El detalle importa para debugging de entregas |
| Ventana 24h | Calculada: `contacts.last_inbound_at + 24h` | Fuente de verdad única, sin flags que caduquen |

## Data Model (migraciones nuevas)
```sql
CREATE TABLE contacts (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id UUID NOT NULL REFERENCES tenants(id),
  phone_e164 TEXT NOT NULL,
  profile_name TEXT,
  opted_out_at TIMESTAMPTZ,
  last_inbound_at TIMESTAMPTZ,
  created_at TIMESTAMPTZ DEFAULT NOW(), updated_at TIMESTAMPTZ DEFAULT NOW(),
  UNIQUE (tenant_id, phone_e164)
);

CREATE TABLE webhook_events (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  provider TEXT NOT NULL,
  provider_event_id TEXT NOT NULL UNIQUE,      -- idempotencia
  payload JSONB NOT NULL,
  status TEXT NOT NULL DEFAULT 'received'
         CHECK (status IN ('received','processed','failed','dead_letter')),
  error_msg TEXT, attempts INT NOT NULL DEFAULT 0,
  created_at TIMESTAMPTZ DEFAULT NOW(), processed_at TIMESTAMPTZ
);

-- ALTERs a tablas de F001:
ALTER TABLE conversations ADD COLUMN contact_id UUID REFERENCES contacts(id);
ALTER TABLE messages ADD COLUMN provider_message_id TEXT UNIQUE,
  ADD COLUMN msg_type TEXT NOT NULL DEFAULT 'text'
      CHECK (msg_type IN ('text','image','document','audio','sticker','location','contact','unsupported')),
  ADD COLUMN delivery_status TEXT
      CHECK (delivery_status IN ('queued','sent','delivered','read','failed'));

CREATE TABLE message_status_events (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  message_id UUID NOT NULL REFERENCES messages(id),
  status TEXT NOT NULL, provider_ts TIMESTAMPTZ, created_at TIMESTAMPTZ DEFAULT NOW()
);
```

## API
- `POST /webhooks/ycloud` — público con HMAC (fuera de /api/v1, sin JWT).
- `GET /tenant/usage` — consumo en vivo: mensajes IA usados/límite, in/out del mes.
- `GET /platform-admin/dead-letters` · `POST /platform-admin/dead-letters/:id/retry`.
- `GET /tenant/contacts` (paginado, búsqueda) — base para F007.

## Estrategia de Testing
Unit: parser de payloads YCloud (fixtures reales anonimizados), cálculo ventana 24h, debounce. Integration: webhook→respuesta end-to-end con provider mock; idempotencia (mismo evento ×3 = 1 mensaje); caos (kill del worker a mitad); concurrencia del contador de cuota. Contract: `YCloudProvider` contra mock server con firmas válidas/ inválidas.

## Riesgos
| Riesgo | Mitigación |
|---|---|
| Payloads de YCloud distintos a la doc | Guardar crudo en `webhook_events`; parser tolerante + alerta de campos desconocidos |
| Latencia LLM degrada p95 <10s | Timeout de agente 20s con mensaje de cortesía y reintento en background |
| Retries del proveedor duplican respuestas | Idempotencia por provider_event_id + lock por conversación |

## Estimación
1.5–2 semanas. Complejidad: Alta (confiabilidad y concurrencia).
