# Plan Técnico — Onboarding de Tenant + Conexión de Número

**Feature ID:** 001
**Fecha:** 2026-07-30

## Constitution Check ✓
- ✅ Stack: Python 3.12 + FastAPI + PostgreSQL 16/pgvector + SQLAlchemy/Alembic + Redis + Next.js 15 + Docker Compose en Contabo — coincide.
- ✅ Principios: configuración sobre código (wizard escribe entidades, no código); aislamiento por `tenant_id` en repositorios; `WhatsAppProvider`/`LLMProvider` como únicas puertas a terceros; agent-core v0 ya nace con registro de tools.
- ✅ Seguridad: JWT+refresh, cifrado de credenciales del número, audit log de acciones admin, rate limiting, Ley 29733 (retención/archivado).
- ✅ Testing: 80% agent-core/channels, 70% resto; suite de aislamiento multi-tenant; contract tests de adaptadores.
- ⚠️ Excepción consciente: los evals de agente en CI se introducen aquí en versión semilla (5 conversaciones doradas del rubro de prueba) y se expanden en la Feature 003 (agent-core completo). Motivo: el dataset por rubro necesita las tools de media/visión que aún no existen.

## Arquitectura

### Patrón Elegido
**Monolito modular con capas (API → Services → Repositories → DB) + Ports & Adapters para proveedores externos.** Justificación: máxima simplicidad operativa para un equipo de 1 (Constitution), testabilidad por capa, y los adaptadores (`LLMProvider`, `WhatsAppProvider`, `EmailProvider`, `StorageProvider`) aíslan todo lo intercambiable.

### Diagrama de Componentes (texto)
```
Next.js (panel tenant + panel admin)
        │ HTTPS (Caddy)
        ▼
FastAPI (routers → deps de auth/roles)
        │
   Services (Onboarding, Checklist, Team, Connection, SandboxChat, KnowledgeIngest, CatalogImport, TrialLifecycle)
        │                                    │
   Repositories (todos exigen tenant_id)     ├─ LLMProvider ──► API del LLM
        │                                    ├─ EmailProvider ─► email transaccional
        ▼                                    ├─ StorageProvider ► MinIO (S3)
PostgreSQL 16 + pgvector                     └─ WhatsAppProvider ► YCloud (v1: registro
        ▲                                        de credenciales; operación manual admin)
        │
ARQ workers (Redis): ingesta RAG, emails, jobs de trial (avisos/pausa/archivado)
```

### Decisiones Técnicas
| Decisión | Opción elegida | Razón | Alternativa descartada |
|----------|---------------|-------|----------------------|
| Estructura de repos | Monorepo (`backend/`, `frontend/`, `deploy/`) | Un solo dev, un solo despliegue, PRs atómicos | Repos separados (overhead de sincronización) |
| Cola de trabajos | ARQ (async, nativo Redis) | Ligero, async-first, encaja con FastAPI; Celery es excesivo para un dev | Celery (+ complejidad de config/monitoring) |
| Auth | JWT access (15 min) + refresh rotativo (30 días) en cookie httpOnly | Estándar, sin estado de sesión en BD salvo refresh tokens revocables | Sesiones server-side (más estado), Auth0 (dependencia y costo) |
| Cifrado de credenciales | Cifrado a nivel de aplicación (Fernet, clave en env) sobre columna `bytea` | Simple, portable, clave fuera de BD | pgcrypto (clave viaja en SQL), Vault (infra extra) |
| Multi-tenancy | Columna `tenant_id` + repos que la exigen + tests de aislamiento | Simplicidad operativa; RLS de Postgres queda como refuerzo futuro | Schema-per-tenant (migraciones ×N, contra "equipo de 1") |
| Sandbox vs real | Mismas tablas `conversations`/`messages` con `channel: sandbox|whatsapp` | Un solo código de agente y de historial; el sandbox prueba el sistema real | Tablas sandbox aparte (duplicación) |
| Checklist | Servicio de cálculo en tiempo real | Fuente de verdad = estado de entidades (Clarification 009) | Flags almacenados (desincronización) |
| Import CSV | Preview + confirmación; parcial con reporte de errores | UX de mercado (HU-02) | Todo-o-nada (frustrante con datos reales) |
| Embeddings RAG | Vía `LLMProvider.embed()` (proveedor configurable) | Mismo principio de intercambiabilidad que el chat | Librería local (calidad/一 mantenimiento) |
| Frontend data layer | TanStack Query + fetch tipado (OpenAPI generado) | Contratos sincronizados con el backend sin escribir tipos a mano | Redux (excesivo), SWR (menos features de mutación) |

## Dependencias Nuevas (requieren aprobación — Constitution)
| Librería | Propósito | Por qué esta |
|----------|-----------|--------------|
| arq | Workers/colas sobre Redis | Async nativo, mínima config |
| pydantic-settings | Config tipada por env | Estándar FastAPI |
| passlib[argon2] | Hash de contraseñas | Argon2 recomendado actual |
| python-jose o pyjwt | JWT | PyJWT preferido (más simple) |
| cryptography (Fernet) | Cifrado de credenciales | Estándar de facto |
| boto3 | Cliente S3/MinIO | Estándar S3 |
| pypdf + tiktoken (o equivalentes) | Extracción de texto PDF y chunking para RAG | Mínimos suficientes para v0 |
| Resend (SDK o SMTP) | Email transaccional | API simple; **pendiente de aprobación** (Open Question) |
| Frontend: @tanstack/react-query, react-hook-form, zod | Data fetching, formularios del wizard, validación | Estándares del ecosistema |

## Data Model
Ver `data-model.md`. Entidades principales:
- `users`, `tenants`, `tenant_users`, `invitations`, `otp_codes` — identidad, membresías y verificación.
- `plans` — límites por plan (incluye el plan `trial`): asientos, mensajes IA, caracteres RAG.
- `business_profiles`, `agent_configs`, `products`, `payment_methods_business` — configuración del wizard.
- `knowledge_sources`, `knowledge_chunks` (pgvector) — RAG.
- `whatsapp_numbers`, `connection_requests` — canal.
- `conversations`, `messages`, `agent_traces` — chat (sandbox y, a futuro, real).
- `audit_logs` — acciones de plataforma.

## Contratos de API
Ver `contracts/api.md`. Grupos: `/auth`, `/tenant` (perfil, agente, productos, conocimiento, pagos, checklist, activación), `/tenant/team`, `/tenant/connection-requests`, `/tenant/sandbox`, `/platform-admin/*`.

## Estrategia de Testing
- **Unit:** servicios (checklist con sus 6 ítems y combinaciones; lifecycle del trial; validaciones de import CSV; límites RAG; máquina de estados de solicitudes).
- **Integration (BD real, testcontainers o compose de test):** flujo registro→verificación→wizard→sandbox; flujo solicitud→connected; invitaciones y revocación de sesiones; expiración de trial con jobs.
- **Suite de aislamiento multi-tenant:** fixture con 2 tenants; por cada repositorio, intento de lectura/escritura cruzada debe fallar. Obligatoria en CI (Constitution).
- **Contract tests:** `LLMProvider` (mock + uno real marcado `@slow`), `EmailProvider`, `StorageProvider`.
- **Evals semilla:** 5 conversaciones doradas (rubro restaurante demo) contra agent-core v0; aserciones de contenido (menciona precio correcto del catálogo, no inventa productos, escala cuando corresponde).
- **E2E ligero (Playwright):** registro → wizard completo → mensaje en sandbox con respuesta del agente.

## Riesgos y Mitigaciones
| Riesgo | Prob. | Impacto | Mitigación |
|--------|-------|---------|-----------|
| Latencia/costo LLM en sandbox ilimitado | Media | Medio | Modelo económico por default, rate limit 30/min, trazas con conteo de tokens desde el día 1 |
| Cuello de botella operativo en conexión asistida (solo Giannier) | Alta | Medio | SLA interno 24–48h visible al cliente; runbook de conexión; iniciar trámite Meta Tech Provider en paralelo |
| Abuso de trials (cuentas descartables) | Media | Medio | OTP de teléfono (Clarification 005), unique de teléfono en trial, métricas de abuso |
| Ingesta RAG de PDFs "sucios" (escaneados) | Alta | Bajo | v0 solo extrae texto embebido; PDFs sin texto se marcan `failed` con mensaje claro (OCR queda para Feature 006 completa) |
| Divergencia sandbox vs canal real futuro | Media | Alto | Mismo agent-core, mismas tablas, mismo pipeline; solo cambia `channel` |
| Cambios de precios/planes durante desarrollo | Media | Bajo | Límites en tabla `plans`, nunca hardcodeados |

## Decisiones Posteriores

### 2026-09-08 — Post-guard de nombres de catálogo en agent-core (TASK-025)

**Problema.** El catálogo decía "Leche" y el agente ofreció "Leche pausterizada".
La regla 1 del prompt ("Nunca inventes productos") ya lo prohibía y no lo evitó:
un nombre *casi* correcto no le parece inventado al modelo. Para el cliente sí lo
es — llega al negocio a pedir algo que no se vende. La única detección existente
vivía dentro de los evals (`backend/evals/dataset.py`), corría solo contra el
fixture demo y nunca en producción; además reconocía el descriptor por una lista
blanca de terminaciones, que capturaba "helada" y "pausterizada" y dejaba pasar
"fresca", "entera" y "light" — el mismo error con otro sufijo.

**Decisión (aprobada por Giannier).**

| Punto | Elegido | Razón | Alternativa descartada |
|-------|---------|-------|----------------------|
| Dónde vive el detector | `backend/app/agent/catalog_guard.py`, código de producción; los evals lo importan | Una sola fuente de verdad: una copia privada en `evals/` se desviaría, y la que se desviaría es la que protege a clientes reales | Copia en los evals (deriva garantizada) |
| Cómo conoce el catálogo | Los nombres son un **parámetro**; el eval pasa `DEMO_PRODUCTS`, el runtime pasa los del tenant | El detector no debe saber de dónde vienen; `app/` nunca importa de `evals/` | Detector acoplado al fixture |
| Lectura del catálogo | `ProductRepository.active_names()` — filtrado por `tenant_id` en la capa de repositorio, solo activos y no borrados | Constitution: toda query de negocio filtra por tenant en el repositorio. Un producto desactivado ya no se vende, y citarlo genera un pedido que no se puede cumplir | Reusar la salida de `consultar_catalogo` (viene truncada y filtrada por la búsqueda del modelo ⇒ productos reales parecerían inventados) |
| Costo/latencia | Una sola lectura por turno, perezosa: `_enforce_catalogue_names` construye el guard una vez y juzga con él la respuesta original y la reintentada | A6 (latencia media 3364 ms) ya es un problema; un turno que nunca produce respuesta usable no paga la lectura | Consultar por cada verificación |
| Detección del descriptor | Lista blanca de terminaciones **reemplazada** por: longitud mínima + lista cerrada de palabras función y verbos del dominio + terminaciones verbales `-an/-en/-ando/-iendo` | Las palabras que siguen a un nombre de producto *en prosa* son clases cerradas y sí se pueden enumerar; los adjetivos del español no | Ampliar la lista de sufijos (deja el mismo hueco con otro sufijo) |
| Al detectar | **Un** reintento con instrucción correctiva que le nombra los nombres exactos (`build_name_correction`, en `prompts/builder.py`); si reincide, respuesta segura que deriva a una persona | Repetir "no inventes" no cambia nada — ya está en la regla 1; darle las cadenas a copiar sí. Un segundo intento tras ver la lista es un modelo que no va a cumplir, y cada intento más son segundos de "escribiendo..." | Bloquear a la primera (pierde respuestas recuperables) o reintentar N veces (costo y espera) |
| Trazabilidad | Columna `agent_traces.catalog_guard` (JSONB, nullable; migración `0012`), expuesta en la traza del sandbox | El dueño necesita distinguir "el modelo renombró un producto y se corrigió" de "el modelo respondió normal". Nullable a propósito: un valor no nulo *es* la señal | Meterlo en `tools_called` (contamina un contrato ya definido) |

**Riesgo principal: falsos positivos.** Un guard que dispara sobre una respuesta
correcta la degrada a una disculpa y enseña a todo el mundo a desactivarlo, que
vale menos que no tener guard. Por eso el detector solo reporta frases
*ancladas* en el catálogo del propio tenant, la tabla de prosa en
`backend/tests/unit/test_catalog_guard.py` es más larga que la de renombres, y
un catálogo vacío o mayor que `MAX_GUARDED_CATALOG_NAMES` hace que el guard se
**abstenga** en lugar de juzgar con datos incompletos.

**Límites conocidos, asumidos a conciencia.**
1. Un nombre inventado **entero y en minúsculas** ("tenemos sushi variado") no se
   detecta en runtime. Detectarlo exigiría marcar cualquier palabra en minúscula
   que el tenant no haya escrito, y en prosa española eso es casi todo
   ("pedido", "delivery", "promoción", "rápido"): sería un falso positivo por
   respuesta correcta. La mitad cara de ese caso — un **precio** inventado sin
   negación — la siguen capturando los evals de forma estructural.
2. La mitad "nombre introducido de la nada" (palabra capitalizada fuera del
   vocabulario del tenant) solo corre en los evals, que tienen el fixture
   completo. En runtime el agente conoce su catálogo y nada más, así que
   dispararía contra "Miraflores", "Yape" o el nombre del propio cliente.
3. `-an/-en` como terminación verbal cuesta descriptores como "virgen" o
   "joven". Es una omisión, no una falsa alarma, y ese es el lado por el que
   este código se equivoca a propósito.

**Verificación.** `make test`, `make isolation`, `make lint` y `make coverage` en
verde; la suite de aislamiento comprueba que el guard juzga contra el catálogo de
su propio tenant y contra ningún otro. Cambio de prompt ⇒ `make evals` adjunto al
PR (Constitution).

## Estimación
- **Esfuerzo total:** 3–4 semanas calendario supervisando a Claude Code (≈ 26 tasks de 1 sesión c/u; varias paralelizables).
- **Complejidad:** Alta (es la feature fundacional: define auth, multi-tenancy, adaptadores y el esqueleto del agente para todo lo demás).
