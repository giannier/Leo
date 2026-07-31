# Post-MVP — Guía de la Fase 2

Las features 011–016 ya cuentan con documentación SDD completa en
`features/011-*` … `features/016-*` (spec + clarify integrado + plan + tasks).
Este directorio conserva el plan transversal que no es una feature de código:
la migración a **Meta Tech Provider** (`roadmap-011-016.md`, sección final).

## Orden sugerido de la Fase 2 (~10–11 semanas)
1. **011 — Campañas, plantillas y follow-ups** (2 sem) — desbloquea a 013/014/016 (todas usan plantillas)
2. **012 — Mini-CRM de leads** (1.5 sem)
3. **013 — Analytics + CSAT** (1.5 sem)
4. **014 — Citas** (2 sem) — si hay tenants de servicios; si no, adelantar 016
5. **016 — Pedidos y delivery** (1.5–2 sem) — si hay tenants restaurante/retail
6. **015 — Integraciones y API pública** (2 sem) — al final: expone un producto ya estable

Regla de priorización: entre 014 y 016 decide la composición real de los primeros
tenants de pago, no el plan. La migración a Tech Provider corre en paralelo a toda la
fase y se implementa (Embedded Signup v4) apenas Meta apruebe — antes del 15-oct-2026.
