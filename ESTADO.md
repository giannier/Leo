# ESTADO.md — Leo frente a los documentos SDD

Rama `task-025-hardening-and-coverage`, medido el 2026-10-07. Cada afirmación sale del repo (código, `tasks.md`, `git log`) o de una ejecución propia. Lo que no se pudo confirmar dice "no verificado".

## 1. Estado por feature

| Feature | Estado | Evidencia |
|---|---|---|
| 001 Onboarding | Parcial (25/26 tasks) | TASK-001..025 marcadas `[x]`; TASK-026 (deploy Contabo) sin hacer. No existe `docker-compose.prod.yml` ni Caddy/Sentry/backups en `deploy/` |
| 002 Canal WhatsApp | Hecho salvo milestone humano | TASK-201..208 `[x]`; 209 `[~]`: test de caos hecho, falta la prueba real con número propio |
| 003 Agent-core | Cerrada en versión mínima (2026-10-14) | 301..308 marcadas por la 004 (C-02, TASK-445): circuit breaker por tenant, nivel → modelo desde settings, ventana de 20 mensajes. Sin resumen continuo, perfil de contacto ni panel de `model_policies` |
| 004 Motor de ventas | Fases A–E construidas (2026-10-14) | Milestones A, B y C aprobados; D (derivación, seguimientos, breaker) y E (aislamiento, evals de flujos, e2e, aceptación) hechos y verificados con el modelo real (`milestones/aceptacion.md`). Falta: TASK-459 número real (al final, por decisión de Giannier) y el merge |
| 005 Catalog / 008 Inbox | Absorbidas | Catálogo CRUD+CSV/XLSX en 001 (TASK-011); inbox absorbido por 007 |
| 006 Conocimiento avanzado | Construida (2026-10-14) | TASK-601..604 `[x]`: editar y reindexar sin estados a medias, dedup, OCR de escaneados con el modelo de visión, lectura de la web con protección SSRF, prioridad de fuentes, citas en simulador y bandeja, recall@5 en los evals, pantalla `/knowledge` (`006/milestones/conocimiento.md`). Para ratificar: costo del OCR (C-105). Web real de un cliente: con el número |
| 007 Inbox | Construida (2026-10-14) | TASK-701..708 `[x]`: toma exclusiva, canal en vivo (WebSocket), API y pantalla `/bandeja`, búsqueda, auto-devolución, correo de cola desatendida; e2e de dos operadores (`007/milestones/bandeja.md`). Para ratificar: Web Push (C-101, dependencia) y borrado de contacto (C-102, datos personales). La prueba con ayudante y número real va con la TASK-459 |
| 009 Billing | Construida, pendiente de revisión humana (2026-10-15) | TASK-901..909 `[x]` con las decisiones de Giannier: precios de la spec en tabla, **pagos manuales** (cobro con referencia, el admin registra la transferencia o Yape), renovación con cobro pendiente y gracia de 5 días, cambios de plan con prorrateo, paquetes, cupones, comprobante PDF, ledger de solo agregar; pantallas `/plans` y `/admin/cobros` (`009/milestones/cobro.md`). Mercado Pago, después. La Constitution exige revisión línea por línea antes del merge |
| 010 Dashboard | Construida (2026-10-15) | TASK-1001..1005 `[x]`: métricas por día local con rollup reconciliado, costo por traza con precios versionados, `/resultados` (operador sin dinero, CSV) y `/admin/metricas` (MRR, conversión, margen por negocio, operación), purga de retención (`010/milestones/dashboard.md`). Precios de modelos: los carga el admin |
| 011–016 (Fase 2) | No iniciado | Docs en estado "Draft para ratificación"; ningún código |

## 2. Cambios respecto a la spec

- **LLM por defecto: NVIDIA a OpenAI.** `nvidia/nv-embedqa-e5-v5` murió el 25/8/2026 (HTTP 410). Cambio con migración `0006` (modelo de embedding por chunk) y `make reindex`. Commit `2d1d76b`.
- **Regla del teléfono en trial.** `trial_phone_locks` nunca liberaba el número; se eliminó (migración `0013`). Ahora se consulta el estado vivo y se retiene en `trial` o `paused`. Es un desvío deliberado de la lectura literal de la Clarification 005.
- **Debounce.** Se usó un marcador Redis `SET NX EX 8` en vez del sorted set del plan. La ventana es fija, no reiniciable.
- **Regrupación de tasks.** Upsert de contacto, conversación y guardado del mensaje pasaron de TASK-204 a TASK-203.
- **Catalog guard** (no estaba en la spec). Post-validación de nombres de producto con un reintento y respuesta segura. Migración `0012`, decisión aprobada por Giannier (`001/plan.md`, "Decisiones Posteriores").
- **Prompt, regla 5.** Prohíbe confirmar acciones que no existen. Commit `945131a`, nacido de la primera conversación real.
- **Campos nuevos:** `unsupported_fallback`, `opt_out_keywords`, aviso de cuota 80/100% (migraciones `0008`, `0010`, `0011`).
- **Rate limit de auth:** guarda por IP más contador por cuenta, distinto del 5/min original. Los logs guardan un digest, no el email.
- **Proveedores de desarrollo:** email y OTP con proveedor `console` para recorrer el flujo sin Resend.

## 3. Arquitectura real

- **Backend:** FastAPI + SQLAlchemy 2 (psycopg3), Alembic, ARQ (worker) y Redis. Postgres 16 con pgvector; los archivos (fotos, comprobantes, documentos) también viven en Postgres y se sirven por enlaces firmados (`STORAGE_BACKEND`, ver `leo-sdd/features/001-onboarding/plan.md`).
- **Frontend:** Next.js 15.5 con React 19, Tailwind 4 y Playwright (6 specs).
- **Compose dev** (`deploy/docker-compose.dev.yml`): postgres, redis, backend, worker, frontend. Los contenedores `leo-dev-*` estaban activos al medir.
- **WhatsApp:** YCloud. Adaptador `providers/whatsapp/ycloud.py` (envío, parseo, firma HMAC) y webhook `POST /webhooks/ycloud`. Meta Cloud API directo y Tech Provider no existen aún.
- **LLM:** perfil `openai` por defecto, chat `gpt-5.4-mini`, embeddings `text-embedding-3-small`. Alternativa: `offline` (solo desarrollo). El perfil `nvidia` se eliminó en el Milestone B de la 004 a pedido de Giannier. Los tenants pueden traer sus credenciales de chat (`provider_for_tenant`).
- **Secretos:** credenciales cifradas con Fernet en BD. No se inspeccionó ningún `.env`.

## 4. Modelo de datos (13 migraciones, `0001`–`0013`)

| Tabla | Campos clave |
|---|---|
| `plans` | límites: `max_seats`, `max_ai_messages`, `max_rag_chars`, `max_knowledge_files`, `trial_days`, `price_usd_month` |
| `tenants` | `name`, `status`, `plan_id`, `trial_ends_at`, `ai_messages_used`, `paused_at`, `archived_at` |
| `users`, `tenant_users`, `invitations` | rol/estado por tenant; invitación con `token_hash`, `expires_at` |
| `otp_codes`, `email_tokens`, `refresh_tokens` | familias de refresh rotativo |
| `business_profiles`, `agent_configs` | perfil, moneda, horarios; `agent_name`, `tone`, `restrictions`, `is_active`, `unsupported_fallback`, `opt_out_keywords` |
| `products`, `payment_methods_business` | `price`, `stock`, `is_active`, `deleted_at`; método e instrucciones |
| `knowledge_sources`, `knowledge_chunks` | `status`, `embedding` (vector), `embedding_model` |
| `whatsapp_numbers`, `connection_requests` | `credentials_enc`, `quality_rating`; flujo de aprobación |
| `contacts`, `conversations`, `messages` | `opted_out_at`, `last_inbound_at`; `direction`, `delivery_status`, `provider_message_id` |
| `webhook_events`, `message_status_events` | evento crudo, `attempts`, `status` (incluye dead-letter). `webhook_events` no lleva `tenant_id`, a propósito |
| `agent_traces` | `tools_called`, `rag_sources`, tokens, `latency_ms`, `catalog_guard` |
| `tenant_notifications`, `tenant_llm_settings`, `audit_logs` | avisos, LLM propio del tenant, auditoría de plataforma |

## 5. Resultados de pruebas (ejecutadas hoy)

| Verificación | Resultado |
|---|---|
| `pytest backend/tests` (equivale a `make test`) | **1377 passed**, 3 deselected (slow/evals), 0 fallos, 3m21s |
| Suite de aislamiento (`backend/tests/isolation`) | **174 passed** |
| `ruff check` / `ruff format --check` | limpio / 217 archivos formateados |
| `mypy backend/app` | sin errores (113 archivos) |
| `make coverage` | total **94%**. Gate: agent-core 96.75% (mín. 80), canales 96.85% (80), billing 91.91% (80), resto 93.20% (70). Cumple |
| Frontend `eslint` y `tsc --noEmit` | sin errores |
| Evals del agente (`make evals`) | **No ejecutados**: requieren modelo real y consumen créditos. Resultado no medido |
| Playwright e2e, `next build` | No ejecutados |
| Webhook YCloud y envío con proveedor real | No verificado (nadie ha probado con número real) |

- **Cobertura baja puntual:** `workers/channel.py` 56% y `workers/settings.py` 64%. El gate agrupa por módulo y no lo marca.
- **Fixes recientes (bugs ya corregidos):** reintento de trial imposible (`bfe6b48`), perfil por defecto con embedding muerto (`2d1d76b`), cuatro defectos de respuesta del agente (`945131a`), nombre de producto casi correcto (`ae7703e`), paso 4 del panel sin aviso de conocimiento (`b9c949c`), tool-calls visibles al cliente (#34).
- **Límites asumidos del catalog guard** (`001/plan.md`): no detecta productos inventados enteros en minúsculas; los nombres capitalizados fuera del vocabulario solo se vigilan en evals.
- **Deuda anotada:** no hay TODOs en `backend/app` (los 2 hallazgos de `XXX` son texto de ejemplo).

## 6. Pendientes y decisiones abiertas

- **Cobro de la plataforma apagado** (`BILLING_ENFORCED=false`, 2026-10-13): prueba que no vence, cupo
  sin aplicar, sin límites de asientos ni de conocimiento, un teléfono puede abrir varios negocios. Se
  enciende antes de vender (deploy, TASK-026 de la 001).

1. **Milestone TASK-209 (Giannier):** probar de punta a punta con su número real. Requiere configurar `YCLOUD_WEBHOOK_SECRET` y apuntar el webhook a `/webhooks/ycloud`. Es lo único que mide el p95 <10s.
2. **TASK-026:** deploy a producción (compose prod, Caddy, backups con restore probado, Sentry/Uptime Kuma, runbook). Milestone: Giannier ejecuta el restore.
3. **Milestones 1–5 de 001:** no hay registro en el repo de que se confirmaron. No verificado.
4. **Feature 003:** reconciliar `tasks.md` (301–308 sin marcar) con lo ya construido. Decidir si el circuit breaker, la memoria y `model_policies` entran en el MVP.
5. **Rama:** `task-025-hardening-and-coverage` trae los commits 2xx y fixes; decidir PR y merge a `main`. Existe también `fix/agent-reply-defects-from-the-first-real-conversation`.
6. **Meta Tech Provider:** `post-mvp/README.md` fija "antes del 15-oct-2026" para el trámite. Requiere decisión de Giannier.
7. **Evals:** correr `make evals` tras los cambios de prompt (regla 5). El CLAUDE.md exige adjuntar el resultado al PR.
8. **MVP restante** (orden del CLAUDE.md): 004 → 006 → 007 → 009 → 010. Fase 2 solo con ratificación por feature.
