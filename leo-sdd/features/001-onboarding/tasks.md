# Tasks — Onboarding de Tenant + Conexión de Número

**Feature ID:** 001
**Estimación total:** 3–4 semanas (supervisión de Claude Code por Giannier)
**Equipo:** Solo (Giannier supervisa, Claude Code implementa)

> **PRINCIPIO:** Cada task = 1 sesión de Claude Code. No superar ~70% del context window
> por task. Commit al completar cada task (Conventional Commits + `[TASK-XXX]`).
> Antes de cada task, Claude Code debe leer: `constitution.md`, este archivo, y los docs
> referenciados en Input. Ninguna task puede violar las Restricciones para la IA.

---

## Phase 0: Setup e Infraestructura

- [x] **TASK-001: Esqueleto del monorepo + Docker Compose de desarrollo**
  - **Input:** `constitution.md`, `plan.md`
  - **Output:** `backend/` (FastAPI + estructura de capas api/services/repositories/providers/models), `frontend/` (Next.js 15 + Tailwind), `deploy/docker-compose.dev.yml` (Postgres16+pgvector, Redis, MinIO, backend, frontend), `.env.example`, Ruff+mypy+pytest configurados, healthcheck `GET /api/v1/health`
  - **Verification:** `docker compose up` levanta todo; healthcheck OK; `pytest` y `ruff` corren en verde
  - **Parallel:** No (es la primera)

- [x] **TASK-002: CI en GitHub Actions**
  - **Input:** TASK-001
  - **Output:** workflow con lint (Ruff), mypy, pytest (backend), build de frontend; bloqueo de merge si falla
  - **Depends on:** TASK-001
  - **Verification:** pipeline verde en un PR de prueba

- [x] **TASK-003: Migración 0001 — esquema completo + seed de planes**
  - **Input:** `data-model.md`
  - **Output:** migraciones Alembic de todas las tablas, extensiones (vector, citext), seed de `plans` (trial/emprende/crece/pro con límites de clarifications), modelos SQLAlchemy tipados
  - **Depends on:** TASK-001
  - **Verification:** `alembic upgrade head` y `downgrade -1` sin errores; tests de smoke de modelos

- [x] **TASK-004: Capa base de repositorios multi-tenant + suite de aislamiento**
  - **Input:** TASK-003, `constitution.md` (principio de aislamiento)
  - **Output:** `BaseRepository` que exige `tenant_id` en toda operación; repos de products/knowledge/conversations; **suite de tests de aislamiento** (2 tenants fixture, acceso cruzado debe fallar) integrada a CI
  - **Depends on:** TASK-003
  - **Verification:** suite de aislamiento pasa y falla correctamente si se rompe el filtro (test del test)

- [x] **TASK-005: Adaptadores de proveedor (interfaces + implementaciones v1)**
  - **Input:** `plan.md` (Ports & Adapters), aprobación de dependencias
  - **Output:** interfaces `LLMProvider` (chat con tools + embed), `EmailProvider`, `StorageProvider`; implementaciones: LLM real configurable por env, email (Resend/SMTP según aprobación), MinIO; mocks para tests; contract tests
  - **Depends on:** TASK-001
  - **Parallel:** Sí (con TASK-003/004)
  - **Verification:** contract tests en verde con mocks; test `@slow` opcional contra proveedor real

---

## Phase 1: Identidad, Tenants y Equipo

- [x] **TASK-006: Registro + verificación de email**
  - **Input:** `contracts/api.md` (§auth), TASK-003/004/005
  - **Output:** `POST /auth/register` (transacción user+tenant trial+trial_ends_at), `verify-email`, `resend-verification`, emails transaccionales, validación `phone_in_active_trial` con `trial_phone_locks`
  - **Depends on:** TASK-003, TASK-005
  - **Verification:** integration tests del flujo completo, incl. duplicados de email/teléfono

- [x] **TASK-007: Login, JWT + refresh rotativo, logout**
  - **Input:** `contracts/api.md`, TASK-006
  - **Output:** login con memberships, access 15min, refresh httpOnly rotativo revocable, middleware de auth + resolución de tenant + roles, rate limit 5/min/IP en auth
  - **Depends on:** TASK-006
  - **Verification:** tests de rotación/revocación de refresh; acceso denegado sin email verificado

- [x] **TASK-008: OTP de teléfono**
  - **Input:** `contracts/api.md`, Clarification 005
  - **Output:** request-otp/verify-otp con hash, expiración 10min, máx 5 intentos, rate limit 3/h; envío vía canal decidido (documentar decisión en el PR)
  - **Depends on:** TASK-007
  - **Verification:** tests de expiración, reintentos y consumo único

- [x] **TASK-009: Equipo — invitaciones, roles y membresías**
  - **Input:** `contracts/api.md` (§team), Clarifications 003/008
  - **Output:** endpoints de team completos; límite de asientos desde `plans`; expiración 7 días; revocación de sesiones al desactivar miembro; autorización por rol en dependencias FastAPI reutilizables
  - **Depends on:** TASK-007
  - **Verification:** tests de límite de asientos, expiración, aceptación con usuario nuevo y existente

---

## Phase 2: Wizard, Catálogo y Conocimiento

- [ ] **TASK-010: Perfil de negocio + configuración del agente (API)**
  - **Input:** `contracts/api.md`, `data-model.md`
  - **Output:** GET/PUT de business-profile y agent-config con validaciones y `updated_at` para concurrencia
  - **Depends on:** TASK-007
  - **Parallel:** Sí (con TASK-011/012)
  - **Verification:** unit tests de validación; test de last-write-wins con aviso

- [ ] **TASK-011: Catálogo — CRUD + importación CSV/XLSX con preview**
  - **Input:** `contracts/api.md` (§productos)
  - **Output:** CRUD soft-delete; parser de import con preview en Redis (expira 1h), confirmación, reporte de filas inválidas; plantilla CSV descargable
  - **Depends on:** TASK-007
  - **Parallel:** Sí
  - **Verification:** tests con CSV válido, parcialmente inválido y malicioso (fórmulas, encoding)

- [x] **TASK-012: Ingesta de conocimiento + RAG (worker)**
  - **Input:** `contracts/api.md` (§conocimiento), Clarification 011
  - **Output:** endpoints de knowledge; worker `knowledge_indexer` (extracción PDF, chunking, `LLMProvider.embed`, estados pending→indexed/failed); límites por plan; búsqueda vectorial `search_knowledge(tenant_id, query, k)`
  - **Depends on:** TASK-004, TASK-005
  - **Verification:** integration test de ingesta e indexación; PDF sin texto → `failed` con mensaje; límite excedido → 413 con uso

- [ ] **TASK-013: Métodos de pago del negocio + checklist de activación**
  - **Input:** `contracts/api.md`, Clarification 009
  - **Output:** CRUD de payment-methods; `ActivationChecklistService` puro con los 6 ítems y flags `sandbox_ready`/`activation_ready`; endpoint del checklist
  - **Depends on:** TASK-010, TASK-011
  - **Verification:** unit tests de todas las combinaciones del checklist (tabla de verdad)

---

## Phase 3: Agent-core v0 + Sandbox

- [ ] **TASK-014: Agent-core v0 — motor y registro de tools**
  - **Input:** `constitution.md` (principios del agente), Clarification 010, TASK-005/012
  - **Output:** builder de system prompt desde config del tenant (identidad, tono, restricciones, reglas de escalamiento, horarios); loop LLM+tools con registro de tools; tools `consultar_catalogo` y `consultar_conocimiento`; guardrail "no inventar" (instrucción + fallback a escalamiento); persistencia de `agent_traces`
  - **Depends on:** TASK-012, TASK-013
  - **Verification:** unit tests del builder; tests del loop con LLM mock; traza completa por respuesta

- [ ] **TASK-015: Sandbox — endpoints de conversación**
  - **Input:** `contracts/api.md` (§sandbox), TASK-014
  - **Output:** crear conversación sandbox (guard `sandbox_ready`), enviar mensaje → respuesta del agente + traza; historial; rate limit 30/min/tenant; sin consumo de cuota (Clarification 002)
  - **Depends on:** TASK-014
  - **Verification:** integration test de conversación completa con catálogo real del fixture

- [ ] **TASK-016: Evals semilla en CI**
  - **Input:** TASK-015, `constitution.md` (estándar de evals)
  - **Output:** dataset de 5 conversaciones doradas (tenant restaurante demo) + runner de evals (aserciones: precio correcto, no inventa productos, escala cuando debe) + job de CI marcado requerido para cambios en `agent/`
  - **Depends on:** TASK-015
  - **Verification:** evals pasan; un cambio adversarial de prompt los hace fallar (test del eval)

---

## Phase 4: Conexión de número, Lifecycle del trial y Admin

- [ ] **TASK-017: Solicitudes de conexión (tenant) + cifrado de credenciales**
  - **Input:** `contracts/api.md` (§conexión), `data-model.md`, RF-008/009
  - **Output:** endpoints de connection-requests con validaciones (OTP requerido, unicidad E.164, solicitud abierta única); `whatsapp_numbers` con credenciales Fernet; interfaz `WhatsAppProvider` con implementación `ManualYCloudProvider` (v1: solo almacena/valida credenciales que carga el admin)
  - **Depends on:** TASK-008
  - **Verification:** tests de todas las validaciones; roundtrip de cifrado; contract test del provider

- [ ] **TASK-018: Panel admin de plataforma (API) + audit log**
  - **Input:** `contracts/api.md` (§platform-admin), Clarification 012
  - **Output:** cola de solicitudes, transición de estados con credenciales, notificación al tenant, listado/detalle de tenants, extend-trial; audit log en toda acción
  - **Depends on:** TASK-017
  - **Verification:** integration test del flujo requested→in_progress→connected/rejected; audit log presente

- [ ] **TASK-019: Lifecycle del trial (jobs) + activación del agente**
  - **Input:** RF-004/005/006, Clarification 001/004, `contracts/api.md` (§jobs)
  - **Output:** contador `ai_messages_used` (solo canal whatsapp — hook en agent-core listo para Feature 002); jobs `trial_notifier`, `trial_enforcer` (con cierre con gracia), `tenant_archiver`, `wizard_reminder`; endpoints activate/deactivate con guards
  - **Depends on:** TASK-014, TASK-018
  - **Verification:** tests con reloj simulado (freezegun) de todas las transiciones y avisos

---

## Phase 5: Frontend (Next.js)

- [ ] **TASK-020: Base del frontend — auth, layout y guardas**
  - **Input:** `contracts/api.md`, TASK-007; leer skill `frontend-design` si disponible
  - **Output:** páginas registro/login/verificación (email y OTP), cliente API tipado (OpenAPI), layout del panel con estado del trial visible, guardas por rol, manejo de sesión/refresh
  - **Depends on:** TASK-007 (API estable)
  - **Verification:** E2E Playwright: registro→verificación→login

- [ ] **TASK-021: Wizard de onboarding (UI)**
  - **Input:** spec HU-02, TASK-010/011/012/013
  - **Output:** wizard de 6 pasos con autosave, re-entrante, import CSV con preview, subida de PDFs con estados de indexación, checklist visual con deep-links a lo pendiente
  - **Depends on:** TASK-013, TASK-020
  - **Verification:** E2E: completar wizard hasta `sandbox_ready`

- [ ] **TASK-022: Sandbox (UI)**
  - **Input:** spec HU-03, TASK-015
  - **Output:** chat de simulación con etiqueta MODO PRUEBA, reinicio de conversación, panel expandible de trazas (tools y fuentes), estados de carga
  - **Depends on:** TASK-015, TASK-021
  - **Verification:** E2E: conversación con respuesta del agente y traza visible

- [ ] **TASK-023: Conexión de número + equipo + activación (UI)**
  - **Input:** HU-04/05/06, TASK-017/019, TASK-009
  - **Output:** formulario y seguimiento de solicitud de conexión (estados), gestión de equipo (invitar/roles/desactivar), switch del agente con guía de primer test real
  - **Depends on:** TASK-019, TASK-021
  - **Verification:** E2E con solicitud simulada llevada a `connected` vía API admin

- [ ] **TASK-024: Panel admin de plataforma (UI)**
  - **Input:** TASK-018
  - **Output:** vista de cola de solicitudes con acciones y captura de credenciales, listado de tenants con uso, acción extender trial
  - **Depends on:** TASK-018, TASK-020
  - **Parallel:** Sí (con TASK-022/023)
  - **Verification:** flujo manual documentado + E2E básico

---

## Phase 6: Quality Gates y Deploy

- [ ] **TASK-025: Endurecimiento y cobertura**
  - **Input:** toda la implementación, `constitution.md` (estándares)
  - **Output:** cobertura ≥80% agent/channels, ≥70% resto; rate limiting revisado; headers de seguridad; revisión de logs (sin secretos ni datos personales); casos edge de clarifications cubiertos por tests
  - **Depends on:** TASK-016..024
  - **Verification:** reporte de cobertura en CI; checklist de seguridad del plan marcado

- [ ] **TASK-026: Deploy a producción en Contabo**
  - **Input:** TASK-025, `deploy/`
  - **Output:** `docker-compose.prod.yml` (Caddy con HTTPS, backend, workers, frontend, Postgres, Redis, MinIO), backups diarios cifrados + script de restore probado, Sentry y Uptime Kuma conectados, GitHub Actions de deploy, runbook de operación (conexión YCloud, restore, extender trial)
  - **Depends on:** TASK-025
  - **Verification:** smoke test en producción del flujo registro→sandbox; restore de backup ejecutado una vez con éxito

---

## Resumen de Dependencias
```
T001 → T002
T001 → T003 → T004 ─────────────┐
T001 → T005 ─────────┬──────────┤
                     │          ▼
T003+T005 → T006 → T007 → T008 → T017 → T018 ─┐
                    │  └→ T009                 │
                    ├→ T010 ┐                  │
                    ├→ T011 ├→ T013 ┐          ▼
T004+T005 → T012 ───┘       │       ├→ T014 → T015 → T016
                            └───────┘             │
T014+T018 → T019 ─────────────────────────────────┤
T007 → T020 → T021 → T022                         │
T019+T021 → T023      T018+T020 → T024            ▼
T016..T024 → T025 → T026
```

## Convención de Commits
```
feat(auth): registro con tenant trial [TASK-006]
feat(agent): agent-core v0 con tools de catálogo y RAG [TASK-014]
test(tenancy): suite de aislamiento multi-tenant [TASK-004]
chore(deploy): compose de producción con backups [TASK-026]
```

## Milestones de revisión humana (Giannier)
1. Fin de Phase 0: levantar el entorno y revisar estructura. 
2. Fin de TASK-009: probar registro→login→equipo a mano.
3. Fin de TASK-016: conversar con el agente en sandbox vía API y revisar evals.
4. Fin de TASK-019: simular un ciclo completo de trial con reloj acelerado.
5. Fin de TASK-024: demo completa del flujo en local.
6. TASK-026: acompañar el deploy y ejecutar el restore de backup personalmente.
