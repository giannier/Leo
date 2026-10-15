# Milestone 009 — Billing

**Tasks:** TASK-901 a TASK-909 (adaptadas a las decisiones de Giannier del 2026-10-14: pagos manuales,
renovación con cobro pendiente) · **Quién verifica:** Claude, con pruebas automáticas y Playwright.
**Fecha:** 2026-10-15.

**La Constitution pide revisión humana línea por línea de todo diff de billing antes del merge.** Esto está
construido y probado, **no revisado por una persona**. El milestone humano de la TASK-909 (un ciclo real con
plata real antes del lanzamiento) se adapta a v1 así: Giannier contrata un plan desde un negocio propio,
paga por transferencia o Yape con la referencia y registra el pago en *Cobros*.

## Veredicto

**Construido y verificado por pruebas automáticas; pendiente de revisión humana.** Con el cobro apagado
(`BILLING_ENFORCED=false`, hoy) nada vence ni se pausa: se puede contratar y registrar pagos, y los períodos
se renuevan solos sin pedir pago.

## Criterios de aceptación (spec), adaptados

| Criterio | Evidencia | Resultado |
|---|---|---|
| Prueba → pago → activo | Por API (`test_billing.py::TestTrialToPaid`) y por navegador (`e2e/plan.spec.ts`, escritorio y Android): el dueño elige Emprende, ve el monto, la referencia `LEO-XXXXXX` y las instrucciones; el admin registra la transferencia en *Cobros*; el negocio queda activo en Emprende y descarga su comprobante PDF. Los días de prueba que quedaban se suman al primer mes (C-005) | ✅ (pasarela: después) |
| Fallo de cobro → gracia → pausa → pago → activo, con reloj simulado | `TestTheCalendar`: el cobro de renovación sale 7 días antes, una sola vez; al vencer sin pago, gracia de 5 días; recordatorios los días 1 y 3 y pausa el día 5, sin repetir correos al correr de nuevo; al pagar vuelve al instante con un período nuevo desde ese día. Si paga dentro de la gracia, sigue su calendario. Con el cobro apagado el período se renueva sin pedir nada | ✅ |
| Upgrade con prorrateo y downgrade validado, al centavo | Emprende a mitad de mes a Crece: crédito US$ 14.50 y cobro US$ 44.50. El downgrade se rechaza con la lista de lo que sobra (asientos, caracteres, fuentes) y, si cabe, queda programado: la renovación ya sale con el plan menor. Pruebas de propiedades (Hypothesis) de crédito, descuento y monto: siempre entre 0 y el precio, con dos decimales y a favor del cliente | ✅ |
| Idempotencia con replays | El mismo número de operación registrado dos veces devuelve el mismo pago (`repetido: true`) y no cobra dos veces; un cobro pagado no se paga de nuevo; un monto distinto se rechaza | ✅ |
| Cobertura ≥ 80 % (módulo crítico) | `services/billing` 91 a 97 %, APIs 98 y 99 %, repositorios y modelos 100 %, worker 90 % | ✅ |

**También:**
- Ledger de solo agregar: la base rechaza `UPDATE` y `DELETE`.
- Paquete de +500 mensajes: el negocio que se quedó sin mensajes vuelve a responder al pagarlo.
- Cupones: porcentaje, vencimiento, usos máximos y uno por negocio. Un cupón del 100 % activa sin
  registrar un pago.
- Reembolso registrado, cortesía (+días) auditada, roles (el operador no ve dinero; solo el dueño compra).
- Aislamiento: `tests/isolation/test_billing_isolation.py`.

## Corridas

- **Backend:** 3 496 pasan. La única falla era `env.reference`, que se regeneró. Ruff, formato y mypy
  limpios.
- **Frontend:** `tsc`, `eslint` y `next build` limpios. Playwright `plan.spec.ts`, `navigation.spec.ts` y
  `admin.spec.ts`: 16 de 16.

## Decisiones

**Bloqueantes:** ninguna.

**Para ratificar** (dinero; tomadas con la lectura conservadora y registradas en el plan, "Decisiones
Posteriores" 5 a 9):
- El cobro de renovación sale 7 días antes del fin del período.
- Si estaba en gracia, el período nuevo sigue el calendario; si estaba pausado, empieza el día del pago.
- La renovación cobra el menor entre el precio fijado del negocio y el de lista (C-004): una suba de precios
  necesita un aviso y un proceso aparte.
- Un pago manual debe coincidir al centavo con el cobro; si no, el admin lo rechaza y lo revisa.
- Las instrucciones de pago (cuenta, Yape, titular) van en `BILLING_PAYMENT_INSTRUCTIONS` y la tasa
  referencial de soles en `BILLING_PEN_REFERENCE_RATE` (vacía, no se muestran soles).

**Informativas:** el asiento extra (US$10) no entra en v1. Mercado Pago queda detrás del puerto
`PaymentProvider` para cuando haya cuenta y credenciales.
