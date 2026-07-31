# Spec — Billing: Planes, Suscripciones y Conversión Trial→Pago

**Feature ID:** 009 · **Módulo:** 09-billing · **Estado:** Draft para ratificación
**Depende de:** 001 (plans, trial lifecycle), 002 (medición de consumo)

## Resumen
Monetiza Leo: el tenant elige plan, paga con métodos locales (Perú primero) y su
suscripción gobierna límites (mensajes IA, asientos, RAG). Estructura de planes basada
en la investigación de mercado: Emprende US$29 / Crece US$59 / Pro US$99 mensuales
(≈ S/ equivalente mostrado en el checkout), facturación anual −20%, mensajes de Meta
**pass-through sin markup** (argumento comercial de confianza, heredado de YCloud).
Restricción de Constitution: este módulo requiere revisión humana en todo cambio.

## User Stories

### HU-01: Elegir plan y pagar
**Como** dueño en trial, **quiero** contratar un plan en minutos con un método que uso.
- Página de planes con comparación clara (límites por plan desde tabla `plans`, nunca hardcodeados) y precio en USD + referencia en S/.
- WHEN elijo plan y período (mensual/anual −20%), THEN pago vía `PaymentProvider` (v1: **Mercado Pago** — tarjetas y Yape; adaptador preparado para Culqi/Stripe).
- WHEN el pago se confirma (webhook), THEN el tenant pasa a `active`, su plan se aplica al instante y recibo comprobante por email.
- WHEN el pago falla, THEN mensaje claro y reintento sin perder el trial restante.

### HU-02: Renovación y fallos de cobro
- WHEN llega la fecha de renovación, THEN se cobra automáticamente (suscripción del proveedor o cobro programado — según capacidades de Mercado Pago, decidir en TASK-903 y documentar).
- WHEN el cobro falla, THEN periodo de gracia de 5 días con reintentos (día 1, 3, 5) y avisos; al agotarse, tenant → `paused` (mismo comportamiento conocido del trial: nada se pierde, el agente se pausa).
- WHEN el tenant `paused` paga, THEN reactivación inmediata.

### HU-03: Cambio de plan
- Upgrade: inmediato, con prorrateo simple (crédito por días no usados aplicado al nuevo cobro).
- Downgrade: se aplica al inicio del siguiente período; si el uso actual excede los límites del plan menor (asientos, RAG), el sistema exige resolverlo antes (lista de qué sobra) o bloquea el downgrade.
- Cancelación: al final del período pagado; luego `paused` → política de retención estándar.

### HU-04: Medición y límites por plan
- Consumo mensual de mensajes IA por período de facturación (no calendario); visible en `/tenant/usage` con proyección ("a este ritmo llegarás al límite el día 22").
- WHEN se alcanza 80%/100%, THEN avisos; al 100%: opción de **paquete extra de mensajes** (add-on único, ej. +500 por US$8 — precio en `plans_addons`) o upgrade; sin compra, el agente se pausa hasta el nuevo período (nunca fallar en silencio).
- Asientos y RAG se validan contra el plan en el momento de uso (ya implementado en F001; aquí solo cambian los límites al cambiar plan).

### HU-05: Comprobantes y administración
- Historial de pagos y comprobantes descargables (v1: comprobante simple PDF; facturación electrónica SUNAT queda post-MVP explícito, evaluando OSE/facturador cuando haya volumen).
- Panel admin: ver suscripciones, otorgar descuentos/cortesías (cupones simples %, auditados), extender períodos, marcar pago manual (transferencia directa — realidad peruana de los primeros clientes).

## Requerimientos Funcionales
1. [RF-001] `PaymentProvider` con implementación MercadoPagoProvider (checkout + webhooks firmados + reembolso manual desde admin).
2. [RF-002] Webhooks de pago idempotentes (reusa patrón webhook_events de F002).
3. [RF-003] Estado de suscripción como máquina: `trialing → active → past_due → paused → canceled` sincronizada con `tenants.status`.
4. [RF-004] Períodos de facturación por tenant (`current_period_start/end`) y contador de mensajes por período (reemplaza el contador simple del trial, con migración de datos).
5. [RF-005] Add-on de mensajes extra aplicado al período vigente.
6. [RF-006] Cupones: porcentaje, un uso por tenant, expiración; creados solo por admin (auditado).
7. [RF-007] Pago manual: admin registra pago por transferencia con referencia; mismo efecto que webhook.
8. [RF-008] Todos los montos en tabla (plans, addons) — cero precios en código.
9. [RF-009] Emails: pago exitoso, fallo de cobro, gracia por vencer, límite 80/100%.

## Decisiones y Edge Cases (Clarify integrado)
- **C-001 Moneda de cobro:** USD como precio de lista; Mercado Pago cobra en moneda local según país del tenant (PEN para Perú). El tipo de cambio lo maneja el proveedor — Leo no fija tasas.
- **C-002 Impuestos:** v1 precios finales (impuestos incluidos); desglose fiscal formal llega con facturación electrónica. Documentado en términos.
- **C-003 Doble webhook / pago duplicado:** idempotencia por payment_id del proveedor; segundo cobro accidental → flujo de reembolso manual documentado en runbook.
- **C-004 Cambio de precios de lista:** los tenants existentes conservan su precio hasta renovación +1 aviso de 30 días (campo `price_locked_usd` en suscripción).
- **C-005 Trial que paga el día 1:** los días de trial restantes se suman como días gratis al primer período (goodwill barato, diferenciador).

## Fuera de Scope
- Facturación electrónica SUNAT (post-MVP, explícito), self-service de reembolsos, multi-moneda de lista, marketplace de add-ons más allá del paquete de mensajes.

## Criterios de Aceptación
- [ ] Flujo completo trial→pago→active con Mercado Pago sandbox, incluido Yape de prueba.
- [ ] Fallo de cobro → past_due → gracia → paused → pago → active, todo probado con reloj simulado.
- [ ] Upgrade con prorrateo y downgrade con validación de exceso, correctos al centavo (tests de dinero con Decimal, jamás float).
- [ ] Idempotencia de webhooks probada con replays.
- [ ] Cobertura ≥80% (módulo crítico) y revisión humana línea por línea del servicio de cobros (milestone).

---

## Decisión registrada — Modelo "socio" por comisión (2026-07-31)

**Propuesta de Giannier:** ofrecer dos formas de hacer negocio con el cliente:
(a) **servicio directo** — el tenant contrata un plan, que es lo ya especificado
arriba; y (b) **socio** — Leo cobra una comisión cuando el agente concreta una
venta.

**Decisión: (b) queda fuera del MVP.** Se retoma después de cerrar el MVP y con
ratificación explícita.

**Motivo técnico, no de preferencia.** Cobrar por venta concretada exige
*atribución*: el sistema tiene que registrar que hubo una venta, por qué monto,
y decidir si la causó el agente o el humano que retomó la conversación después.
Nada de eso existe hoy — no hay tabla de pedidos ni de ventas en la Feature 001,
y `016-pedidos-delivery` es post-MVP. Sin pedidos, el modelo socio no tiene
sobre qué calcularse.

**Qué haría falta antes de retomarlo:**
- Pedidos con monto y estado (Feature 016).
- Un evento de "venta concretada" enlazado a la conversación que la originó.
- Una regla de atribución escrita: ventana temporal, y qué pasa cuando un
  operador humano cierra una venta que el agente inició.
- Liquidación y comprobante de comisión, con su propio audit log.

**Nota de alcance:** esto no afecta a `payment_methods_business`. Esa tabla
guarda cómo los clientes **del tenant** le pagan **al tenant** (Yape, Plin,
transferencia) — instrucciones que el agente le dicta a un comprador. Es
independiente de cómo el tenant le paga a Leo.
