# Leo — Documentación SDD

Plataforma AaaS multi-tenant: negocios conectan su WhatsApp (API oficial de Meta),
configuran su catálogo y activan un agente de IA que atiende y vende.

## Estructura
```
leo-sdd/
├── README.md                          # Este archivo
├── CLAUDE.md                          # Instrucciones para Claude Code (copiar a la raíz del repo)
├── constitution.md                    # Principios y stack no negociables (v2, aprobada)
├── features/
│   ├── 001-onboarding/               # spec, clarifications, plan, data-model, contracts/api, tasks
│   ├── 002-canal-whatsapp/           # spec, plan (con data model y API), tasks
│   ├── 003-agent-core/               # spec, plan, tasks
│   ├── 004-tools/                    # spec, plan (incluye tasks)
│   ├── 006-conocimiento-avanzado/    # spec-plan-tasks combinado (feature corta)
│   ├── 007-conversaciones-inbox/     # spec, plan (incluye tasks) — cubre módulos 07 y 08
│   ├── 009-billing/                  # spec, plan (incluye tasks)
│   ├── 010-dashboard/                # spec-plan-tasks combinado (feature corta)
│   ├── 011-campanas/                 # spec-plan-tasks — plantillas, broadcasts, follow-ups
│   ├── 012-crm-leads/                # spec-plan-tasks — pipeline kanban automático
│   ├── 013-analytics-csat/           # spec-plan-tasks — embudo, CSAT, NPS, reporte mensual
│   ├── 014-citas/                    # spec-plan-tasks — agendamiento + Google Calendar
│   ├── 015-integraciones/            # spec-plan-tasks — webhooks, API pública, Sheets, widget
│   └── 016-pedidos-delivery/         # spec-plan-tasks — pedidos, comanda, zonas de envío
└── post-mvp/
    ├── README.md                     # Orden sugerido de la Fase 2 y regla de priorización
    └── roadmap-011-016.md            # Resumen ejecutivo + plan de migración Meta Tech Provider
```
Cobertura de módulos de la Constitution: `05-catalog` se construye dentro de 001 y se
cierra en 004; `08-inbox` está integrado en 007.

## Estado de decisiones
- Constitution: **aprobada** por Giannier.
- Features 001–016: **documentación completa en borrador para revisión**. Las decisiones
  de Clarify de 001 salen de la investigación de mercado (trial 14 días / 250 mensajes /
  sin tarjeta; sandbox ilimitado; 2 asientos en trial; precios de referencia $29/$59/$99);
  las de 002–016 están integradas en cada spec bajo "Decisiones y Edge Cases" — todas
  justificadas y marcadas para ratificación antes de implementar cada feature.

## Roadmap de desarrollo (orden y estimaciones)
1. **001 — Onboarding + conexión** (3–4 sem): fundaciones, wizard, sandbox, agent-core v0
2. **002 — Canal WhatsApp real** (1.5–2 sem): webhooks, envío, 24h, cuota en vivo
3. **003 — Agent-core completo** (2 sem): memoria, prompts por rubro, failover, evals
4. **004 — Tools avanzadas** (2 sem): media, visión de comprobantes, stock, catálogo por foto
5. **006 — Conocimiento avanzado** (1 sem): OCR, ingesta web, calidad de recuperación
6. **007 — Conversaciones + Inbox** (2 sem): bandeja en vivo, takeover, notificaciones
7. **009 — Billing** (2 sem): Mercado Pago, suscripciones, límites por plan
8. **010 — Dashboard** (1 sem): métricas de valor para el tenant y de margen para el admin

**Total MVP estimado: ~14–16 semanas** de desarrollo supervisado con Claude Code.

**Fase 2 (post-MVP, ~10–11 semanas):** 011 Campañas (2 sem) → 012 CRM (1.5) →
013 Analytics+CSAT (1.5) → 014 Citas o 016 Pedidos según los tenants reales (2) →
el restante (1.5–2) → 015 Integraciones/API (2). Detalle en `post-mvp/README.md`.

En paralelo desde la semana 1: trámite de verificación de negocio + Tech Provider en Meta
(ver post-mvp/roadmap-011-016.md), para migrar de YCloud a Embedded Signup v4 sin reescribir
— fecha límite de v4: 15-oct-2026.

## Cómo usarlo con Claude Code
```bash
# Opción Spec-Kit:
specify init leo --ai claude
# copiar constitution.md a .specify/memory/constitution.md
# copiar features/001-onboarding/* a .specify/features/001-onboarding/
# en Claude Code: /speckit.implement

# Opción directa (sin Spec-Kit):
# 1. Crear el repo y copiar leo-sdd/ a docs/
# 2. En CLAUDE.md del repo, indicar: "Lee docs/constitution.md y docs/features/001-onboarding/
#    antes de cualquier cambio. Ejecuta las tasks de tasks.md en orden, una por sesión,
#    respetando las Restricciones para la IA."
# 3. Ir task por task: pedir "Ejecuta TASK-001", revisar, commit, siguiente.
```

## Reglas de oro de la supervisión (equipo de 1 + IA)
- Una task por sesión; revisar el diff antes de commitear.
- Nunca saltarse los milestones de revisión humana de `tasks.md`.
- Cambios de esquema solo por migración Alembic; dependencias nuevas solo con aprobación.
- Los evals y la suite de aislamiento multi-tenant no se desactivan jamás "para avanzar".
