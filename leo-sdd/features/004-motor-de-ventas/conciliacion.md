# Conciliación — Motor de ventas (004 v2) frente al repo real

**TASK-401** · Rama `task-025-hardening-and-coverage` · Revisado el 2026-10-10 · Solo lectura: no se tocó código, ni la 003, ni la 004 vieja.

Regla de precedencia (README, punto 5): si el repo resuelve algo distinto, manda el repo. Si la documentación de YCloud contradice `contracts/api.md §8`, manda YCloud.

---

## 1. Qué existe hoy del agente

| Pieza | Ruta | Estado real |
|---|---|---|
| Core | `backend/app/agent/core.py` | `AgentCore.respond(conversation_id, customer_message: str) -> AgentReply`. Bucle de tools con `MAX_TOOL_ROUNDS = 2` (la 3.ª vuelta no ofrece tools), `HISTORY_TURNS = 30` mensajes, caché de tools por turno (`_ToolCache`), `FALLBACK_REPLY` si hay `LLMError`, limpieza de sintaxis de tool-call (`_TOOL_BLOCK`), `whatsapp_text()` (Markdown a WhatsApp) y `_tidy()`. Persiste traza con `persist_trace`. |
| Prompt builder | `backend/app/agent/prompts/builder.py` | Función pura `build_system_prompt(profile, config, tool_names, now)`. Usa tono, objetivo (`vender/atender/ambos`), `restrictions`, `escalation_rules`, horarios y fecha. Regla 5 prohíbe registrar, reservar, agendar o cobrar. También `build_name_correction()` para el guard. **No** es `agent/prompt_builder.py` como dice el plan. |
| Registry | `backend/app/agent/tools/registry.py` | `ToolRegistry` (register/specs/get/names), `ToolContext(session, tenant_id, llm)`, handlers que devuelven `str`. Ya existe: el plan lo marca "NUEVO". |
| Tools | `backend/app/agent/tools/catalog.py` | Solo 2: `consultar_catalogo` (ILIKE por nombre, máx. 12, moneda `S/` fija) y `consultar_conocimiento` (RAG, máx. 4 chunks). `build_default_registry()`. |
| Trazas | `AgentTrace` en `backend/app/models/conversation.py`; `AgentTraceRepository` | `model`, `tools_called` (`tool`, `args_resumen` de 120 caracteres, `ok`, `cached`), `rag_sources`, tokens, `latency_ms`, `catalog_guard`. Sin etapa, costo, iteraciones ni versión de prompt. |
| Catalog guard | `backend/app/agent/catalog_guard.py` + `_enforce_catalogue_names` en `core.py` | Post-guard con un reintento y `CATALOG_GUARD_REPLY`. Nombres desde `ProductRepository.active_names` (tope 1000; sobre eso se desactiva). Sin vocabulario en runtime: solo detecta nombre de catálogo más descriptor. Migración `0012`. |
| Canal | `backend/app/services/channel.py`, `backend/app/workers/channel.py` | Webhook, debounce Redis, lock por conversación, `ConversationResponder.respond` (genera **una** respuesta de texto `queued`), `ReplySender.send` (un `send_text`, reintentos, guard de ventana de 24 h), barrido de respuestas varadas. |
| Simulador | `backend/app/services/sandbox.py`, `backend/app/api/v1/sandbox.py`, `frontend/app/(panel)/sandbox/page.tsx` | Mismo `AgentCore`, canal `sandbox`, sin contacto, rate limit por tenant, no mide cuota. |
| Evals | `backend/evals/` (`dataset.py`, `runner.py`, `test_golden_conversations.py`) | Un restaurante demo con 5 conversaciones doradas en dataclasses Python (no YAML). CI las corre al cambiar el agente (`c59affa`). |
| LLM | `backend/app/providers/llm/` | `LLMProvider.chat(messages, tools, temperature, max_tokens)` y `embed`. `ChatMessage.content` es `str`: **sin entrada de imagen, sin salida estructurada, sin modelo por llamada**. Un solo `chat_model` por perfil. |

Verificado además: no existen `circuit`, `model_polic*`, `prompt_versions`, `contact_profiles`, resumen móvil ni tool de escalamiento en `backend/app` (solo comentarios en `core.py`).

---

## 2. Tasks 301–308 de la 003

Evidencia: lectura de `backend/app/`, migraciones `0001`–`0013`, `git log` (no hay commits `TASK-3xx`; solo `feat(agent): ... [TASK-014]`, `[TASK-016]` de la 001).

| Task 003 | Hecho hoy | Falta | Lo absorbe la 004 v2 | Marcar |
|---|---|---|---|---|
| 301 Migraciones (`contact_profiles`, `model_policies`, `prompt_versions`, ALTERs) | Nada | Todo | Parcial: `plans.allowed_intelligence` + tabla de niveles en settings (0016, TASK-445). `conversation_state` reemplaza los ALTERs a `conversations`. **No absorbe** `contact_profiles` ni `prompt_versions` (la versión vive en el código de la plantilla). | `[ ]` |
| 302 LLM compuesto + circuit breaker | `provider_for_tenant` (LLM propio del tenant) y perfiles `openai`/`nvidia`/`offline`. Sin fallback ni breaker. | Primary/fallback, breaker, alertas | Breaker mínimo y `model_policy.py` (TASK-445). Sin primary/fallback. | `[ ]` |
| 303 Prompt builder por rubro | Parcial: `builder.py` con contexto dinámico (fecha, horario, moneda), tono, restricciones. Cubre la mitad "contexto dinámico". | Plantillas Jinja2, 6 rubros, overrides, versionado con checksum | TASK-409/414 (plantillas por proceso, no por rubro; sin Jinja2). | `[~]` |
| 304 Memoria (resumen móvil + perfil) | Solo ventana de 30 mensajes (`HISTORY_TURNS`). | Resumen, perfil de contacto, jobs | **No.** C-02 fija ventana y hechos, sin resumen. | `[ ]` |
| 305 Guardrails + escalamiento | Parcial: post-guard (`catalog_guard`), tope de rondas, errores de tool como texto, limpieza de tool-call. | Pre-guards, `escalar_a_humano`/`needs_human`, heurísticas de frustración | `derivar_a_humano` (TASK-443). Sin heurísticas de frustración. | `[~]` |
| 306 Trazas enriquecidas + costo | Parcial: modelo, tools, fuentes, tokens, latencia, `catalog_guard`. | Iteraciones, presupuesto, versión de prompt, costo estimado | Etapa, embudo, tools habilitadas, salientes, nivel (0020). Sin costo. | `[~]` |
| 307 Runner de evals + datasets por rubro | Parcial: runner y 5 casos de un rubro; job CI. | YAML, 15 × 3 rubros, umbral 90 % | TASK-447 (24 conversaciones por plantilla, formato por definir). | `[~]` |
| 308 Admin de `model_policies` + canal real | Mitad hecha: el agente ya corre en canal real (002 TASK-205). | CRUD con auditoría, panel, smoke de failover | **No.** Los niveles salen de settings y del plan; no hay panel de políticas. | `[~]` |

**Propuesta para la 003 (no editada):** hoy no corresponde `[x]` a ninguna. Marcar `[~]` en 303, 305, 306, 307 y 308 con una nota de lo que falta. Al cerrar TASK-445/443/447 se revisa de nuevo; aun así 301 (parte), 304 y 308 (panel) quedan fuera y hay que decidir si se descartan o pasan a post-MVP. TASK-445 dice "tareas 301–308 de la 003 marcadas": no podrá marcarlas todas en `[x]` sin mentir.

---

## 3. Diferencias entre `data-model.md` / `contracts/api.md` y el repo

### 3.1 Esquema

| Tema | Spec | Repo | Acción |
|---|---|---|---|
| Última migración | 0014 en adelante | Última `0013` (`down_revision = "0012"`). Revisiones de 4 dígitos como `str`, archivo `NNNN_slug.py`, docstring con `Revision ID`/`Create Date`. | Confirmado: la nueva cadena parte de `down_revision = "0013"`. |
| Tipo de PK | `uuid` con `gen_random_uuid()` | Igual (`UUIDPrimaryKeyMixin`, `server_default gen_random_uuid()`). Excepciones: `plans.id` es texto; `business_profiles`/`agent_configs` usan `tenant_id` como PK. | Sin cambios. PK compuestas de `business_info` y `tenant_sequences` encajan. |
| Mixins y checks | `created_at`/`updated_at` con `now()`; `CHECK` en línea | `TimestampMixin` (`onupdate clock_timestamp()`), `enum_check(...)`. `NAMING_CONVENTION` antepone `ck_<tabla>_` e `ix_<tabla>_<cols>`. | Declarar índices y checks en el modelo con nombres cortos (p. ej. `identity`, no `ck_contacts_identity`) para no duplicar el prefijo y mantener limpio el autogenerate. |
| `ON DELETE CASCADE` en `tenant_id` | En todas las tablas nuevas | Casi ninguna FK del repo lo usa (4 apariciones en todas las migraciones; `tenants.id` sin cascade en `conversations`, `messages`, `products`). | **Decisión de Giannier**: seguir el repo (sin cascade) o cascade solo en tablas nuevas. La Constitution pide "eliminación por tenant"; hoy no hay ruta de borrado uniforme. |
| Orden de eventos | `conversation_stage_events.created_at DEFAULT now()` | `messages` usa `clock_timestamp()` porque `now()` empata dentro de una transacción. | Usar `clock_timestamp()`: varios eventos de etapa pueden caer en la misma transacción de un turno. |
| `contacts` teléfono | `ALTER ... phone DROP NOT NULL`, `ck_contacts_identity` sobre `phone` | La columna se llama **`phone_e164`** (NOT NULL), con índice único `uq_contacts_tenant_id_phone_e164 (tenant_id, phone_e164)`. No hay `wa_id`. | 0020 debe apuntar a `phone_e164`. `external_user_id` y `custom_data` no existen: sin choque. |
| `contacts.last_inbound_at` | C-09 dice `conversations.last_inbound_at` | Está en **`contacts.last_inbound_at`**; la ventana es `session_window_is_open(contact, now)` en `repositories/channel.py`. | Corregir C-09/`ConversationWindow`: reutilizar esa función. |
| `contacts.upsert` | Upsert por BSUID o teléfono | `ContactRepository.upsert(phone_e164, profile_name)`; el parser exige `from` y `to`; `ReplySender` envía a `contact.phone_e164`. | TASK-408 cambia repositorio, parser y sender a la vez. |
| Conversación sandbox | Contacto sintético `sandbox:{user_id}`; `orders.contact_id NOT NULL` | `conversations.contact_id` es nullable y el sandbox **no tiene contacto**. | Crear contacto sandbox (con `phone_e164` nulo permitido por 0020) o hacer `orders.contact_id` nullable. Los contactos sandbox aparecerán en listados de contactos: filtrarlos. |
| Marca sandbox | `is_sandbox` en `conversation_state` y `orders` | `conversations.channel = 'sandbox'` ya lo dice. | `conversation_state.is_sandbox` es redundante (derivable); en `orders` sirve como columna desnormalizada para el índice. |
| `agent_configs` | 10 columnas nuevas | Ninguna choca. Ya existen `tone`, `objective`, `restrictions`, `escalation_rules`, `is_active`, `unsupported_fallback`, `opt_out_keywords`. | `objective` (`vender/atender/ambos`) y `escalation_rules` se solapan con plantilla y `derivar_a_humano`: decidir si se retiran del prompt o se mantienen. |
| `products` | `ADD COLUMN IF NOT EXISTS category` | `category` ya existe. Existen también `image_key` y `file_key`. | No agregar `category`. Alembic `add_column` no tiene `IF NOT EXISTS`: omitirla. `image_key`/`file_key` duplican `media_assets.product_id`: definir cuál manda para `enviar_recurso`. |
| `payment_methods_business` | `media_asset_id` | No existe; `validate_receipts` ya existe y dice "enciende la tool de visión de la Feature 004". | Sin choque. Decidir si `validate_receipts` condiciona el `ProofPipeline` (RF-022 lo ignora). |
| `plans` | `allowed_intelligence text[]` | Sin choque. | OK. |
| `agent_traces` | 5 columnas | Sin choque. | OK. |
| `tenant_notifications` | "Tipos nuevos" para `nuevo_comprobante`, `derivacion`, `llm_caido`... | Tabla de **avisos de una sola vez**: PK `(tenant_id, kind)`, sin payload, CHECK `tenant_notifications_kind` con 5 valores (`trial_*`, `wizard_reminder`, `quota_80`). | **Incompatible.** No puede alojar eventos repetidos ni el aviso "ventana cerrada" por pedido. Hace falta una tabla nueva (migración extra) o rediseñar. |
| `messages` | Sin cambios en 0014–0020 | Sin `media_key`, sin payload, `MESSAGE_TYPES` sin `interactive` ni `video`. Entrantes de imagen se guardan como `"[imagen]"`. | **Falta una migración** para salientes tipados (imagen, botones, lista) y para la imagen entrante (`payment_proofs.message_id/storage_key` la presuponen). |
| Tablas nuevas | `conversation_state`, `conversation_stage_events`, `business_info`, `faqs`, `promotions`, `process_stage_overrides`, `media_assets`, `tenant_sequences`, `orders`, `order_items`, `payment_proofs` | Ninguna existe. | Sin choques de nombre. Cada una necesita repositorio que herede `BaseRepository` (falla al importar si el modelo no tiene `tenant_id`) y caso en `tests/isolation`. |
| Moneda | `orders.moneda char(3)` | `business_profiles.currency` (`PEN`), pero `consultar_catalogo` imprime `S/` fijo. | Corregir en TASK-420. |

### 3.2 API

| Tema | Spec | Repo |
|---|---|---|
| Prefijo | `/api/v1/assistant/...`, `/orders`, `/process`, `/conversations`, `/sandbox` | `/api/v1` (`settings.api_v1_prefix`) y los routers de tenant cuelgan de **`/tenant/...`** (`/tenant/products`, `/tenant/knowledge`, `/tenant/sandbox`, `/tenant/agent`). Propuesta: `/tenant/assistant/*`, `/tenant/orders/*`, `/tenant/process/*`, `/tenant/conversations/*`. |
| Simulador | `POST /sandbox/messages`, `GET /sandbox/messages?after=`, `POST /sandbox/reset` | Basado en conversación: `POST /tenant/sandbox/conversations`, `POST .../{id}/messages` (cuerpo `{content}`), `GET .../{id}/messages`. Hay que extender esos, no crear los de la spec. |
| Errores | `{code, ...}`; 422 `validation_error`; 403 `plan_limit` | `{"error", "message", "fields"?}` (`ApiError`); el sandbox documenta `validation_error` con 400. Usar `ApiError`/`error_responses`. |
| Roles | `admin` / `operador` | `owner`, `tenant_admin`, `operator`; deps `MANAGES_CONFIGURATION`, `ANY_MEMBER`, `OWNS_THE_ACCOUNT`. |
| Proveedor | `send_text(to: Recipient, text)` | `send_text(credentials, *, sender, to: str, body)`. No hay `send_media/buttons/list/upload_media` ni descarga de media entrante. El parser manda `interactive` a `unsupported` y no lee `referral` ni BSUID. |
| Worker | Jobs ARQ `followups.run`, etc. | Se registran en `WorkerSettings.functions` y `cron_jobs` (`workers/settings.py`); `ctx` trae `session_factory`, `storage`, `llm`, `email`, `whatsapp`. |

### 3.3 Frontend

| Tema | Spec (`plan.md`) | Repo |
|---|---|---|
| Raíz | `frontend/src/app/(panel)/` | **`frontend/app/(panel)/`** (sin `src/`). Componentes en `frontend/components/`, cliente en `frontend/lib/api/`. |
| Rutas existentes | `/asistente`, `/pedidos` | `/onboarding`, `/catalog`, `/knowledge`, `/channel`, `/team`, `/plans`, `/sandbox`. El simulador ya es `/sandbox`, no `/asistente/simulador`. |
| Tipos de API | no se menciona | `lib/api/schema.d.ts` se genera con `npm run api:types` (openapi-typescript) contra un backend levantado: cada task de API debe regenerarlo. |
| Playwright | specs nuevas | `frontend/e2e/` con 6 specs (`auth`, `onboarding`, `navigation`, `sandbox`, `channel-team`, `admin`). |
| Dependencias | "formulario generado desde el schema" (TASK-440) | Solo `next`, `react`, `react-dom`; sin librería de formularios ni validación. Generar el formulario con JSON Schema implica una dependencia nueva: ver §5. |

### 3.4 Tools: existentes frente a las 10 declaradas

| Declarada (`§7`) | Repo hoy | Nota |
|---|---|---|
| `buscar_productos`, `ver_producto` | `consultar_catalogo` (una sola) | Dividir y **renombrar** rompe `tests/integration/test_agent_core.py` (19 menciones), `test_prompt_builder`, `test_provider_contracts`, evals y el texto de las reglas del prompt. Alternativa: conservar `consultar_catalogo` como alias o ampliar sus argumentos. |
| `consultar_conocimiento` | Existe, parámetro `consulta` (la spec usa `pregunta`) | Mantener `consulta`. |
| `enviar_recurso`, `mostrar_opciones`, `elegir_camino`, `gestionar_pedido`, `registrar_datos_cliente`, `enviar_metodos_pago`, `derivar_a_humano` | No existen | Nuevas (8 por construir, 2 existentes). `TOOL_NAMES` debe incluir la decisión sobre `consultar_catalogo`. |
| Sobre `ToolResult(status, data, decir)` | Handlers devuelven `str`; `core.py` detecta `output.startswith("ERROR:")` y `"No hay información"` | Al migrar al sobre hay que adaptar `_run_tool` y la lógica de `rag_sources`. El comentario de `catalog.py` dice que prosa rinde mejor que JSON: renderizar el sobre como texto. |

### 3.5 Ubicación de módulos

| Plan | Repo | Propuesta |
|---|---|---|
| `app/process/{templates,engine,facts,state,interactive,service}` | Capas planas: `models/`, `repositories/`, `services/`, `api/v1/`, `workers/`, `providers/`, `agent/` | Lógica pura (`templates`, `engine`, `pricing`, `formatter`, `budget`) en `app/process/` y `app/orders/`. Modelos en `app/models/`, repositorios en `app/repositories/` heredando `BaseRepository`, routers en `app/api/v1/` registrados en `api/v1/__init__.py`, jobs en `app/workers/`. Evita los `models.py`/`router.py` por módulo que el plan dibuja. |
| `agent/prompt_builder.py` | `agent/prompts/builder.py` | Extender el existente. |
| `agent/tools/registry.py` NUEVO | Ya existe | Extender: añadir `for_stage`, `ToolResult`, `cacheable`. |
| `agent/tools/{resources,options,orders,customer,payments,path,handoff}.py` | Solo `catalog.py` | Nuevos; `agent/tools/` ya cuenta como "agent-core and tools" (80 %). |
| `agent/formatter.py` | `whatsapp_text`, `_tidy`, `_usable_answer` viven en `core.py` | Mover y reutilizar, no portar de cero (TASK-416). |
| Cobertura 80 % en `process`, `orders` | `scripts/coverage_gate.py` solo tiene `agent-core and tools = app/agent/`, `channels = app/providers/whatsapp/`, `billing`; todo lo demás cae en 70 % | Agregar `app/process/` y `app/orders/` al grupo crítico (cambio de script, a aprobar). |
| `.env` / settings | Niveles de inteligencia "desde settings" | `ProviderProfile` tiene un solo `chat_model`; hacen falta variables nuevas y actualizar la referencia de entorno (`tests/unit/test_env_reference.py`). |

---

## 4. Ubicación de la carpeta

- README y tasks hablan de `specs/004-motor-de-ventas/` y `specs/_archivo/004-tools-v1/`. **No existe `specs/` en el repo**: las features viven en `leo-sdd/features/NNN-nombre/` y `CLAUDE.md` (raíz y `leo-sdd/`) lo declara así.
- La carpeta actual está en la raíz (`004-motor-de-ventas/`, sin seguimiento en git).

**Propuesta (sin ejecutar):**

| Elemento | Destino |
|---|---|
| Esta carpeta | `leo-sdd/features/004-motor-de-ventas/` |
| 004 vieja (`leo-sdd/features/004-tools/`: `spec.md`, `plan.md`; no tiene `tasks.md`) | `leo-sdd/features/_archivo/004-tools-v1/` |
| Rutas `specs/...` en `tasks.md`, `README.md`, `plan.md` | Sustituir por `leo-sdd/features/004-motor-de-ventas/...` (TASK-401, 423, 433, 442, 449 mencionan `specs/` y `milestones/`). |
| `CLAUDE.md` (raíz y `leo-sdd/`), línea "004-tools" del orden del MVP | Cambiar a `004-motor-de-ventas`. Requiere tu OK, es un archivo de instrucciones. |
| `leo-sdd/post-mvp/README.md` y roadmap | Anotar que 016 (pedidos) y 012 (leads) dependen de 004 y que 004 v2 ya implementa pedidos formales y embudo. |

Lo que la 004 vieja cubría y la v2 **no**: reservas de stock con TTL (HU-03), audios entrantes con transcripción (HU-05), catálogo desde foto de lista de precios (HU-04), stickers. Hay que reasignarlos (006, 016 o una 004b) antes de archivar, o se pierden sin decisión.

---

## 5. Riesgos y huecos

### 5.1 Contradicciones internas de la spec

| # | Hallazgo | Dónde |
|---|---|---|
| 1 | RF-012 dice tres tools globales (derivar, conocimiento, enviar recurso); `plantillas.md §2` agrega `registrar_datos_cliente` (cuatro). | spec RF-012 / plantillas §2 |
| 2 | `entrada_anuncio_vigente` ("últimos 7 días") y el borrador "7 días" (C-06) asumen una conversación larga, pero el canal abre una **conversación nueva tras 24 h de silencio** (`_resolve_conversation`, 002 C-002). `conversation_state`, `facts` y `orders.conversation_id` son por conversación: el pedido y los hechos quedan huérfanos al día siguiente, y `saludo_enviado` se reinicia. | C-03, C-06, plantillas §1 |
| 3 | C-02 pide ventana de 20 mensajes; `HISTORY_TURNS = 30` se subió a propósito tras un bug real (comentario en `core.py`). | C-02 |
| 4 | C-02 reutiliza `unsupported_fallback` para "LLM caído", pero en el repo ese texto es la respuesta a audios, videos y ubicaciones. | C-02 |
| 5 | NFR: p95 < 10 s desde que llega el mensaje. La 002 lo mide **incluyendo los 8 s de debounce** (TASK-208). Quedan ~2 s para etapa, prompt, hasta 3 vueltas de LLM, posible reintento del guard y visión (timeout 8 s). Ya era ajustado; no está medido (ESTADO). | spec NFR / plan |
| 6 | C-13 limita el seguimiento a IDENTIFICAR, PEDIDO, DATOS, COBRO y VERIFICACION con rechazo. Un lead que no contesta en CALIFICAR/ANUNCIO nunca recibe seguimiento y `plantillas §6` no tiene texto para ellas. | C-13 |
| 7 | La Constitution dice "mensajes proactivos solo con plantillas aprobadas y opt-in registrado". El seguimiento dentro de la ventana de 24 h cumple a Meta, no la letra de la Constitution; además `post-mvp` asigna "follow-ups automáticos de conversaciones frías" a la 011. | spec H8 / C-13 |
| 8 | C-13 omite condiciones que el canal ya exige: tenant activo (`agent_may_reply`), cuota no agotada y conteo en `ai_messages_used` para respuestas generadas sin mensaje entrante. | C-13 |
| 9 | `plan.md`: "visión con el nivel medio del perfil `openai`". El perfil solo tiene un `chat_model`; el tenant con LLM propio puede no tener visión. | plan |

### 5.2 Dependencias de algo que no existe

| Elemento | Falta |
|---|---|
| Cola de salida multi-mensaje (C-11, plan paso 12) | El flujo actual guarda una respuesta `queued` y `ReplySender` envía un texto. "La tool envía el media en el momento" saltaría el estado `queued`, el barrido de varadas, el guard de ventana, el opt-out y la medición. Cada saliente necesita fila en `messages` (tipo, payload, `delivery_status`). |
| Imagen entrante (TASK-429) | `InboundMessage` no trae id/URL de media; el proveedor no tiene descarga; `messages` no guarda `media_key`. TASK-418 cubre `interactive`/`referral`/BSUID pero **no** la descarga de imágenes. |
| Visión (C-08) | `LLMProvider` no admite imágenes ni salida estructurada ni modelo por llamada: hay que ampliar el puerto y el adaptador `openai_compatible` (httpx, sin SDK). Es un cambio de interfaz protegida ("no romper `LLMProvider`"). |
| Caché de tools por turno (`core.py`) | Asume lecturas puras. `gestionar_pedido(ver)` tras `agregar` en el mismo turno devolvería el pedido viejo. Las tools con efectos deben quedar fuera de la caché. |
| `MAX_TOOL_ROUNDS = 2` | `buscar` más `agregar` ya consumen las dos vueltas; sumar `registrar_datos_cliente` o `enviar_recurso` en cadena no cabe. Evaluar 3–4 (costo y latencia). |
| `catalog_guard` y selecciones libres | El guard compara con nombres del catálogo: un tema libre ("Excel avanzado" con un producto "Excel") se marca inventado. La lista blanca de TASK-426 exige cambiar `_catalogue_guard()` en `core.py`, no solo el guard. |
| Datos personales (C-20) | `_summarise()` guarda argumentos de tools en `agent_traces.tools_called` (hasta 120 caracteres) y el sandbox los devuelve al panel: `registrar_datos_cliente` filtraría DNI y correo. Además el DNI que el cliente escribe queda en `messages.content` y se reenvía al LLM como historial. La 002 TASK-209 ya exige logs sin PII. |
| Stock | Los pedidos no descuentan ni validan stock; `consultar_catalogo` muestra `stock`. Se puede vender un agotado. |
| Evals y prompt | La regla 5 actual ("no puedes registrar ni cobrar") contradice las tools nuevas. Cambiar el prompt obliga a rehacer `test_prompt_builder`, los 5 casos dorados y correr `make evals` (no medido en ESTADO). |
| Solapes con Fase 2 | 016 (pedidos y delivery) y 012 (leads) se apoyan en la 004 vieja; la v2 los absorbe en parte. Falta decidir qué queda de ellos. |

### 5.3 Dependencias nuevas (requieren aprobación explícita)

El plan afirma "no se agrega ninguna librería". Es cierto para el backend si se respetan estas rutas; lo siguiente **no** debe resolverse con paquetes sin pedirlo:

| Necesidad | Sin dependencia nueva | Riesgo de que se proponga una |
|---|---|---|
| Visión de comprobantes | Ampliar `LLMProvider` y enviar `image_url` base64 por `httpx` | SDK de OpenAI, Pillow, OCR (Tesseract). |
| Validar tipo real de archivo (magic bytes, TASK-436) | Comparar cabeceras a mano (JPG, PNG, PDF, MP4) | `python-magic`, `filetype`. |
| Reducir imágenes > 5 MB | Rechazar (la spec ya rechaza) | Pillow para comprimir. |
| Subida de media a YCloud | `httpx` soporta multipart | Ninguna. |
| Esquema de opciones por plantilla | `model_json_schema()` de Pydantic | `jsonschema`. |
| Formulario generado desde schema (TASK-440) | Componentes propios | `react-jsonschema-form`, `ajv`, librería de drag and drop (TASK-441), UI kit. |
| Cron de seguimiento, zona horaria | ARQ `cron` y `zoneinfo` | Ninguna. |
| Niveles de modelo | Variables de settings | Ninguna. |

---

## 6. Clarifications que conviene ratificar antes de TASK-402

| C | Qué decidir | Por qué ahora |
|---|---|---|
| **C-06** | Ciclo de vida del pedido y los hechos frente a la conversación de 24 h (hallazgo 5.1 #2). Opciones: mantener la lectura por contacto (pedido y `entrada_anuncio` siguen al contacto), o aceptar que cada conversación nueva reinicia todo. | Cambia el modelo de 0014 y 0019 (`conversation_id` frente a `contact_id`). |
| **C-19** | Contacto del simulador: crear contacto sandbox (con `phone_e164` nulo) o `orders.contact_id` nullable. | Decide 0019/0020 y el aislamiento. |
| **C-11** | Salientes: tools envían en caliente (plan) o escriben filas `queued` que el sender despacha. Recomiendo lo segundo. Incluye la migración de `messages` que falta. | Sin esto 0014–0020 queda incompleto. |
| **C-08** | Visión: ampliar `LLMProvider` (cambio de interfaz protegida), qué hacer con `validate_receipts`, y quién descarga la imagen entrante. | Afecta TASK-418 y TASK-429. |
| **C-02** | Ventana 30 (no 20), no reutilizar `unsupported_fallback` para el LLM caído, y qué pasa con 301/304/308 de la 003 (descartar o aplazar). | Cierra la 003 y evita un bug ya corregido. |
| **C-13** | Seguimiento dentro de la ventana frente a la Constitution y la 011; condiciones de tenant activo y cuota; etapas CALIFICAR/ANUNCIO. | Es contenido regulatorio y de costo. |
| **C-20** | Enmascarado en `agent_traces` y respuestas del sandbox; política para el DNI en `messages.content`. | Cumple la Ley 29733 y la 002 TASK-209. |
| **C-01** | Orden del MVP: reescribir `CLAUDE.md` (004-tools a 004-motor-de-ventas), carpeta destino, y qué pasa con stock, audios y catálogo por foto de la 004 vieja. | Evita perder alcance al archivar. |
| **Nueva** | Tabla de avisos del tenant: `tenant_notifications` no sirve (§3.1). Decidir tabla nueva o ampliar con PK propia. | Bloquea TASK-431/443. |
| **Nueva** | `ON DELETE CASCADE` sí o no, y reubicación de módulos (§3.5) y del grupo de cobertura. | Convención de todas las migraciones nuevas. |

Esto sin decisión no bloquea TASK-402 (0014 es solo `conversation_state` y eventos), pero C-06, C-19 y C-11 sí cambian su forma; conviene cerrarlas antes.

---

## 7. No verificado

- No se ejecutó nada (ni `engine_referencia.py`, ni `harness21.js`, ni tests, ni `alembic`); no se leyeron `calc21.js`, `fw21.js`, `ce21.js`, `ec21.js`, `etapas.json` ni `etapas_capacita.sql`, solo `LEEME.md`.
- No se revisó el nodo `Formato WhatsApp` de Capacita frente a `whatsapp_text`; la reutilización propuesta es por lectura.
- Campos de YCloud para `interactive`, `referral`, BSUID, subida de media y vigencia del `media_id`: no consultados (TASK-418/419 los resuelven).
- No se leyó `tests/isolation/*` en detalle ni se comprobó si hay un test que compare metadata y migraciones.
- No se confirmó si `runner.py` aplica ya el umbral del 90 %, ni qué hace `tenant_archiver` con los datos (afecta la decisión de `CASCADE`).
- El dato "Meta cobra cada mensaje desde el 1 de octubre de 2026" (spec, historia 7) no se verificó.
- No se abrió `.env` ni ningún secreto.

---

## 8. Adaptaciones

Cambios que se hicieron al implementar frente a `data-model.md` y `contracts/api.md`, con la autorización del dueño (clarifications C-01…C-22 ratificadas; si el repo resuelve algo distinto, manda el repo). Se completa task a task.

### 8.0 Criterios generales

| Tema | Decisión | Motivo |
|---|---|---|
| `ON DELETE CASCADE` | No se usa en `tenant_id` ni en `conversation_id`. Solo en `order_items.order_id` (hijo que no tiene vida sin su pedido). Los `ON DELETE SET NULL` del data-model se respetan. | El repo no hace cascade desde `tenants` ni `conversations` (§3.1). Es el criterio más conservador: un DELETE suelto no borra historial de ventas. El reinicio del simulador (TASK-422) borrará en código, en orden. |
| Nombres de índices y checks | `ix_<tabla>_<columnas>` y `ck_<tabla>_<nombre>` (`op.f` en las migraciones). | Convención de `NAMING_CONVENTION`. Se verifica con `alembic check` (sin diferencias entre modelos y migraciones). |
| Paridad modelos y migración | Cada migración se probó con `upgrade head`, `downgrade -1`, `upgrade head` y `alembic check` contra la BD de desarrollo. | |
| Repositorios | Cada tabla nueva tiene un repositorio que hereda `BaseRepository`. Las de clave compuesta o sin `id` (`conversation_state`, `business_info`, `process_stage_overrides`, `tenant_sequences`) sobreescriben `get` o lo prohíben. | `BaseRepository.get` asume una columna `id`. |

### 8.1 Migración 0014 (TASK-402)

- `conversation_stage_events.created_at` usa `clock_timestamp()` (ya anotado en §3.1).
- `template_key`, `stage_key` y `reason` sin CHECK: las plantillas viven en código (C-05) y el vocabulario de `reason` crece con cada feature; un CHECK convertiría cada uno en una migración.
- `conversation_state` no tiene columna `id`: la PK es `conversation_id`. `ConversationStateRepository.get(x)` recibe el id de la conversación.
- `FunnelCode` (enum compartido, C-18) está en `app/models/conversation_state.py`.
- La FK `conversation_id` no garantiza por sí sola que la conversación sea del mismo tenant (no hay FK compuesta). La garantía es de servicio: `StateRepo` (TASK-412) recibe la conversación ya leída con el repositorio del tenant.

### 8.2 Migración 0018 (TASK-406)

- `products.category` ya existía (0001): no se agrega.
- CHECK `ck_products_modo_venta` y `ck_products_bundle_size_range` (2 a 50) en la base; la API los valida antes (400).
- La importación CSV/XLSX acepta cinco columnas opcionales: `modo_venta` (alias `modo`), `es_digital` (`digital`), `bundle_size` (`paquete`), `bundle_permite_libres` (`permite_libres`, `libres`) y `destacado`. Los alias y las palabras de sí/no (`si`, `sí`, `x`, `1`, `true`...) son una convención de este importador; una celda vacía vale "no" / `precio_fijo`. Un valor ilegible rechaza solo esa fila (`modo_venta_invalido`, `paquete_invalido`, `es_digital_invalido`...).
- `PUT /tenant/products/{id}` rechaza un `null` explícito en las columnas NOT NULL nuevas (400 en lugar de un 500 de la base). `bundle_size: null` sí vale: devuelve el paquete a producto simple.
- `TEMPLATE_CSV` queda sin cambios (las columnas nuevas son opcionales).

### 8.3 Migración 0019 (TASK-407)

- Sin `ON DELETE CASCADE` en `tenant_id`, `conversation_id` ni en `payment_proofs.order_id`: borrar una conversación o un pedido no debe borrar el historial de ventas ni los comprobantes. Único cascade: `order_items.order_id`. Los `SET NULL` del data-model se respetan.
- `payment_proofs.created_at` usa `clock_timestamp()` (el "último comprobante" decide si el pago está rechazado y `now()` empata dentro de una transacción).
- CHECK extra: montos de `orders` y `order_items` no negativos; `payment_proofs.confianza` entre 0 y 1.
- `orders.contact_id` queda NOT NULL: el simulador usa un contacto sandbox (C-19).
- `OrderRepository.add_numbered` toma el número de `tenant_sequences` con un único `INSERT ... ON CONFLICT DO UPDATE ... RETURNING` (`TenantSequenceRepository.next_value`). `OrderService` (TASK-425) lo reutiliza; la prueba de numeración concurrente con conexiones reales es de esa task.
- `OrderRepository.active_for_conversation` desempata por `numero` (dos pedidos en una misma transacción comparten `created_at`).

### 8.4 Migración 0020 (TASK-408)

- La columna es `contacts.phone_e164` (no `phone`) y no existe `wa_id` (§3.1). `phone_e164` pasa a nullable; el CHECK `ck_contacts_identity` exige teléfono o BSUID. El índice único parcial es `uq_contacts_tenant_id_external_user_id` (convención de nombres).
- `ContactRepository.upsert(phone_e164=None, *, external_user_id=None, profile_name=None)`: busca primero por BSUID y luego por teléfono; si falta el otro identificador y llega, lo aprende; nunca sobrescribe uno guardado. Si el teléfono y el BSUID corresponden a dos contactos distintos, gana el del BSUID y NO se fusionan (fusionar movería conversaciones y pedidos): **decisión abierta** para Giannier.
- `ReplySender` ya no envía a un contacto sin teléfono: marca la respuesta `failed` (con evento de estado y log) en vez de llamar al proveedor sin destinatario. El envío por BSUID (`Recipient`) llega con TASK-418; el parser del webhook sigue exigiendo `from` y `to` hasta entonces.
- `agent_traces` suma `stage_key`, `funnel_code`, `tools_enabled`, `outbound_count` y `model_tier`, todas nullable. Las llena el pipeline (TASK-421).
- El downgrade se niega a correr si existe algún contacto sin teléfono (restaurar NOT NULL lo perdería).

### 8.5 Plantillas de proceso (TASK-409)

- Viven en `backend/app/process/templates/` (`base.py` + una por plantilla). `base.py` tiene el vocabulario común: `Stage`, `TOOL_NAMES` (las 10 de `contracts/api.md §7`), `GLOBAL_TOOLS`, los textos `[base]`, los de seguimiento (§6) y los mensajes del sistema (§7, claves `pago_confirmado`, `pago_rechazado`, `pedido_rechazado`, `derivacion` de `contracts/api.md`).
- `TOOL_NAMES` nombra `buscar_productos` y `ver_producto` y NO `consultar_catalogo` (la tool única que existe hoy). Dividirla o renombrarla rompe `test_agent_core`, `test_prompt_builder`, evals y el prompt (§3.4): la decisión (renombrar, alias o ampliar argumentos) queda abierta y es de TASK-417.
- DERIVADO lleva un texto mínimo que no está en `plantillas.md` ("pausada para que la atienda una persona... No respondas"): la tabla dice "no hay turno de modelo", pero el test exige texto en todas las etapas y el texto degrada con seguridad si alguna vez se llamara.
- Las herramientas de cada etapa excluyen las globales (se suman en el registry). `VERIFICACION` guarda aparte las del pago rechazado (`tools_if_rejected`); `COBRO` guarda su segunda columna del embudo (`funnel_with_methods_sent`).
- Opciones: `Template.defaults` (§3), `Template.options_schema` (JSON Schema de Pydantic, sin dependencias nuevas) y `Template.resolve()` -> `EffectiveOptions`. Una sección que el tenant fija reemplaza la del default completa (una calificación a medias no la configuró nadie). `certificaciones` y `servicios` usan `PrepaidOptions`: `modalidad_pago` solo admite `previo`. Los caminos por defecto de `certificaciones` son el ejemplo de `data-model.md §0016`.
- `servicios` limita el pedido a 3 líneas (`max_lineas`, §3); `certificaciones` es `paquete`; las otras son `carrito`.
- Los textos conservan los saltos de línea de `plantillas.md`; por eso los módulos de plantilla llevan `# ruff: noqa: E501`.

