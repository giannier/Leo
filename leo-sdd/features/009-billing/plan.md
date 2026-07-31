# Plan Técnico — Billing

**Feature ID:** 009

## Constitution Check ✓
✅ `PaymentProvider` como único punto de contacto con Mercado Pago. ✅ Restricción IA: nada de este módulo se mergea sin revisión humana. ✅ Nunca fallar en silencio: avisos en cada transición. ✅ Precios y límites en BD.

## Arquitectura
```
Checkout (frontend) ─► POST /tenant/billing/checkout ─► PaymentProvider.create_checkout ─► redirección MP
MP webhook ─► /webhooks/mercadopago (firma+idempotencia) ─► BillingService.apply_payment
BillingService: máquina de estados de suscripción ─► sincroniza tenants.status/plan ─► emails
Jobs: renewal_charger (si cobro programado), dunning (reintentos día 1/3/5), period_roller
       (cierra período, resetea contador, aplica downgrades pendientes)
Todo movimiento de dinero/estado escribe en billing_ledger (append-only)
```

### Decisiones Técnicas
| Decisión | Elegida | Razón |
|---|---|---|
| Proveedor v1 | Mercado Pago | Tarjetas + Yape en Perú, sandbox decente, cobertura LATAM para expansión |
| Suscripción | Preferencia por "preapproval" (suscripciones MP); fallback: cobro programado propio con tarjeta tokenizada — decidir en TASK-903 con spike de 1 día y documentar | Capacidades de MP varían por país; el spike evita construir sobre supuestos |
| Dinero | `NUMERIC`/Decimal en todo el camino; centavos jamás en float | Correctitud contable |
| Ledger | `billing_ledger` append-only (evento, monto, saldo resultante) | Auditoría y debugging de disputas |
| Prorrateo | Crédito lineal por días no usados, redondeo a favor del cliente | Simple y defendible |
| Comprobante v1 | PDF simple generado (número correlativo interno) | SUNAT después; correlativo desde ya para orden |

## Data Model (migraciones)
```sql
CREATE TABLE subscriptions (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id UUID NOT NULL UNIQUE REFERENCES tenants(id),
  plan_id TEXT NOT NULL REFERENCES plans(id),
  status TEXT NOT NULL CHECK (status IN ('trialing','active','past_due','paused','canceled')),
  billing_cycle TEXT NOT NULL CHECK (billing_cycle IN ('monthly','annual')),
  price_locked_usd NUMERIC(8,2) NOT NULL,
  current_period_start TIMESTAMPTZ NOT NULL,
  current_period_end   TIMESTAMPTZ NOT NULL,
  pending_plan_id TEXT REFERENCES plans(id),      -- downgrade programado
  provider_ref TEXT,                               -- id de suscripción/preapproval en MP
  grace_until TIMESTAMPTZ, canceled_at TIMESTAMPTZ,
  created_at TIMESTAMPTZ DEFAULT NOW(), updated_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE payments (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id UUID NOT NULL REFERENCES tenants(id),
  subscription_id UUID REFERENCES subscriptions(id),
  provider TEXT NOT NULL, provider_payment_id TEXT UNIQUE,   -- idempotencia
  kind TEXT NOT NULL CHECK (kind IN ('subscription','addon','manual')),
  amount_usd NUMERIC(8,2) NOT NULL, amount_local NUMERIC(10,2), currency_local TEXT,
  status TEXT NOT NULL CHECK (status IN ('approved','failed','refunded')),
  receipt_number TEXT UNIQUE, receipt_pdf_key TEXT,
  registered_by UUID REFERENCES users(id),         -- pagos manuales
  created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE plan_addons (
  id TEXT PRIMARY KEY,                             -- 'extra_500_msgs'
  name TEXT NOT NULL, ai_messages INT NOT NULL, price_usd NUMERIC(8,2) NOT NULL,
  is_active BOOLEAN NOT NULL DEFAULT TRUE
);

CREATE TABLE usage_periods (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id UUID NOT NULL REFERENCES tenants(id),
  period_start TIMESTAMPTZ NOT NULL, period_end TIMESTAMPTZ NOT NULL,
  ai_messages_used INT NOT NULL DEFAULT 0,
  ai_messages_limit INT NOT NULL,                  -- plan + addons del período
  UNIQUE (tenant_id, period_start)
);
-- Migración de datos: tenants.ai_messages_used → usage_periods del período vigente.

CREATE TABLE coupons (
  code TEXT PRIMARY KEY, percent_off INT NOT NULL CHECK (percent_off BETWEEN 1 AND 100),
  expires_at TIMESTAMPTZ, max_redemptions INT, created_by UUID REFERENCES users(id),
  created_at TIMESTAMPTZ DEFAULT NOW()
);
CREATE TABLE coupon_redemptions (
  coupon_code TEXT REFERENCES coupons(code), tenant_id UUID REFERENCES tenants(id),
  redeemed_at TIMESTAMPTZ DEFAULT NOW(), PRIMARY KEY (coupon_code, tenant_id)
);

CREATE TABLE billing_ledger (
  id BIGSERIAL PRIMARY KEY,
  tenant_id UUID NOT NULL, event TEXT NOT NULL, detail JSONB NOT NULL DEFAULT '{}',
  amount_usd NUMERIC(10,2), created_at TIMESTAMPTZ DEFAULT NOW()
);
```

## API
- `GET /tenant/billing/plans` (público-tenant) · `POST /tenant/billing/checkout` `{plan_id, cycle, coupon?}` → URL de pago · `POST /tenant/billing/addons/:id/checkout`.
- `GET /tenant/billing` (suscripción, próximos cobros, historial y comprobantes) · `POST /tenant/billing/change-plan` `{plan_id}` (upgrade inmediato/downgrade programado con validación de exceso) · `POST /tenant/billing/cancel`.
- `POST /webhooks/mercadopago` (público, firmado, idempotente).
- Admin: `GET /platform-admin/subscriptions` · `POST /platform-admin/payments/manual` · `POST /platform-admin/coupons` · `POST /platform-admin/subscriptions/:id/comp` (cortesía) — todo auditado.

## Testing
Unit: máquina de estados completa, prorrateo al centavo (property-based con Hypothesis), validación de downgrade, cupones. Integration: sandbox MP end-to-end (checkout, webhook, reembolso), dunning con reloj simulado, period_roller (reset de contador + downgrade pendiente), replays de webhook. Migración de datos del contador probada sobre copia.

## Riesgos
| Riesgo | Mitigación |
|---|---|
| Capacidades de suscripción de MP en Perú distintas a lo asumido | Spike TASK-903 antes de construir; fallback de cobro programado ya diseñado |
| Errores de dinero | Decimal + ledger + property tests + revisión humana obligatoria |
| Contracargos/disputas | Runbook manual + ledger como evidencia; volumen bajo al inicio |

## Estimación
2 semanas (+revisión humana). Complejidad: Alta.

---

# Tasks — Billing
Commits: `feat(billing): ... [TASK-9XX]` — **todas requieren revisión humana del diff (Constitution)**

- [ ] **TASK-901: Migraciones + ledger + migración del contador a usage_periods.**
- [ ] **TASK-902: PaymentProvider + MercadoPagoProvider (checkout único + webhook firmado idempotente)** con sandbox. Depends: 901.
- [ ] **TASK-903: SPIKE (1 día): suscripciones MP en Perú** — preapproval vs cobro programado; decisión documentada en este plan (actualizarlo). Depends: 902.
- [ ] **TASK-904: BillingService — máquina de estados + apply_payment + sincronización con tenants** + emails. Depends: 902.
- [ ] **TASK-905: Renovación + dunning + period_roller** según decisión de 903, con reloj simulado. Depends: 903, 904.
- [ ] **TASK-906: Cambios de plan (prorrateo/downgrade validado) + cancelación + C-005 (días de trial como crédito).** Depends: 904.
- [ ] **TASK-907: Add-on de mensajes + cupones + pagos manuales + comprobante PDF con correlativo.** Depends: 904.
- [ ] **TASK-908: UI — página de planes, checkout, billing del tenant, admin de suscripciones.** Depends: 904–907.
- [ ] **TASK-909: Endurecimiento + property tests de dinero + revisión humana línea por línea del BillingService.** Depends: todo. Milestone humano: Giannier ejecuta un ciclo completo real con tarjeta propia en producción (monto mínimo) antes del lanzamiento.

Dependencias: 901 → 902 → 903 → 905; 902 → 904 → 906/907 → 908 → 909.
