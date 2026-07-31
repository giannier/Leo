# Plan Técnico — Tools Avanzadas

**Feature ID:** 004

## Constitution Check ✓
✅ Cada tool = módulo del registro, núcleo intacto. ✅ Visión/STT solo vía `LLMProvider`. ✅ "Nunca confirma un pago sin validación" implementado como estado + aprobación humana. ✅ Comprobantes en storage privado por tenant.

## Arquitectura
```
ToolRegistry ← nuevas tools: enviar_imagen | enviar_documento | enviar_sticker
                            consultar_stock/reservar_stock | validar_comprobante
Inbound media (F002 webhook) ─► MediaService: descarga → MinIO privado → messages.media_key
   ├─ imagen en contexto de pago ─► ReceiptService: vision(JSON schema) → receipts(pre_approved|flagged)
   └─ audio ─► TranscriptionService → texto → pipeline normal
CatalogDigitizer: imagen/PDF lista de precios → vision → borrador productos → preview F001
StockService: reservas FOR UPDATE + TTL (job release_expired_reservations)
```

### Decisiones Técnicas
| Decisión | Elegida | Razón |
|---|---|---|
| Salida de visión | JSON Schema estricto validado (pydantic) + reintento 1 con feedback | Estructura confiable para comparar montos |
| "Contexto de pago" | Señal del agente (últimas tools/summary) + fallback: toda imagen entrante se clasifica (recibo vs otra) con visión barata | Evita perder capturas enviadas sin aviso |
| Reserva de stock | Tabla stock_reservations + FOR UPDATE | Correctitud > sofisticación |
| Media saliente | Assets del tenant en MinIO, subidos/cacheados al proveedor (media_id reutilizable) | Menos latencia y ancho de banda |
| STT | Task `transcribe` del LLMProvider (proveedor con STT) o adaptador STT dedicado — decidir en TASK-405 con benchmark de costo/calidad es-PE | Mantiene principio de adaptadores |

## Data Model (migraciones)
```sql
ALTER TABLE messages ADD COLUMN media_key TEXT, ADD COLUMN transcript TEXT;

CREATE TABLE tenant_tools (
  tenant_id UUID NOT NULL REFERENCES tenants(id),
  tool_key  TEXT NOT NULL,               -- 'enviar_imagen','validar_comprobante',...
  enabled   BOOLEAN NOT NULL DEFAULT TRUE,
  config    JSONB NOT NULL DEFAULT '{}', -- umbrales, packs de stickers, etc.
  PRIMARY KEY (tenant_id, tool_key)
);

CREATE TABLE receipts (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id UUID NOT NULL REFERENCES tenants(id),
  conversation_id UUID NOT NULL REFERENCES conversations(id),
  message_id UUID NOT NULL REFERENCES messages(id),
  media_key TEXT NOT NULL,
  extracted JSONB NOT NULL,              -- {monto, fecha, destinatario, operacion, confianza}
  expected  JSONB,                       -- {monto_esperado, titular}
  status TEXT NOT NULL DEFAULT 'pre_approved'
         CHECK (status IN ('pre_approved','flagged','approved','rejected')),
  flags TEXT[], decided_by UUID REFERENCES users(id), decided_at TIMESTAMPTZ,
  created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE stock_reservations (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id UUID NOT NULL REFERENCES tenants(id),
  product_id UUID NOT NULL REFERENCES products(id),
  conversation_id UUID NOT NULL REFERENCES conversations(id),
  qty INT NOT NULL CHECK (qty > 0),
  expires_at TIMESTAMPTZ NOT NULL,
  released_at TIMESTAMPTZ, confirmed_at TIMESTAMPTZ,
  created_at TIMESTAMPTZ DEFAULT NOW()
);
CREATE TABLE sticker_assets (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id UUID REFERENCES tenants(id),  -- NULL = pack default de Leo
  media_key TEXT NOT NULL, label TEXT, created_at TIMESTAMPTZ DEFAULT NOW()
);
```

## API
- `GET/PUT /tenant/tools` — activar/configurar tools (según plan).
- `GET /tenant/receipts?status=` · `POST /tenant/receipts/:id/approve|reject` (bandeja F007 lo integra en UI; API nace aquí, auditada).
- `POST /tenant/catalog/digitize` (imagen/PDF) → preview de productos (reusa confirm de F001).
- `GET/POST/DELETE /tenant/stickers`.

## Testing
Unit: comparación monto/expected con tolerancias, flags de discrepancia, reservas concurrentes (FOR UPDATE), límites de media. Integration: flujo captura→pre_approved→approve humano; sobreventa 10×3; digitalización con fotos reales (fixtures). Evals nuevos (F003 runner): pertinencia de media, vocabulario de pagos, alternativas ante agotado. Aislamiento: presigned URLs de MinIO por tenant.

## Riesgos
| Riesgo | Mitigación |
|---|---|
| Falsos positivos de visión en capturas de baja calidad | Umbral de confianza → flagged (humano decide); mensajes pidiendo mejor foto |
| Fraude por captura editada | Decisión final humana siempre; C-002 documenta límites; monto+operación+fecha cruzados |
| Costo de visión/STT en volumen | Modelos por model_policies; clasificador barato antes del análisis completo; métricas de costo por tenant |

## Estimación
2 semanas. Complejidad: Alta.

---

# Tasks — Tools Avanzadas
Commits: `feat(tools): ... [TASK-4XX]`

- [ ] **TASK-401: Migraciones + registro tenant_tools** (con seed por plan). 
- [ ] **TASK-402: Media saliente** — extensión YCloudProvider (image/document/sticker), assets y caché de media_id, tools de envío con límite 2/respuesta. Depends: 401.
- [ ] **TASK-403: Media entrante** — descarga a MinIO, media_key en messages, clasificador de imagen (recibo/otra). Depends: 401.
- [ ] **TASK-404: Comprobantes** — ReceiptService con visión estructurada, estados y flags, API approve/reject auditada, integración del agente (vocabulario fijo). Depends: 403. Milestone humano: probar con capturas Yape/Plin reales propias.
- [ ] **TASK-405: Audio → texto** — benchmark y decisión STT (documentada), TranscriptionService, fallback >2min. Depends: 403.
- [ ] **TASK-406: Stock** — consultar/reservar con FOR UPDATE, TTL + job de liberación, alertas de umbral, config por tenant. Depends: 401. Verificación: test de sobreventa.
- [ ] **TASK-407: Catálogo por foto** — CatalogDigitizer + endpoint digitize + UI en wizard/panel con preview. Depends: 403.
- [ ] **TASK-408: Evals + panel de tools (UI)** — evals nuevos, pantalla de activación/config de tools, endurecimiento y cobertura. Depends: 402–407.

Dependencias: 401 → (402,403,406) ; 403 → 404/405/407 ; todo → 408.
