# Spec + Plan + Tasks — Pedidos y Delivery

**Feature ID:** 016 · **Módulo:** 16-orders · **Estado:** Construido el 2026-10-22 sobre el módulo de pedidos de 004 y 019 (ver «Implementación» al final); decisiones D-1 a D-8 por ratificar
**Depende de:** 004 (pedido informal, stock, comprobantes), 011 (plantillas de estado), 012 (leads)

## Resumen
Formaliza el "pedido informal" de F004 en un módulo de pedidos con estados, datos de
entrega y notificaciones — el ciclo completo de venta por WhatsApp que restaurantes y
tiendas necesitan: tomar el pedido, cobrar, preparar, entregar, avisar. Sin pretender ser
un POS: Leo gestiona el pedido conversacional; la cocina/almacén sigue siendo del negocio.

## User Stories

### HU-01: Toma de pedido estructurada
**Como** cliente final, **quiero** pedir por chat y que no se pierda nada.
- La tool `crear_pedido` del agente arma el pedido con items del catálogo (cantidad,
  variantes en texto libre tipo "sin cebolla"), valida stock (F004) y calcula total.
- WHEN el pedido requiere entrega, THEN el agente pide y registra: dirección (texto +
  referencia), nombre de quien recibe, teléfono si difiere; para recojo: hora estimada.
- WHEN el cliente confirma el resumen ("¿confirmamos? Total S/ 47"), THEN el pedido pasa
  a `pending_payment` (o `confirmed` si el método es contra entrega) y reserva stock.

### HU-02: Cobro integrado
- WHEN el método es Yape/Plin/transferencia, THEN el flujo de comprobantes (F004) liga el
  receipt al pedido; `approved` → pedido `confirmed` y lead `ganado` (F012) automático.
- WHEN pasa el TTL de reserva (30 min, F004) sin pago, THEN el pedido expira con aviso
  amable y el stock se libera.

### HU-03: Gestión operativa del negocio
**Como** operador, **quiero** una cola de pedidos tipo comanda.
- Vista "Pedidos de hoy": columnas por estado `confirmed → preparing → ready →
  out_for_delivery → delivered` (+ `canceled`); tarjeta con items, total, pago, entrega,
  tiempo en estado; sonido/push al entrar pedido nuevo (reusa F007).
- Mover de estado con un toque (pantalla pensada para tablet en mostrador/cocina).
- Config por tenant: modos habilitados (delivery/recojo/en mesa futuro), costo de envío
  (fijo o por zona simple: lista de zonas+precio), tiempo estimado por defecto.

### HU-04: Notificaciones de estado al cliente
- WHEN el pedido pasa a `confirmed | ready | out_for_delivery`, THEN aviso por WhatsApp
  (dentro de ventana: mensaje normal; fuera: plantilla utility) — configurable qué estados
  notifican.
- WHEN se cancela (por el negocio o por expiración), THEN aviso con motivo y disculpa;
  si hubo pago aprobado, el reembolso es manual (runbook F009) y el pedido queda
  `canceled_refund_pending` hasta que el operador lo marque resuelto.

### HU-05: Historial y repetición
- El contacto tiene historial de pedidos visible en bandeja/CRM; tool `repetir_pedido`
  ("lo de siempre") arma el último pedido entregado para confirmar — retención barata.

## Requerimientos Funcionales
1. [RF-001] `orders` + `order_items` con snapshot de precios al momento (los cambios de catálogo no alteran pedidos pasados).
2. [RF-002] Máquina de estados con transiciones válidas por actor (agente solo hasta `confirmed`; operación es humana; `delivered` puede marcarlo el agente si el cliente lo confirma por chat).
3. [RF-003] Integración bidireccional con F004: receipt.approved → confirmed; reserva de stock ligada al pedido (confirm consume, cancel/expire libera).
4. [RF-004] Costo de envío por zona (lista simple nombre+precio; el agente pregunta la zona y lo suma al total; "no sé mi zona" → escala o tarifa default).
5. [RF-005] Eventos de dominio (F015): `order.created|confirmed|status_changed|canceled` — Sheets y webhooks los reciben gratis.
6. [RF-006] Numeración diaria amigable por tenant (`#037`) además del UUID (la cocina grita "¡el 37!").
7. [RF-007] Notificaciones de estado con topes (máx 4 por pedido) y plantillas requeridas fuera de ventana (guard de config, patrón F014 C-006).
8. [RF-008] Métricas a metrics_daily: pedidos, ticket promedio, tiempo confirmado→entregado, cancelaciones por motivo.

## Decisiones y Edge Cases (Clarify integrado)
- **C-001 Cambios post-confirmación** ("agrégale una gaseosa"): antes de `preparing` el agente puede editar (revalida stock y total, re-confirma); desde `preparing` en adelante, solo el operador (el negocio decide si acepta).
- **C-002 Pago parcial o monto distinto en el comprobante:** el receipt queda `flagged` (F004) y el pedido en `pending_payment` con nota; decisión humana.
- **C-003 Dirección por ubicación de WhatsApp:** los mensajes `location` (F002) se aceptan como dirección de entrega (lat/lng + texto que el cliente añada) — resuelve el caso real #1 de delivery.
- **C-004 Doble pedido activo del mismo contacto:** permitido (familias piden dos veces); el agente lo confirma explícitamente ("¿es un pedido nuevo aparte del #37?").
- **C-005 Horario de cierre a mitad de pedido:** si el negocio cierra antes del tiempo estimado, el agente lo advierte antes de confirmar ("cerramos en 20 min, ¿lo programamos para mañana?" — programado = `confirmed` con `scheduled_for`).
- **C-006 Delivery propio vs apps:** Leo solo gestiona delivery propio del negocio; integraciones con Rappi/PedidosYa fuera de scope (mundo aparte).

## Fuera de Scope
- POS/facturación de mostrador, cocina KDS avanzada (tiempos por estación), tracking GPS del repartidor, pasarela de pago online del cliente final (evaluar tras F009 maduro), integración con apps de delivery (C-006).

## Plan técnico

### Constitution Check ✓
✅ Tools nuevas registradas; núcleo intacto. ✅ El agente no inventa: precios/stock/zonas desde BD; cambios de dinero (reembolsos) son humanos y auditados. ✅ Compliance de notificaciones (plantillas + topes). ✅ Config sobre código (zonas, modos, notificaciones).

### Arquitectura
```
OrderService: máquina de estados + snapshot de precios + numeración diaria
Tools: crear_pedido / editar_pedido / consultar_pedido / repetir_pedido
Integraciones: ReceiptListener (F004) ─► confirmar; StockReservation ligada al pedido;
  LeadListener (F012) ─► ganado; DomainEvents (F015)
NotifierJob: cambios de estado ─► mensaje/plantilla con topes
Comanda UI: columnas por estado, WS en vivo (F007), modo tablet
```

### Decisiones Técnicas
| Decisión | Elegida | Razón |
|---|---|---|
| Snapshot de items | Copia de nombre/precio en order_items | Inmutabilidad contable del pedido |
| Numeración diaria | Secuencia por (tenant, fecha) con tabla de contadores + lock | Amigable y sin colisiones |
| Zonas de envío | Lista plana nombre+precio (sin geocercas) | 90% del valor, 5% de la complejidad; C-003 cubre ubicación |
| Estados | Enum fijo v1 (sin custom) | Las comandas necesitan estandarización; custom = post-v1 |
| Programados (C-005) | `scheduled_for` + job que los "despierta" al inicio del día/turno | Simple |

### Data Model (migraciones)
```sql
CREATE TABLE delivery_zones (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id UUID NOT NULL REFERENCES tenants(id),
  name TEXT NOT NULL, price NUMERIC(10,2) NOT NULL,
  is_active BOOLEAN NOT NULL DEFAULT TRUE,
  UNIQUE (tenant_id, name)
);

CREATE TABLE orders (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id UUID NOT NULL REFERENCES tenants(id),
  daily_number INT NOT NULL, order_date DATE NOT NULL,
  contact_id UUID NOT NULL REFERENCES contacts(id),
  conversation_id UUID REFERENCES conversations(id),
  lead_id UUID REFERENCES leads(id),
  mode TEXT NOT NULL CHECK (mode IN ('delivery','pickup')),
  status TEXT NOT NULL DEFAULT 'draft' CHECK (status IN
    ('draft','pending_payment','confirmed','preparing','ready',
     'out_for_delivery','delivered','canceled','canceled_refund_pending','expired')),
  subtotal NUMERIC(12,2) NOT NULL, delivery_fee NUMERIC(10,2) NOT NULL DEFAULT 0,
  total NUMERIC(12,2) NOT NULL,
  payment_method TEXT, receipt_id UUID REFERENCES receipts(id),
  delivery_zone_id UUID REFERENCES delivery_zones(id),
  delivery_address TEXT, delivery_location JSONB,      -- C-003 {lat,lng,texto}
  receiver_name TEXT, receiver_phone TEXT,
  scheduled_for TIMESTAMPTZ, cancel_reason TEXT,
  created_by TEXT NOT NULL CHECK (created_by IN ('agent','user')),
  created_at TIMESTAMPTZ DEFAULT NOW(), updated_at TIMESTAMPTZ DEFAULT NOW(),
  UNIQUE (tenant_id, order_date, daily_number)
);

CREATE TABLE order_items (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  order_id UUID NOT NULL REFERENCES orders(id) ON DELETE CASCADE,
  tenant_id UUID NOT NULL,
  product_id UUID REFERENCES products(id),
  name_snapshot TEXT NOT NULL, price_snapshot NUMERIC(12,2) NOT NULL,
  qty INT NOT NULL CHECK (qty > 0), notes TEXT           -- "sin cebolla"
);

CREATE TABLE order_events (
  id BIGSERIAL PRIMARY KEY, tenant_id UUID NOT NULL,
  order_id UUID NOT NULL REFERENCES orders(id) ON DELETE CASCADE,
  actor TEXT NOT NULL, from_status TEXT, to_status TEXT, detail JSONB DEFAULT '{}',
  created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE order_counters (
  tenant_id UUID NOT NULL, order_date DATE NOT NULL, last_number INT NOT NULL DEFAULT 0,
  PRIMARY KEY (tenant_id, order_date)
);

CREATE TABLE order_settings (
  tenant_id UUID PRIMARY KEY REFERENCES tenants(id),
  modes TEXT[] NOT NULL DEFAULT '{delivery,pickup}',
  default_prep_minutes INT NOT NULL DEFAULT 30,
  notify_statuses TEXT[] NOT NULL DEFAULT '{confirmed,ready,out_for_delivery}',
  status_template_id UUID REFERENCES templates(id),
  updated_at TIMESTAMPTZ DEFAULT NOW()
);
ALTER TABLE stock_reservations ADD COLUMN order_id UUID REFERENCES orders(id);
```

### API
- `GET /tenant/orders?date=&status=` (comanda) · `GET /tenant/orders/:id` · `POST /tenant/orders` (manual) · `POST /tenant/orders/:id/status {to, reason?}` · `POST .../resolve-refund`.
- Config: `GET/PUT /tenant/order-settings` · CRUD `/tenant/delivery-zones`.
- Contacto: `GET /tenant/contacts/:id/orders` (historial, usado por bandeja y tool repetir_pedido).

### Testing
Unit: máquina de estados por actor (tabla completa), totales con zonas y snapshots (Decimal), numeración diaria concurrente, topes de notificación. Integration: flujo completo pedido→pago→confirmado→entregado con eventos F004/F012/F015 reales; expiración con liberación de stock; C-001 edición pre/post preparing; C-005 programados. Evals: toma de pedido con variantes/notas, C-004 doble pedido, resumen y confirmación correctos (dataset restaurante extendido). E2E: comanda en tablet-viewport moviendo estados.

### Riesgos
| Riesgo | Mitigación |
|---|---|
| Pedidos mal tomados dañan la confianza más que no tener el módulo | Confirmación explícita con resumen SIEMPRE antes de crear; evals dedicados; edición fácil C-001 |
| Cancelaciones con pago aprobado (reembolsos) | Estado explícito refund_pending + runbook F009 + no se oculta hasta resolverse |
| Comanda ignorada por el personal | Push + sonido + milestone piloto midiendo tiempo confirmado→preparing |

### Estimación
1.5–2 semanas. Complejidad: Media-Alta.

## Tasks
Commits: `feat(orders): ... [TASK-16XX]`
- [ ] **TASK-1601: Migraciones + settings + zonas + numeración diaria.**
- [ ] **TASK-1602: OrderService (estados, snapshots, totales, programados) + eventos.** Depends: 1601.
- [ ] **TASK-1603: Integraciones F004/F012 (receipts→confirmed, reservas ligadas, lead ganado) + expiración.** Depends: 1602.
- [ ] **TASK-1604: Tools del agente (crear/editar/consultar/repetir) + evals de toma de pedido.** Depends: 1602; toca `agent/` → evals.
- [ ] **TASK-1605: Notificaciones de estado (topes + plantilla + guard).** Depends: 1602, F011.
- [ ] **TASK-1606: Comanda UI (tablet-first, WS, sonido) + config + historial en bandeja.** Depends: 1602–1605.
- [ ] **TASK-1607: Endurecimiento + reconciliación de métricas.** Milestone humano: servicio de un día completo con un restaurante piloto; medir pedidos mal tomados (meta: 0 con confirmación) y tiempo de comanda.

Dependencias: 1601 → 1602 → 1603/1604/1605 → 1606 → 1607.


---

## Implementación (2026-10-22, migración 0066)

Este documento se escribió antes de que existieran el pedido de 004, las zonas de `entregas`, la numeración por negocio,
el historial del cliente ni «repetir pedido». Eso ya estaba hecho; lo que faltaba, y se construyó, es la parte **operativa**:

| Spec | Hecho |
|---|---|
| HU-03 comanda | `/pedidos/comanda`: columnas por etapa, el que más espera primero, tiempo en la etapa con color, un toque para avanzar, sonido opcional, se actualiza cada 15 s, pensada para tableta |
| HU-04 avisos de estado | `pedido_listo` (solo recojo) y `pedido_en_camino` (solo delivery), editables por el dueño; interruptores en `order_settings`; no salen con la ventana de 24 h cerrada (el operador lo ve) |
| C-003 ubicación de WhatsApp | el pin se guarda en el pedido (`ubicacion_entrega`), el modelo lo lee como dirección y el operador lo abre en el mapa |
| HU-02 reembolso | `devolucion_pendiente` ya existía; ahora el operador lo marca resuelto (`POST /{id}/refund-resolved`, con bitácora) |
| RF-008 métricas | tiempo medio confirmado → entregado en el resumen (`tiempo_medio_entrega_min`) |
| HU-05 historial y repetir | ya hecho en 019 fase G (`consultar_pedidos`) |
| RF-004 zonas de envío | ya hecho (`entregas.zonas` del negocio, con envío gratis desde un monto) |

### Decisiones (por ratificar por Giannier)
- **D-1. Las etapas son una columna, no estados nuevos.** `orders.etapa` (`preparando`, `listo`, `en_camino`) solo vale mientras el
  pedido está `en_curso`. El spec proponía estados nuevos (`preparing`, `ready`, `out_for_delivery`, `delivered`); se descartó porque
  los hechos del agente, la devolución pendiente, las ventas y el embudo ya dependen de `confirmado / en_curso / completado`, y
  cambiarlos arriesgaba lo que ya funciona. `en_curso` + etapa dice lo mismo. *Entregado* es `completado`.
  (La «etapa» del pedido no es la «etapa» de la conversación —INICIO, IDENTIFICAR…—: son cosas distintas.)
- **D-2. El número `#37` sigue siendo el consecutivo del negocio, no diario.** Ya aparece en avisos, comprobantes y devoluciones;
  cambiarlo a diario obligaría a que `#5` signifique cosas distintas según el día. Si la cocina lo prefiere, se agrega un número
  de turno aparte.
- **D-3. Los avisos salen por defecto, dentro de la ventana de 24 h.** Fuera de la ventana no se envían (no hay plantillas utility
  aún: es el hueco de «seguimientos con plantilla»), y la respuesta de la API lo dice para que el operador avise por otro medio.
  Un recojo avisa «listo»; un delivery avisa «en camino»: el otro paso no manda nada para no mandar dos mensajes por una entrega.
  **Cada aviso es un mensaje de servicio y desde el 2026-10-01 esos se cobran por mensaje entregado** (aprox. US$ 0,030 en Perú, con
  1.000 gratis al mes por número según partners; ver `leo-sdd/meta-reglas-2026-10.md` §1). Lo cobra Meta al negocio. Quedan
  **encendidos por defecto** (el cliente espera saber que su pedido salió); es dinero del negocio, así que Giannier decide si el
  valor por defecto debe ser apagado. El dueño los apaga en la comanda. El gasto cuenta en «gasto estimado en WhatsApp» de Resultados.
- **D-4. El pin es dato personal.** Lo ven los miembros del negocio y no se escribe en logs ni se envía al modelo (el modelo lee
  «[Ubicación compartida por WhatsApp]» y, si es un lugar guardado, su nombre o dirección). Un pin de las últimas 12 h es del
  pedido que se arma; uno más viejo no. *La forma del mensaje (`location.latitude/longitude/name/address`) es la de Meta, que
  YCloud reenvía; no se ha probado con un webhook real.*
- **D-5. C-001 (agregar algo después de confirmar) no cambia:** un pedido confirmado es negocio cerrado (C-06 de 004); pedir algo
  más crea un pedido nuevo. Editar un pedido en preparación sería una decisión del negocio, no del agente.
- **D-6. Pedidos programados (C-005) no se construyeron.** Dependen de los horarios de atención y de despertar el pedido a la hora;
  es una decisión de producto (¿el negocio acepta pedidos para otro día?) antes que de código.
- **D-7. Eventos de dominio (RF-005) esperan a 015** (integraciones y webhooks): no hay a quién entregarlos todavía.
- **D-8. Sin reparto ni asignación de repartidor.** La comanda dice qué sale y a dónde; quién lo lleva no se registra (fuera de
  alcance del spec: tracking GPS y apps de delivery).

**Sin probar con un negocio real:** un servicio completo de un día en la comanda (el hito humano de TASK-1607) y el pin con un
webhook real de WhatsApp.
