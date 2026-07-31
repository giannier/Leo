# CLAUDE.md — Instrucciones para Claude Code (proyecto Leo)

> Copia este archivo a la raíz del repositorio de Leo. Claude Code lo lee automáticamente.

## Antes de tocar cualquier cosa
1. Lee `leo-sdd/constitution.md`. Es ley. Sus "Restricciones para la IA" no admiten excepciones.
2. Lee el spec, plan y tasks de la feature en curso (`leo-sdd/features/NNN-*/`).
3. Trabaja **una task por sesión**, en el orden y con las dependencias de `tasks.md`.
4. Si una decisión no está en los docs, NO la inventes: propónla, espera aprobación de
   Giannier, y documenta la decisión en el plan de la feature antes de implementarla.

## Orden de desarrollo del MVP
001-onboarding → 002-canal-whatsapp → 003-agent-core → 004-tools →
006-conocimiento-avanzado → 007-conversaciones-inbox → 009-billing → 010-dashboard
(005-catalog y 08-inbox están absorbidos por 001/004 y 007 respectivamente.)

## Fase 2 (post-MVP) — solo tras cerrar el MVP y con ratificación de Giannier por feature
011-campanas → 012-crm-leads → 013-analytics-csat → (014-citas o 016-pedidos-delivery,
según tenants reales) → la restante → 015-integraciones. Ver `leo-sdd/post-mvp/README.md`.

## Reglas duras (resumen operativo de la Constitution)
- Toda query de negocio filtra por `tenant_id` en la capa de repositorio. La suite de
  aislamiento multi-tenant nunca se desactiva ni se marca skip.
- SDKs de terceros solo dentro de su adaptador (`providers/`). Nada de llamadas directas.
- Esquema de BD solo por migración Alembic. Nunca `create_all` ni edición manual.
- Dependencias nuevas: proponer con justificación y esperar aprobación explícita.
- Billing y credenciales: todo diff requiere revisión humana antes de merge.
- Prompts del agente: cualquier cambio corre los evals (`make evals`) y se adjunta el
  resultado en el PR.
- Dinero siempre con Decimal. Jamás float.
- Commits: Conventional Commits + `[TASK-XXX]`. Un commit por task como mínimo.

## Definition of Done de una task
- Código + tests de la task en verde localmente (`make test`), lint y mypy limpios.
- Cobertura del módulo dentro del estándar (80% críticos / 70% resto).
- Criterios de "Verification" de la task cumplidos y demostrables.
- Sin TODOs silenciosos: lo pendiente se anota en la task siguiente o se pregunta.
- Si la task cambió comportamiento visible: actualizar el doc correspondiente en `leo-sdd/`.

## Milestones de revisión humana
No avances más allá de un milestone marcado en `tasks.md` sin confirmación explícita de
Giannier. Los milestones existen porque él prueba a mano con datos y números reales.

## Estructura esperada del repo
```
leo/
├── CLAUDE.md
├── leo-sdd/               # ← este paquete SDD completo
├── backend/
│   ├── app/
│   │   ├── api/           # routers FastAPI
│   │   ├── services/      # lógica de negocio
│   │   ├── repositories/  # acceso a datos (exigen tenant_id)
│   │   ├── providers/     # adaptadores: llm/, whatsapp/, payments/, email/, storage/
│   │   ├── agent/         # agent-core, tools/, prompts/
│   │   ├── models/        # SQLAlchemy
│   │   ├── workers/       # jobs ARQ
│   │   └── core/          # config, seguridad, deps
│   ├── alembic/
│   ├── evals/             # datasets dorados + runner
│   └── tests/             # unit/ integration/ isolation/
├── frontend/              # Next.js 15 (panel tenant + admin)
└── deploy/                # docker-compose dev/prod, Caddy, CI
```

## Comandos estándar (crearlos en TASK-001 y mantenerlos)
```
make up          # docker compose dev
make test        # pytest completo
make isolation   # solo suite multi-tenant
make evals       # evals del agente
make lint        # ruff + mypy
make migrate     # alembic upgrade head
```
