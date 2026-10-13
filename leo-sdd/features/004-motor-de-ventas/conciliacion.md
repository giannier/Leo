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
| Moneda | `orders.moneda char(3)` | `business_profiles.currency` (`PEN`), pero `consultar_catalogo` imprime `S/` fijo. | Corregido en TASK-420 para las tools nuevas (§8.16). |

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
- Campos de YCloud para `interactive`, `referral`, BSUID, subida de media y vigencia del `media_id`: resueltos en TASK-418/419 (§8.14 y §8.15).
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
- (Superado por §8.14.) `ReplySender` ya no envía a un contacto sin teléfono: marca la respuesta `failed` (con evento de estado y log) en vez de llamar al proveedor sin destinatario. El envío por BSUID (`Recipient`) llega con TASK-418; el parser del webhook sigue exigiendo `from` y `to` hasta entonces.
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

### 8.6 Motor de etapas (TASK-410)

- `compute_stage(template, options, facts, now, *, ignorar_derivacion=False)` y `funnel_for(template, stage, facts)` en `backend/app/process/engine.py`. `now` es parámetro (sin reloj propio); `ignorar_derivacion` es el `stage_previa` del paso 3 del pipeline. `template` queda en la firma de `plantillas.md §1` pero hoy no se usa en `compute_stage` (las tres plantillas comparten reglas y difieren solo por las opciones); `funnel_for` sí lee la columna del embudo de la plantilla.
- Los 34 casos de `engine_referencia.py` están portados a `tests/unit/test_process_engine.py` contra las plantillas reales (`CERT` = defaults de `certificaciones`, `TIENDA` = defaults de `productos`). Más prioridades C-04, bordes (pausa que vence justo ahora, anuncio a exactamente 7 días), alcanzabilidad de las 11 etapas y un barrido de todas las combinaciones de hechos. Cobertura de `engine.py`: 100 % de líneas y ramas.
- El modelo `Facts` (guardados + derivados, `backend/app/process/facts.py`) se define aquí porque el motor lo necesita; TASK-411 agrega `FactsLoader` y las derivaciones. En el modelo, el hecho guardado `metodos_pago_enviados` ({order_id, en}) es `metodos_pago_registro` y `metodos_pago_enviados` es el booleano derivado.
- `backend/scripts/coverage_gate.py` aún no cuenta `app/process/` ni `app/orders/` como grupo crítico (§3.5): cambio de script pendiente de aprobación; hoy caen en el 70 % de "el resto".

### 8.7 Hechos (TASK-411)

- `FactsLoader(session, tenant_id, FactsConfig).load(conversation, now=None)` lee en **3 sentencias como máximo** (2 si no hay pedido): (1) contacto + estado de la conversación (outer join), (2) pedido activo + último comprobante + fecha del último rechazo (sub-selects correlacionados), (3) líneas del pedido con `products.es_digital`. Lo hace `ConversationSnapshotRepository` (`backend/app/repositories/conversation_snapshot.py`), que no hereda `BaseRepository` porque cruza seis tablas; el `tenant_id` es argumento del constructor y va en el WHERE/ON de cada tabla. Una conversación de otro tenant se lee como vacía. Un test cuenta las sentencias.
- La configuración que necesitan los hechos derivados (opciones efectivas, sección Entregas, promociones vigentes) entra como `FactsConfig`: la sirve `ProcessConfigService` con su caché de 60 s (TASK-414), así el loader no suma consultas.
- `build_facts` es pura y concentra las reglas: `pago_estado` (tabla de `data-model.md §0019`), `datos_faltantes` (el teléfono desde el que escribe cuenta como `telefono`; un contacto solo con BSUID debe darlo), entrega (`tipo_entrega`, `direccion`, `zona_envio` solo si hay zonas), `metodos_pago_enviados` (del pedido activo y posterior al último comprobante rechazado, C-21), nombre confirmado frente a nombre de perfil, `promos_cierre`.
- **Decisiones abiertas para Giannier:**
  1. Un negocio que ofrece **solo delivery** (sin recojo) igual obliga a elegir `tipo_entrega`: `plantillas.md §1` solo fija el tipo solo cuando el negocio ofrece únicamente recojo. Parece una asimetría; si se quiere que `delivery` también se fije solo, es una línea en `_missing_data`.
  2. `promos_cierre` lista **todas** las promociones de cierre vigentes. El filtro "que aplique al pedido" (productos, categorías, monto mínimo) queda para `pricing` (TASK-424) y el prompt builder (TASK-414).
  3. Una línea cuyo producto fue borrado (`product_id` nulo) cuenta como física (se pide dirección): es el lado seguro.
- `motivo_rechazo` solo se expone mientras el pedido activo está en `borrador` (rechazo de comprobante o de contra entrega, plantillas §5 COBRO); un pedido confirmado no arrastra el motivo viejo.

### 8.8 Estado persistente (TASK-412)

- `StateRepo` (`backend/app/process/state.py`): `get`, `get_or_create(conversation, template)` y `save(conversation_id, *, stage, funnel, facts_patch, reason, expected_version, snapshot=None)`. `get_or_create` rechaza una conversación de otro tenant (`TenantScopeError`) y crea la fila con `INSERT ... ON CONFLICT DO NOTHING`, así dos workers no chocan en la clave primaria.
- `get_or_create` **no escribe evento**: un evento registra un cambio y el primero es el paso de INICIO a donde caiga el primer turno (si el cliente solo saluda y la etapa sigue en INICIO, no hay evento).
- `save`: `UPDATE ... WHERE version = :v` (`version + 1`). Si el UPDATE no toca ninguna fila, reintenta **una vez** sobre la fila fresca (re-aplica el parche del llamador, que es su intención) y, si vuelve a chocar, lanza `StateConflict`; nunca descarta una escritura en silencio. El evento se compara contra la fila fresca, no contra la que el llamador creía: dos escritores que llegan a la misma etapa dejan **un** evento.
- `facts_patch` mezcla clave a clave sobre los hechos guardados; `None` borra la clave (así se limpia `derivado` al reanudar el bot). Solo acepta `STORED_KEYS`: un hecho derivado o desconocido lanza `ValueError` (C-03).
- `facts_snapshot(facts)` arma el `facts_snapshot` del evento: hechos guardados y derivados sin `nombre_cliente` ni `derivado.resumen` (datos personales, C-20). `datos_faltantes` lleva solo nombres de campo.
- "Recalcula una vez" del `plan.md` queda del lado del pipeline (TASK-421): `save` re-aplica la misma etapa y el mismo parche; si el pipeline quiere recalcular la etapa con los hechos nuevos, atrapa `StateConflict` o lee `SaveResult.retried`.
- Pruebas: la contención se prueba con versiones viejas y, además, con dos conexiones reales (un escritor mantiene el candado de fila sin confirmar mientras el otro intenta guardar). Este último test confirma filas reales y las borra él mismo.


### 8.9 Respuestas interactivas y grupo de cobertura (TASK-413)

- `apply_interactive(reply, facts, options)` en `backend/app/process/interactive.py` (la firma de `tasks.md` decía `state`; necesita los caminos configurados para validar la clave y los hechos para no reescribir el mismo camino). Devuelve `InteractiveOutcome(facts_patch, context)`: el `facts_patch` es el de `StateRepo.save`.
- `camino:{clave}` conocido fija `{"camino": clave}` y no agrega línea de contexto. Una clave inexistente en esa plantilla se ignora y queda en el log (`warning`). Tocar el camino que ya estaba elegido no escribe nada.
- `producto:`, `categoria:`, `promo:` y `accion:` devuelven `El cliente eligió: {título} ({campo}={valor})`. Un id sin prefijo conocido o mal formado (solo se aceptan `[A-Za-z0-9_.-]{1,64}`) devuelve `El cliente eligió: {título}` y no escribe nada: es dato de entrada.
- El título se aplana a una línea de 100 caracteres máximo antes de llegar al prompt.
- `InteractiveReply(id, title, description)` es el tipo que el adaptador (TASK-418) deberá producir al parsear `button_reply` y `list_reply`.
- `scripts/coverage_gate.py`: `app/process/` y `app/orders/` pasan al grupo crítico "sales engine" (80 %), cierra el pendiente de §3.5 y §8.6.

### 8.10 Presupuesto de mensajes (TASK-415)

- `TurnBudget(max=3, reserva_texto=1)` en `backend/app/agent/budget.py`. `consume()` gasta un mensaje de una herramienta y devuelve `False` (sin gastar) cuando solo queda la reserva del texto; así una herramienta responde `no_disponible` con "ofrécelo en tu próximo mensaje" (lo arma TASK-417).
- `remaining_text_bubbles()` = `max - gastados`: 3, 2 o 1 según se hayan enviado 0, 1 o 2 cosas. El tope por nivel de detalle (breve 1, extenso 2) lo aplica el formateador (TASK-416): con 2 cosas enviadas el texto sale en 1 burbuja (C-11).
- El presupuesto es mutable y una instancia viaja con el turno. Un valor imposible (`max < 1`, reserva fuera de `0..max`) lanza `ValueError`.

### 8.11 Formateador WhatsApp (TASK-416)

- `to_bubbles(texto, detalle, budget, *, threshold=700)` en `backend/app/agent/formatter.py`, port de `fw21.js`. Los 7 casos de `harness21.js` están en `tests/unit/test_formatter.py` con los mismos textos, más breve/moderado/extenso, el corte por párrafo más cercano a la mitad, presupuesto con 1 y 2 envíos de herramientas y presupuesto agotado (cobertura 100 %).
- Burbujas: `breve` 1; `moderado` y `extenso` 1, o 2 si el texto pasa de 700 caracteres; siempre `min(tope del nivel, budget.remaining_text_bubbles())`. Con un presupuesto sin espacio para texto (`reserva_texto=0` y todo gastado) devuelve `[]`: respeta el presupuesto. Un nivel desconocido se trata como `moderado`.
- **Desvío de la propuesta de §3.5 ("mover y reutilizar `whatsapp_text`, `_tidy`, `_usable_answer`"):** no se movieron. `core.py` los conserva tal cual para el camino de una sola respuesta que existe hoy, porque sus tests (`TestWhatsAppMarkup`, `TestReplyLayout`) fijan un comportamiento distinto al del nodo v21: el encabezado pierde el marcador en vez de pasar a negrita y `- item` queda con guion en vez de viñeta. El formateador nuevo sigue a v21 (encabezado a negrita, viñeta `•`, enlaces `texto: url`). TASK-421 reemplaza el camino viejo por este módulo y entonces se retira el código duplicado de `core.py`.
- Dos diferencias deliberadas con `fw21.js`: `__x__` pasa a negrita (como Markdown y como `core.py`; v21 lo dejaba en cursiva) y los delimitadores `**` exigen texto pegado (`2 ** 3` no se toca). Se agregó la viñeta `* item`. Un `#` solo es encabezado si lo sigue un espacio (`#42` queda intacto).
- No limpia sintaxis de llamada a herramienta (`<function=...>`): eso sigue en `_usable_answer` hasta TASK-421.

### 8.12 Prompt por secciones y configuración efectiva (TASK-414)

- **Ubicación.** `tasks.md` dice `prompt_builder.py` y §3.5 propone extender `agent/prompts/builder.py`. Se creó `backend/app/agent/prompts/stage.py` (`build_stage_prompt`) junto al builder existente: el prompt viejo (`build_system_prompt`) sigue siendo el que usa `AgentCore` hasta que TASK-421 cambie el agente, y sus tests (`test_prompt_builder`, evals) no se tocan. Reutiliza `_TONE_GUIDANCE` y `_format_hours` del builder viejo.
- **`ProcessConfigService.effective(tenant_id)`** (`backend/app/process/service.py`) devuelve un `EffectiveProcessConfig` inmutable: plantilla, opciones efectivas, texto efectivo de cada etapa (override o el de la plantilla), comportamiento (`Behavior`), perfil del negocio, las 5 secciones ya validadas (`BusinessInfoView`), métodos de pago activos y promociones vigentes. 6 sentencias en la primera lectura, 0 en las siguientes: caché por tenant de 60 s (`ProcessConfigCache`, local al proceso, con `invalidate()` para las escrituras de TASK-435). La caché guarda solo valores sin sesión: las promociones se copian a instancias transitorias. `EffectiveProcessConfig.facts_config()` entrega el `FactsConfig` que pedía `FactsLoader` (§8.7).
- Robustez: una plantilla inexistente en `agent_configs.process_template_key`, opciones guardadas que ya no validan o una sección de `business_info` inválida no tumban el turno: se usa la plantilla `productos` / las opciones por defecto / se omite la sección, con un `warning`.
- **`TurnContext`** (`backend/app/process/turn.py`) no tenía forma definida en los docs. Se dejó mínimo: `opcion` (la línea de `apply_interactive`), `comprobante` (frase ya redactada para el modelo) e `imagen_recibida`; `lines()` da el bloque "ESTE TURNO". TASK-421 lo arma desde el mensaje entrante.
- **Presupuesto.** Topes por sección de `plan.md` (450 / 600 / 1 800 / 500 / 1 200 / 1 400) en `SECTION_CAPS`; total 6 000. Sin tokenizador (no hay dependencia nueva): `estimate_tokens` = caracteres / 3,5, que sobreestima un poco. Se hace cumplir así: reglas adicionales del dueño se recortan para caber en el estilo; el negocio descarta líneas desde el final (menos útil último) y cada lista larga dice cuántos elementos dejó fuera; el bloque ESTADO se reconstruye con listas más cortas (3 niveles) hasta caber. Las **instrucciones de etapa no se recortan** (cortar a mitad el texto del dueño es peor que pasarse): el prompt de cada etapa por defecto cabe en una fracción del tope y `StagePrompt.over_budget` nombra la sección que se pase para que el panel y `prompt-preview` lo digan. Las definiciones de herramientas viajan aparte (`tools`) pero se cuentan: `build_stage_prompt(..., tool_specs=...)` suma su estimación; el test de TASK-417 verifica el total real con el registry.
- **Test de presupuesto:** 3 plantillas × 11 etapas con un negocio "lleno" (20 locales, 30 zonas, 12 promociones, 2 000 caracteres de reglas, pedido de 10 ítems, listas de 25 claves) y todas las líneas del ESTADO: cada sección ≤ su tope y el total + 1 400 de herramientas ≤ 6 000.
- **Snapshots:** `backend/tests/fixtures/prompts/{productos,certificaciones,servicios}.txt` tienen el prompt de las 11 etapas con un negocio y hechos realistas. Se regeneran con `UPDATE_PROMPT_SNAPSHOTS=1` y se revisan en el diff.
- **Placeholders:** si falta el valor se quita **la línea completa** (también las variables `{var.x}` sin definir). Para que eso no deje frases cortadas se re-acomodó el texto por defecto de COBRO (`base.py` y `plantillas.md §5`): la mención de `{link_pago_tarjeta}` pasó a su propia línea; antes compartía línea con "del comprobante por aquí." y, sin link, se perdía esa mitad de la oración. `{nombre_cliente}` solo se llena con un nombre confirmado (`custom_data.nombres`), nunca con el de perfil.
- **Contenido del negocio compacto** (una línea por tema): identidad (nombre, rubro, descripción de 300 caracteres máx.), métodos de pago (solo los nombres y la modalidad; el detalle sale por `enviar_metodos_pago`), entregas, locales, horario del perfil, cambios y devoluciones, contacto, promociones **automáticas** vigentes y tipos de cliente. Las promociones de **cierre** no se anuncian ahí: solo aparecen en el ESTADO, en IDENTIFICAR/PEDIDO/DATOS/COBRO (regla 7). Se incluyeron la descripción y el horario del perfil (`business_profiles`) porque el prompt viejo los tenía y perderlos al cambiar de prompt sería una regresión; no están en `plantillas.md` (**decisión abierta**).
- **Decisión abierta:** el ESTADO lista **todas** las promociones de cierre vigentes (como las entrega `FactsLoader`, §8.7 punto 2). El filtro "que aplique al pedido" (productos, categorías, monto mínimo) necesita las líneas del pedido y vive en `pricing` (TASK-424), que también rechazará `aplicar_promocion` si no aplica.
- Pendiente de TASK-421: el ESTADO no trae "cliente nuevo" (no hay un hecho que lo indique) y la fecha de hoy no entra al prompt nuevo.

### 8.13 Registry de herramientas (TASK-417)

- **Decisión sobre `consultar_catalogo`** (la que §8.5 dejó abierta): alias, no renombre. Detalle y alternativas en `plan.md`, "Decisiones Posteriores"; **pendiente de ratificar por Giannier**. El registry del agente de hoy no cambia; el registry por etapa ofrece `buscar_productos` y `ver_producto` y resuelve el nombre viejo con `ToolRegistry.alias`.
- `backend/app/agent/tools/registry.py`: `ToolResult(status, data, decir, texto)` (`texto` es la prosa que lee el modelo; `data` la copia estructurada), `ToolContext` ampliado con `conversation_id`, `budget` (`TurnBudget`), `adapter`, `sandbox`, `turn` (`TurnContext`), `facts` y `facts_patch` (todos opcionales: el `ToolContext(session, tenant_id, llm)` del agente de hoy sigue valiendo), `ToolDefinition` / `ToolScope` / `ToolCatalog`. `adapter` es `Any`: su interfaz (`send_media`, `send_buttons`, `send_list`) la define TASK-418/420.
- `ToolCatalog.for_stage(template, stage, facts, options)`. **Se agregó `options` a la firma** de `tasks.md` (`for_stage(template, stage, facts)`): "`elegir_camino` no aparece si no hay caminos" y su enum dependen de las opciones del tenant, que no salen de la plantilla. Un acceso directo, `registry_for_stage(...)`, vive en `definitions.py`.
- Reglas del registry: cada etapa expone sus herramientas más las globales (`plantillas.md §2`; en VERIFICACION con pago rechazado, las del rechazo, C-21); DERIVADO no expone ninguna. Sin caminos: no hay `elegir_camino` y `mostrar_opciones` no admite `tipo=caminos` (ni siquiera si el modelo lo inventa: queda `no_disponible`). `gestionar_pedido(confirmar)` solo está en el enum en COBRO con contra entrega y, además, un guard lo rechaza (`no_disponible`) en cualquier otro caso aunque el modelo lo llame. `registrar_datos_cliente` arma su schema por tenant: `nombres`, los `datos_requeridos` de la plantilla y, si el pedido requiere entrega, `tipo_entrega`, `direccion`, `referencia`, `zona_envio`.
- `definitions.py` declara las 10 herramientas con su schema. Reales: `buscar_productos`, `ver_producto`, `consultar_conocimiento`. Con stub `no_disponible` ("No la menciones"): `enviar_recurso`, `mostrar_opciones`, `elegir_camino` (TASK-420), `gestionar_pedido`, `registrar_datos_cliente`, `enviar_metodos_pago` y `derivar_a_humano` (fases B y D). Las 10 juntas, con schemas completos, ocupan una fracción de las 1 400 tokens asignadas; el test arma el prompt completo de las 33 combinaciones plantilla x etapa con negocio lleno y verifica ≤ 6 000.
- Catálogo: `ProductRepository.search_active` (filtra por tenant, destacados primero, `%` y `_` literales) y `get_active`. `buscar_productos(consulta, categoria?, solo_paquetes?)` y `ver_producto(producto_id)` devuelven `modo_venta`, `bundle_size`, `destacado`, `es_digital` y el `producto_id` (para pasarlo a `ver_producto` y `gestionar_pedido`). Un `producto_id` de otro tenant se ve igual que uno inexistente. La moneda sigue siendo `S/` fija: la corrige TASK-420 (§3.1).
- Fuera de 417 (TASK-420): recursos vinculados, `promociones_aplicables` (necesita `pricing`) y fijar `vio_productos`. Las herramientas ya reciben `ctx.facts_patch` para escribir hechos y el pipeline lo guardará con `StateRepo.save`.
- `AgentCore._run_tool` acepta `ToolResult` o texto; una fuente RAG solo se registra si el estado es `hecho`.

### 8.14 YCloud: interactivos, media, referral y BSUID (TASK-418)

Fuente: documentación oficial de YCloud (`docs.ycloud.com`: ejemplos de envío, "Business-Scoped User IDs API & Webhook Updates", ejemplos de payload de webhook y "Upload media"), consultada el 2026-10-12. Donde difiere de `contracts/api.md §8`, manda la doc.

| Tema | `contracts/api.md §8` | Documentación de YCloud (lo implementado) |
|---|---|---|
| Destinatario por BSUID | `Recipient.bsuid` | `POST /whatsapp/messages` acepta `recipient` en lugar de `to`. Si vienen los dos, YCloud usa `to` e ignora `recipient`: por eso el adaptador manda el teléfono cuando existe y el BSUID solo cuando no. |
| BSUID entrante | `InboundMessage.from_user_id` | `whatsappInboundMessage.fromUserId` (y `fromParentUserId`, no se usa). Con BSUID `from` puede **faltar**: el parser acepta `from` o `fromUserId` y descarta el mensaje solo si faltan ambos. |
| `referral` | `{ad_id, source_url, headline, body, media_url}` | `{source_url, source_type, source_id, headline, body, media_type, image_url, video_url, thumbnail_url, ctwa_clid}`. `Referral` usa `source_id` (no `ad_id`) y `media_url` = `image_url` o `video_url`; agrega `source_type`, `media_type` y `ctwa_clid`. Se aceptan también las claves en camelCase. |
| Interactivos entrantes | `interactive = {id, title}` | `interactive.type` = `button_reply` / `list_reply`, con `id`, `title` y (lista) `description`. Se produce `InteractiveReply(id, title, description)`. |
| Envío | `send_*(to: Recipient, …)` | Se mantiene la convención del adaptador: `send_*(credentials, *, sender, to: Recipient, …)`. Botones y lista usan el objeto `interactive` de Meta; media usa `type: image/document/video/audio` con `id` o `link`. |
| Subida | `upload_media(data, mime, filename)` | `upload_media(credentials, *, sender, data, mime, filename)`: `POST /whatsapp/media/{phoneNumber}/upload`, multipart con un solo `file`; el número va en el path (E.164, el `+` codificado). Respuesta con `id`. |

Decisiones de implementación:

- Un toque en botón o fila llega como `msg_type="text"` con `text` = título que vio el cliente (así se guarda en `messages`, sin migración y sin que el opt-out o la memoria vean un mensaje vacío). El `id` viaja en `InboundMessage.interactive`; **TASK-421 debe volver a leerlo del evento** (`InboundResult` no lo trae) para llamar a `apply_interactive`. Un interactivo que no es `button_reply`/`list_reply` (p. ej. un Flow) sigue siendo `unsupported`.
- `InteractiveReply` se movió a `providers/whatsapp/base.py`; `process/interactive.py` lo re-exporta (misma ruta de import).
- `ReplySender` ya no marca `failed` a los contactos sin teléfono: envía con `Recipient(phone, bsuid)`. Queda sin efecto la salvedad de §8.4.
- `ContactRepository.upsert` recibe ahora también el BSUID del webhook (`receive_inbound`).
- El adaptador rechaza (error no reintentable, sin llamar a YCloud) lo que WhatsApp rechazaría: más de 3 botones, más de 10 filas, título de botón > 20, fila > 24 / descripción > 72, cuerpo > 1024, encabezado o pie > 60. Los tools (TASK-420) truncan antes; esto es la última línea.
- Límite de la doc: los interactivos **no** aceptan media por `id` en el encabezado (solo por `link`). No se envían encabezados de media.
- Los fixtures de `tests/fixtures/ycloud/` (`inbound_interactive_*`, `inbound_referral`, `inbound_bsuid_only`, `send_*_request`, `upload_media_response`) se construyeron **a partir de los ejemplos de la documentación**, no de capturas reales: la doc no publica un ejemplo completo de `referral` ni de `list_reply`. Cuando haya un webhook real, se pega encima del fixture (`tests/fixtures/__init__.py`).
- **No verificado:** la ubicación exacta de `referral` (se leyó a nivel del mensaje, `whatsappInboundMessage.referral`, como en Meta) y cualquier campo de la respuesta de subida distinto de `id`.

### 8.15 Subida de media y caché (TASK-419)

Fuente: documentación de YCloud, "Upload media" (`POST /whatsapp/media/{phoneNumber}/upload`): archivo único en multipart, "todos los archivos se cifran y persisten **30 días**", la respuesta trae el `id` del media. Coincide con `contracts/api.md §8` y C-17; el vencimiento se calcula en el adaptador como subida + 30 días (la respuesta no trae fecha).

- `services/media_delivery.py`: `MediaDelivery.reference_for(asset)` devuelve el `MediaRef` para enviar. Si el `provider_media_id` guardado vence en más de 1 h, lo reutiliza; si falta, venció, está por vencer o no tiene vencimiento, sube el archivo (lee del almacenamiento, `upload_media`) y guarda `provider_media_id` y `provider_media_expires_at`. Si la subida falla (cualquier `WhatsAppError`, incluida una credencial rechazada), no falla el envío: devuelve un link firmado de 3 600 s y **no guarda nada**, así el próximo envío vuelve a intentar la subida. Si el archivo ya no está en el almacenamiento lanza `MediaUnavailable` (no hay qué enviar ni enlazar); el llamador (TASK-420) lo traduce a `no_disponible`.
- Job `media.refresh_provider_ids` (`workers/media.py`, cron diario 08:25 UTC = 03:25 Lima): vuelve a subir los activos cuyo id vence en menos de 48 h. Busca primero qué negocios tienen trabajo (única consulta entre tenants, solo `tenant_id`) y luego opera por tenant con su número conectado y sus credenciales. Cada activo se confirma por separado; una subida rechazada o un archivo faltante se cuenta como `failed` y conserva el id anterior. Un negocio sin número conectado se salta (sus activos cuentan como `failed`).
- El nombre del cron es `cron:media.refresh_provider_ids`: el prefijo `cron:` es la convención de ARQ que `test_worker_registration` usa para distinguir lo que dispara el reloj de lo que se encola.
- Fuera de alcance de esta task: invalidar `provider_media_id` al reemplazar el archivo (`POST /assistant/media/{id}/file`, API de la biblioteca). El repositorio ya tiene `clear_provider_media`.
- Un link firmado de MinIO en desarrollo no es público: el fallback solo funciona con un almacenamiento alcanzable desde internet (producción).

### 8.16 Herramientas `enviar_recurso`, `mostrar_opciones`, `elegir_camino` y extensión del catálogo (TASK-420)

- **Interfaz de salida** (`agent/outbound.py`, `TurnOutbound`): `send_media(asset)`, `send_buttons(body, buttons)`, `send_list(body, button_text, sections)`; todas lanzan `OutboundFailed` si el mensaje no salió. Es lo que cierra el `adapter: Any` de §8.13. Las tools no llaman al proveedor: **TASK-421 debe implementar el adaptador real** (resuelve el archivo con `MediaDelivery`, llama al proveedor, escribe la fila en `messages` según C-11 de §6) y el del simulador (registra y no envía, C-19). Con `adapter=None` las tools responden `no_disponible`.
- `ToolContext.options` (nuevo): `ToolCatalog.for_stage` lo rellena con las opciones del scope en cada llamada, así el handler lee las mismas opciones con las que se armó su schema (caminos por tenant) sin depender de que el pipeline las pase.
- **`enviar_recurso(clave)`**: `no_disponible` si la clave no existe, está inactiva o es de otro tenant; `omitido` si ya está en `recursos_enviados` (de lo guardado o de lo enviado antes en el mismo turno); `no_disponible` con "ofrécelo en tu próximo mensaje" si el presupuesto del turno no alcanza (no gasta ni envía); `error` si el envío falla (no se registra ni gasta). Si sale, agrega la clave a `recursos_enviados` vía `facts_patch`.
- **`mostrar_opciones(tipo, texto, filtro?)`**: 1 a 3 filas van como botones (título a 20) y 4 a 10 como lista (título a 24, descripción a 72, botón "Ver opciones"); más de 10 se recorta a 10; el cuerpo se corta a 1 024. Los truncamientos usan "…". Las filas salen de la base:
  - `caminos`: `calificacion.caminos` del tenant, id `camino:{clave}`.
  - `categorias`: **las categorías no tienen tabla ni id** (`products.category` es texto libre). Se listan las distintas de los productos activos; el id es `categoria:{slug}` (sin tildes ni espacios, para cumplir el patrón que acepta `apply_interactive`). El modelo recibe "El cliente eligió: Gestión pública (categoria_id=gestion-publica)" y debe buscar por el título. **Decisión abierta:** si se prefiere una tabla de categorías con id propio, cambia `_category_rows` y la migración.
  - `productos`: activos, destacados primero, precio en la moneda del negocio ("Se cotiza" para `modo_venta=cotizacion`), id `producto:{uuid}`. `filtro="paquetes"` deja solo paquetes; **cualquier otro `filtro` se lee como nombre de categoría** (convención de esta task; `contracts/api.md` no define los valores). Fija `vio_productos`.
  - `promociones`: solo las **automáticas** vigentes. Las de cierre no se listan: se ofrecen una vez, cuando el cliente duda, con `gestionar_pedido(aplicar_promocion)` (regla 7). **Decisión abierta** si se quiere también mostrar las de cierre.
  - Guarda `opciones_mostradas` como `tipo` o `tipo:filtro` (sin duplicados). Reenviar las mismas opciones **no** devuelve `omitido` (el contrato solo lo prevé para recursos); el costo es de presupuesto del turno.
- **`elegir_camino(camino)`**: `error` si la clave no es una configurada (el enum del schema ya lo evita; el handler lo vuelve a comprobar), `omitido` si ya es el camino vigente, si no fija `facts.camino`.
- **Catálogo**: `buscar_productos` y `ver_producto` agregan a cada producto `recursos` (clave, título, cuándo usar y si ya se envió en la conversación) y `promociones_aplicables`. Sin `pricing` (TASK-424), "aplicables" significa **automáticas, vigentes y cuyo alcance cubre el producto** (todo, productos o categorías); condiciones del pedido (monto mínimo, cantidad) se describen pero no se evalúan, y el descuento de un pedido lo calcula `pricing`. El texto de la regla sale de lo configurado (`20 % de descuento`, `lleva 3 y paga 2`, `5 por S/ 400.00`...).
- **`vio_productos`** se fija cuando `buscar_productos` devuelve coincidencias (no cuando cayó al listado completo por no encontrar nada: eso son alternativas que ofrece el modelo, no algo que el cliente vio), cuando `ver_producto` encuentra el producto y cuando `mostrar_opciones(productos)` se envía.
- **Moneda** (§3.1): `buscar_productos` y `ver_producto` usan `business_profiles.currency` (`PEN` → `S/`, `USD` → `US$`, otro código tal cual; sin perfil, `PEN`). `consultar_catalogo` (la tool de la 003, §8.13) conserva `S/` fijo hasta que TASK-421 la retire.
- Las constantes de límites de WhatsApp pasaron de `ycloud.py` a `providers/whatsapp/base.py` (no dependen del proveedor) y las comparten adaptador y tools.
- `ProductRepository.active_categories()` y `MediaAssetRepository.for_products()` (una consulta por resultado, no por producto) son nuevos y están cubiertos por la suite de aislamiento (`tests/isolation/test_sales_tools_isolation.py`).
- `test_tool_registry` dejó de tratar a las tres tools como stubs (siguen siéndolo `gestionar_pedido`, `registrar_datos_cliente`, `enviar_metodos_pago`, `derivar_a_humano`) y su `_call` pasa un `ToolContext` en vez de `None`, porque el registry ahora le agrega las opciones.

### 8.17 TurnPipeline (TASK-421)

- **`agent/pipeline.py`**: `TurnPipeline(session, tenant_id, *, llm, outbound, sandbox, queue_text, model_tier_applies).run(conversation_id, InboundTurn) -> TurnResult` con los 14 pasos de `plan.md`. `InboundTurn` es la ráfaga ya leída (texto, toques, anuncio, imagen); `TurnResult` trae etapa antes/previa/después, embudo, hechos, herramientas habilitadas, la respuesta del modelo y los mensajes salientes del turno (los de las herramientas y los de texto). `stage_previa` se calcula y se devuelve; el `ProofPipeline` que la usa llega con TASK-429 (hoy una imagen entra al modelo como la línea `[El cliente envió una imagen]`).
- **El estado se guarda antes del gate.** Anuncio, camino tocado y etapa se escriben aunque el asistente esté pausado, el contacto haya pedido no recibir mensajes o haya una derivación vigente (también con la etapa en VERIFICACION: el pago pendiente gana en el embudo, no en el gate). El orden del gate es pausado, opt-out, derivación. El simulador salta los dos primeros (sigue siendo la forma de probar un negocio pausado, Clarification 002). Un mensaje que el asistente no puede leer (audio, sticker...) pasa por los pasos 1 a 7 y vuelve como `unsupported`: el llamador responde con el `unsupported_fallback` del negocio.
- **Un evento por cambio de etapa.** Paso 6 (con los hechos que dejó el paso 4) y paso 13 (con los hechos que dejaron las herramientas y `saludo_enviado`) guardan con `StateRepo.save`; un guardado sin cambio de etapa ni de columna del embudo no escribe evento. Ante un `StateConflict` el pipeline relee la versión y reintenta una vez más.
- **`saludo_enviado`** se fija al terminar un turno cuyo modelo respondió; un turno degradado (el modelo falló) no cuenta como saludo, así el siguiente sigue en INICIO.
- **Toques y anuncio.** El webhook los traía en `InboundMessage`, pero la respuesta corre después del debounce y solo tiene la fila de `messages`. Por eso `ChannelEventProcessor.receive_inbound` guarda `interactive` y `referral` en `messages.payload` (`process/inbound.py` los escribe y los vuelve a leer, defensivo) y el pipeline llama a `apply_interactive` con el id real, no con el texto del botón. Un anuncio pasa a `entrada_anuncio`.
- **Migración 0021 (decisión abierta, ver abajo).** `messages.payload jsonb NULL` y los tipos `interactive` y `video` en el CHECK de `msg_type`. `conciliacion.md` 3.1 y 5.2 ya decían que cada saliente necesita fila con tipo y payload y que faltaba la migración; no estaba asignada a ninguna task.
- **`TurnOutbound` (C-11)**, en `services/turn_outbound.py`: `LiveOutbound` manda por el proveedor en el momento en que la herramienta lo pide (resuelve el archivo con `MediaDelivery`, respeta la ventana de 24 h, reintenta solo lo reintentable) y escribe la fila del saliente (`sent` con su `provider_message_id` y su evento de estado); `SandboxOutbound` escribe la misma fila y no manda nada (C-19). Un saliente que no salió no deja fila: la herramienta responde `error` y el modelo lo sabe. `live_outbound_factory` devuelve `None` si la conversación no puede mandar (sin número, sin credenciales, sin almacenamiento) y entonces las herramientas dicen `no_disponible` sin romper el turno. El payload guarda `tipo` (`botones`, `lista`, `media`), el cuerpo, los botones o filas y, para un archivo, `clave`, `titulo`, `caption` y `asset_id`; `content` conserva el texto legible.
- **Texto al final.** El formateador (`to_bubbles`) parte la respuesta en 1 o 2 burbujas según el presupuesto; en el canal real se guardan `queued` y las manda `ReplySender` en orden. Si el modelo no escribió nada y una herramienta ya puso opciones o un archivo en pantalla, no se inventa una disculpa (`AgentReply.silent`). Un turno cuenta como un solo mensaje de la cuota, sean cuantas sean las burbujas.
- **`ConversationResponder`** deja los gates al pipeline (ya no consulta `agent_may_reply` ni el opt-out) y suma `respond_turn() -> list[Message]`; `respond()` devuelve la primera burbuja. El worker acepta un `outbound_for` y manda las burbujas en orden. `answer_conversation` devuelve `answered` si solo salieron cosas de herramientas.
- **`core.py`.** `respond(..., system_prompt=None, context=None)` recibe el prompt de la etapa y el `ToolContext` ya armado (presupuesto, adaptador, hechos); sin ellos conserva el prompt y el registry de antes, que son los que corren los evals y los tests de la 003. `whatsapp_text` se retiró: `_usable_answer` usa `to_whatsapp_markup` del formateador, así que los encabezados pasan a negrita y `- item` a viñeta (las pruebas de `TestWhatsAppMarkup` y `TestReplyLayout` se ajustaron). Cada herramienta llamada queda en la traza con su `status`.
- **Traza (paso 14).** `agent_traces` guarda `stage_key`, `funnel_code` (los del final del turno), `tools_enabled`, `outbound_count` (salientes de herramientas más burbujas) y `model_tier`. El nivel es el configurado (`agent_configs.nivel_inteligencia`) y queda nulo si el negocio trae su propio modelo; qué modelo corresponde a cada nivel es de TASK-445.
- **Prompt.** El ESTADO suma la línea `Hoy: lunes 12 de octubre de 2026, 10:00` (la fecha que el prompt viejo daba y el nuevo había perdido, `_format_today` del builder viejo); los snapshots se regeneraron. "Cliente nuevo" sigue sin hecho que lo indique.
- **Qué NO se hizo.** El circuit breaker del paso 7 (C-02) es de TASK-445. El enmascarado de datos personales en `agent_traces.tools_called` (C-20) es de TASK-427: hoy `_summarise` sigue guardando los argumentos tal cual. El prompt viejo (`build_system_prompt`, `consultar_catalogo`) no se retiró: lo usan `evals/` y los tests de la 003 (`test_agent_core`, `test_prompt_builder`), y los evals no se corren en esta task; se retira cuando TASK-447 rehaga los casos dorados sobre el pipeline.
- **Decisiones abiertas para Giannier.** (1) La migración 0021 (`messages.payload` y los dos tipos) no estaba en `data-model.md` ni en ninguna task; la 007 planea su propio `ADD COLUMN payload` en `messages` (`007-conversaciones-inbox/plan.md`), así que habrá que dejar uno solo. (2) Los mensajes de la ráfaga se envían al modelo dos veces (en el historial y como mensaje final), como ya hacía la 003; no se tocó. (3) Evals del PR: pendientes de correr (`make evals`) porque cambió el prompt que ve el modelo en el canal real y en el simulador.

### 8.18 Simulador: salientes tipados y modo depuración (TASK-422)

- **Rutas (las del repo, `conciliacion.md` 3.2).** Basadas en la conversación, no `/sandbox/messages` a secas: `POST /tenant/sandbox/conversations/{id}/messages` (JSON `{content}`, `{interactive_id, title}` o multipart con `image`; `?debug=true`), `GET .../messages?after=<message_id>` y `POST .../reset` (204). Una petición debe traer `content` o `interactive_id`, no ambos, y un toque exige su `title` (400 `validation_error`).
- **Respuesta.** `salientes[]` (en el orden que vería el cliente: lo que mandaron las herramientas y luego el texto), `reply` y `trace` (ahora nulos si el turno no produjo texto o no llegó al modelo) y `debug` solo con `?debug=true`. Cada saliente trae su `tipo` (`texto`, `imagen`, `documento`, `video`, `audio`, `botones`, `lista`) y los campos de ese tipo; los archivos llevan un link firmado de 10 minutos que se genera al leer (uno guardado caducaría con el historial) y `url: null` si el archivo ya no está en el almacenamiento. El historial (`GET` sin `after`) devuelve el mismo `saliente` en cada mensaje saliente, así recargar dibuja lo mismo.
- **`debug`** (`contracts/api.md` 4): `etapa_antes` es la etapa guardada al empezar el turno y `etapa_despues` la del final; `hechos` son los guardados y derivados sin nombre del cliente ni resumen de derivación (el mismo recorte de `facts_snapshot`, C-20); `herramientas_llamadas` lleva el `status` de cada una; `modelo` es el nivel configurado (o el modelo si el negocio trae el suyo). Se añadió `silencio` (`handover`, `paused`, `opted_out`) para cuando el asistente se calló.
- **`GET ?after=`** devuelve los mensajes salientes posteriores al ancla (asíncronos: aviso de pago, derivación, seguimiento; hoy nada los escribe, los escribirán el router de Pedidos (aviso de pago, TASK-430), la derivación (TASK-443) y el seguimiento (TASK-444)). Un ancla que no es de la conversación o del negocio se lee como "sin ancla". El panel consulta cada 3 s y solo si no hay otra cosa en vuelo.
- **Contacto sandbox.** `SandboxService.start` crea el contacto `external_user_id = "sandbox:{user_id}"` sin teléfono (uno por usuario del panel, `ContactRepository.upsert`) y lo ata a la conversación; una conversación abierta antes lo recibe en su primer mensaje. Nada sandbox llega al proveedor: el adaptador del simulador (`SandboxOutbound`) solo escribe la fila y el pipeline salta los gates de pausa y opt-out. **Pendiente:** filtrar estos contactos de los listados de contactos; hoy no hay listado (llega con la 007).
- **Imagen.** El simulador acepta JPEG, PNG o WebP de hasta 5 MB, guarda la fila `image` (`[imagen]`) y el modelo la recibe como contexto del turno. **No se guarda el archivo ni se lee**: eso es el `ProofPipeline` de TASK-429 (MinIO privado y visión).
- **`POST .../reset`** (`SandboxResetRepository`, el único código que borra estas tablas) elimina comprobantes, pedidos sandbox (sus líneas van en cascada), trazas, estados de entrega, mensajes, eventos de etapa y el estado de **esa** conversación; la conversación y su contacto se quedan, así el panel conserva el id. Rechaza (`TenantScopeError`) una conversación que no es sandbox del negocio. El botón "Reiniciar conversación" ya no abre otra conversación: llama a esto.
- **Frontend** (`app/(panel)/sandbox/page.tsx`): dibuja texto, imagen, archivo, botones y lista (una lista se abre con su botón; hasta 10 filas); tocar una opción manda su `interactive_id` y el globo del cliente muestra el título; botón "Enviar imagen"; casilla "Modo depuración" con el panel (etapa antes → después, embudo, herramientas habilitadas y llamadas, hechos, tokens). `lib/api/schema.d.ts` se regeneró solo en lo del simulador: el generador también arrastró los campos de venta de `products` (TASK-406) y rompía el `tsc` de `step-catalog.tsx`, que no se tocó en esta task.
- **Verificación.** `frontend/e2e/sandbox-simulator.spec.ts` corre contra el stack real: el negocio se pasa a la plantilla `certificaciones` (no hay API para elegirla hasta TASK-435) y los botones se escriben como un saliente asíncrono, porque el modelo offline no llama herramientas y uno real decidiría solo; tocar el botón cambia la etapa en el panel de CALIFICAR a IDENTIFICAR y el hecho `camino` queda guardado. El mismo recorrido, con el modelo simulado, está en `tests/integration/test_sandbox_simulator.py`. `e2e/sandbox.spec.ts` pierde la comprobación "texto oculto" tras reiniciar (solo se cumplía mientras se veía el esqueleto, y el texto es también una sugerencia del estado vacío) y la cambia por "sin traza y solo la sugerencia"; sus clics en "Enviar" ahora son `exact` porque existe "Enviar imagen". El e2e usa `docker exec` sobre Postgres como `fixtures.ts` usa Redis y necesita `E2E_OUTBOX=../backend/.tmp/outbox.jsonl` con el stack de desarrollo.
- **Decisiones abiertas para Giannier.** (1) En el e2e los botones se inyectan por la base; si prefieres que el modelo offline pueda llamar `mostrar_opciones`, hay que cambiar el principio de `OfflineLLMProvider` ("nunca pide herramientas"). (2) El stack de desarrollo corre con `LLM_PROFILE=openai`: el e2e hace unas 5 llamadas reales al modelo.

## Fase B · Venta

### 8.19 Pricing y promociones (TASK-424)

- **`backend/app/orders/pricing.py`**: `price(items, promociones_vigentes, promos_ofrecidas, now, *, entrega=None) -> Totales`, función pura (sin sesión, sin reloj, sin `float`; redondeo a centavos "half-up"). La firma de `tasks.md` no traía la entrega: se agregó `entrega: Delivery | None` (tipo, zona, zonas con costo y `envio_gratis_desde`), que sale de *Información del negocio → Entregas*. Los datos entran como valores simples (`Line`, `Rule.from_promotion`), así los tests de tabla no necesitan base de datos.
- **Qué hace.** Cada promoción vigente que califica vale un ahorro sobre el pedido; se aplica la de mayor ahorro y nunca se acumulan (C-10). Empate: la automática primero, luego el nombre. Una promoción de cierre solo cuenta si fue ofrecida (`promos_ofrecidas[id] > now`); una no ofrecida y una vencida valen cero. `Totales.candidatas` lista todas las que calificaron.
- **Decisiones de lectura (no están en los docs; a ratificar).**
  1. `monto_minimo` se compara con el subtotal de **las líneas a las que aplica la promoción** (no con todo el pedido). Es la lectura más conservadora.
  2. N×M y precio por cantidad cuentan **por línea** (las unidades de un mismo producto); mezclar dos productos distintos nunca completa un grupo.
  3. El envío gratis (`envio_gratis_desde`) se evalúa sobre los bienes **después del descuento**.
  4. Envío: recojo cuesta 0; delivery sin zona elegida o con una zona desconocida cuesta 0 (la zona es un dato faltante, no un precio); delivery en un negocio sin zonas queda "a coordinar" (0, con la marca `envio_a_coordinar`).
- **Cierra la decisión abierta de §8.7 punto 2 y §8.12.** `facts.promos_cierre` ya no lista todas las promociones de cierre vigentes: con un pedido que tiene líneas, solo las que `pricing.applies_to_order` considera aplicables (vigencia, alcance por productos o categorías, monto mínimo y cantidad). Sin líneas (todavía no hay pedido) se listan todas, porque "lo pensaré" antes de armar el pedido es justo cuando se ofrece un cupón. Para eso `ConversationSnapshotRepository` trae también `products.category` en su tercera sentencia (siguen siendo 3 lecturas).
- **Cobertura.** `orders/pricing.py` 100 % de líneas y ramas (`tests/unit/test_pricing.py`: porcentaje, monto fijo, 2×1, 3×2, precio por cantidad, monto mínimo, vigencia, cierre no ofrecido, cierre vencido, alcance, envío y envío gratis).

### 8.20 OrderService (TASK-425)

- **`backend/app/orders/service.py`**: `OrderService(session, tenant_id, offers=None, now=None)`. Es el único código que escribe pedidos y líneas (la tool `gestionar_pedido`, el pipeline de comprobantes y la API de Pedidos pasan por él). `offers` son las promociones de cierre ofrecidas en la conversación (`promos_ofrecidas`): entran como datos simples porque viven en el estado de la conversación, no en el pedido. `now` se puede fijar en los tests.
- **Alcance.** `tasks.md` pide pedido activo, pedido nuevo tras uno confirmado, numeración, transiciones, snapshot y `is_sandbox`. Como `OrderService` depende de `pricing` (TASK-424) y la regla de C-21 ("cualquier cambio apaga `rechazo_vigente`") es suya, también tiene las operaciones sobre líneas que `gestionar_pedido` necesita: `add_product`, `set_quantity`, `remove_product`, `clear`, `add_selection`, `remove_selection`, `set_delivery`, `recalculate`/`reprice`. Cada una vuelve a calcular con `pricing` y guarda subtotal, descuento, envío, total y la promoción con su snapshot.
- **Pedido activo (C-06).** `open_draft(conversation, ...)` devuelve el borrador activo o crea uno. Si el último pedido es `confirmado`, `en_curso` o `completado` crea uno nuevo; si es `por_confirmar` lanza `OrderLocked` (**decisión mía, no está en los docs**: mientras el negocio revisa, el cliente no puede cambiarlo ni abrir otro; hoy no se alcanza porque la etapa VERIFICACION no habilita `gestionar_pedido`). Un `descartado` no cuenta.
- **Transiciones.** `TRANSITIONS` replica la matriz de `data-model.md` 0019 y `InvalidTransition` rechaza el resto (incluye `confirmado -> completado` sin pasar por `en_curso` y cualquier salida de `completado`/`descartado`). `por_confirmar` exige los datos del cliente (`datos_cliente`) y los copia en ese momento (`customer_snapshot`: lo guardado más el teléfono desde el que escribe); `confirmado` sella `confirmed_at`/`confirmed_by`; volver a `borrador` guarda `motivo_rechazo` y `rechazo_vigente`. `submit()` además exige que el pedido esté completo (`NotReady`).
- **Precios.** Una línea copia nombre y precio del producto al agregarlo (un cambio de precio posterior no la reescribe). El mismo producto dos veces es una línea con las cantidades sumadas (tope 999). Un producto en cotización se rechaza (`NotSellable`). `max_lines` (servicios: 3) lanza `OrderLimit`.
- **Selecciones de un paquete.** Un paquete con `bundle_permite_libres` acepta cualquier nombre; si no, solo productos activos del catálogo que no sean paquetes (`SelectionRefused('not_in_catalog')`, con hasta 10 sugerencias). Una selección que coincide con un producto del catálogo guarda su `product_id`. Sin duplicados dentro del pedido y sin pasar de `bundle_size x cantidad`. Sin `package_id` va al primer paquete incompleto; bajar la cantidad recorta las selecciones que ya no caben. Las líneas se guardan con `created_at` del reloj del servicio (no `now()`), para que dos productos agregados en un mismo turno conserven su orden.
- **Entrega.** `set_delivery` valida contra la sección Entregas (solo recojo -> no admite delivery; solo delivery -> no admite recojo; zona inexistente -> `InvalidDelivery` con las zonas válidas; recojo borra la zona; sin zonas configuradas el costo es "a coordinar"). Valida todo antes de escribir. `requires_delivery(order)` repite la regla de `facts` (algo físico en el pedido y un negocio con delivery o recojo) con datos frescos.
- **Concurrencia.** `OrderRepository.add_numbered` ya tomaba el número con un único `INSERT ... ON CONFLICT DO UPDATE ... RETURNING` (§8.3); `tests/integration/test_order_numbering_concurrency.py` lo prueba con 8 conexiones reales y 24 pedidos: números consecutivos, sin duplicados. Confirma filas reales y las borra.
- **Aislamiento.** `ProductRepository.by_ids` es nuevo (una consulta, solo del tenant). `tests/isolation/test_order_service_isolation.py` entrega al servicio de A pedidos, conversaciones y productos reales de B y exige `TenantScopeError`.
- **Cobertura.** `orders/service.py` 97 %, `orders/pricing.py` 100 % (el grupo crítico exige 80 %).
- **No incluye** el job `orders.discard_stale` (7 días sin actividad), ni confirmar/rechazar desde el operador (TASK-430), ni crear el comprobante (TASK-429); `transition` ya acepta lo que esos necesitarán.

### 8.21 Herramienta `gestionar_pedido` (TASK-426)

- **`backend/app/agent/tools/orders.py`**. Una sola función por acción (`agregar`, `quitar`, `cantidad`, `elegir`, `ver`, `vaciar`, `aplicar_promocion`, `confirmar`) sobre `OrderService`. Los argumentos nunca traen un precio: nombre y precio salen del catálogo y los totales de `pricing`; un `precio` o `total` enviado por el modelo se ignora (hay un test). Devuelve siempre el pedido completo en `data.pedido` (o `null`), con la forma del ejemplo de `contracts/api.md §7` más `producto_id`, `subtotal` de línea, `tipo_entrega`, `zona_envio` y `promocion = {nombre, ahorro}`; el texto que lee el modelo es prosa con los montos ya formateados en la moneda del negocio.
- **Producto en cotización.** `agregar` devuelve `no_disponible` con "usa `derivar_a_humano` con motivo `cotizacion` y un resumen", y no crea el pedido.
- **`elegir`.** Acepta uno o varios temas separados por coma, punto y coma o salto de línea (**convención mía**: el contrato dice `seleccion?` en singular, pero el cliente suele dictar la lista de una vez). Con `producto_id` y sin `seleccion`, el producto del catálogo es lo que se elige; con ambos, `producto_id` es el paquete de destino. Un tema repetido es `omitido`; si una lista se corta a la mitad se conserva lo elegido y se avisa lo que sobró. Cada tema elegido se anota en `ToolContext.extra_names`.
- **Lista blanca del `catalog_guard`.** `AgentCore._catalogue_guard()` recibe ahora los nombres elegidos en el turno (`extra_names`) y las selecciones que ya están en el pedido (`facts.pedido`). Sin esto, "Excel avanzado" con un producto "Excel" en el catálogo se marcaba como inventado. Test: con la elección, la respuesta pasa sin ronda de corrección; sin ella, el mismo texto sigue bloqueado.
- **Caché de herramientas por turno.** Hallazgo de §5.2: la caché de `core.py` servía `ver` con el pedido viejo. Ahora una herramienta marcada `effect=True` (`ToolDefinition.effect`, solo `gestionar_pedido` por ahora) se sirve de la caché únicamente mientras no se haya ejecutado otra escritura desde que se guardó: un `agregar` idéntico repetido por el modelo no suma dos veces, y un `ver` posterior a un `agregar` ejecuta de nuevo. Una escritura que falla invalida lo guardado. Las herramientas de solo lectura siguen en caché igual que antes.
- **`aplicar_promocion`.** Solo promociones de cierre vigentes (otra cosa: `no_disponible`). Con pedido que tiene líneas, rechaza una que no aplica (alcance, monto mínimo o cantidad: `pricing.applies_to_order`) y no registra la oferta. Registra `promos_ofrecidas[id] = ahora + vigencia_horas` en `facts_patch` (se reescribe el diccionario completo y se descartan las ofertas vencidas); si ya había una oferta vigente se conserva su vencimiento (decir "lo pensaré" dos veces no la alarga). Sin pedido, devuelve `hecho` con "se aplicará cuando arme su pedido". Si otra promoción (automática o de cierre) ahorra más, queda aplicada esa y se le dice al modelo que no se acumulan.
- **`confirmar`.** Además del guard por etapa (COBRO + contra entrega, de TASK-417), la acción comprueba en base de datos que el pedido sea borrador, `contra_entrega` y completo, y que no falten datos del cliente (usa la misma función que los hechos, `missing_customer_data`); luego `OrderService.submit` lo deja `por_confirmar` con la copia de `datos_cliente`. **No** crea el aviso al operador (`pedido_contra_entrega`): eso es de TASK-431.
- **Contexto.** `ToolContext` suma `max_lines` (la plantilla `servicios` limita a 3 líneas; lo rellena `for_stage` desde `Template.max_lineas`) y `extra_names`.
- **No se tocó** `MAX_TOOL_ROUNDS = 2` (§5.2): `buscar` + `agregar` consumen las dos vueltas, y sumar `registrar_datos_cliente` en cadena no cabe. Subirlo cambia costo y latencia; **decisión abierta para Giannier** (hoy el modelo puede llamar varias herramientas en paralelo dentro de una misma vuelta, que es lo que cubren los tests).
- **Cobertura.** `agent/tools/orders.py` 97 %, `agent/core.py` 100 %, `agent/tools/registry.py` 100 %.

### 8.22 Herramienta `registrar_datos_cliente` y enmascarado (TASK-427)

- **`backend/app/agent/tools/customer.py`** (global, `effect=True`). Guarda en `contacts.custom_data` lo que el cliente da, **parcial** y campo por campo: lo válido se guarda, lo inválido vuelve en `invalidos` con una razón que no repite el valor, y `faltan` se calcula siempre con datos frescos. `faltan` usa `missing_customer_data` (extraída de `facts.py`), la misma función de `datos_faltantes`, así que no pueden discrepar. Si no se guarda nada y hay inválidos, responde `error`; con algo guardado, `hecho`.
- **Validación** (`backend/app/orders/customer_data.py`, `data-model.md` 0020): DNI de 8 dígitos, RUC de 11, correo bien formado (en minúsculas), teléfono en E.164, fecha de nacimiento (`AAAA-MM-DD` o `DD/MM/AAAA`, no futura), textos de largo acotado y con letras. Un número JSON (`dni: 12345678`) se lee como texto. **Decisión mía a ratificar:** un teléfono de 9 dígitos que empieza en 9 se toma como móvil peruano y se guarda como `+51...`; sin eso el cliente peruano que escribe "987654321" quedaría inválido. También se normalizan DNI/RUC (se quitan espacios y guiones).
- **Campos que acepta:** `nombres` siempre, los `datos_requeridos` de la plantilla y, solo si el pedido requiere entrega (`OrderService.requires_delivery`), `tipo_entrega`, `direccion`, `referencia` y `zona_envio`. Un producto digital no pide dirección; un campo no pedido vuelve en `invalidos`. `tipo_entrega` y `zona_envio` van al pedido vía `OrderService.set_delivery` (recalcula el envío; una zona inexistente devuelve `zonas_validas`; recojo borra la zona). Si el negocio solo ofrece recojo y el pedido requiere entrega, `tipo_entrega = recojo` se fija solo al llamar la tool. Reenviar el mismo dato no cuenta como cambio; un dato nuevo apaga `rechazo_vigente` (C-21).
- **Enmascarado (C-20), `backend/app/agent/privacy.py`.** `mask_arguments` enmascara por **nombre de campo** (DNI/RUC/teléfono `****5678`, correo `a***@dominio`, el resto `***`) y `_summarise` de `core.py` lo aplica antes de cortar a 120 caracteres: `agent_traces.tools_called.args_resumen` y lo que el simulador devuelve al panel ya no traen datos personales. La respuesta de la tool al modelo nombra campos, nunca valores. Tests: ningún valor aparece en `agent_traces` ni en los `facts_snapshot` de los eventos tras un turno real con el pipeline.
- **No cubierto (decisión abierta para Giannier):** el DNI que el cliente escribe queda en `messages.content` y vuelve al LLM en el historial, como advierte §5.2; enmascararlo exige una política (¿redactar al guardar el mensaje?) que no está en los docs. Tampoco hay un redactor de logs general: los logs del repo no escriben argumentos de tools.
- **Cobertura:** `tools/customer.py` 98 %, `orders/customer_data.py` 100 %, `agent/privacy.py` 100 %.

### 8.23 Herramienta `enviar_metodos_pago` (TASK-428)

- **`backend/app/agent/tools/payments.py`** (`effect=True`). Envía un texto con los métodos activos (`payment_methods_business.instructions` tal como las escribió el dueño; el modelo no redacta cuentas) y después la imagen o QR. Guarda `metodos_pago_enviados = {order_id, en}` en `facts_patch`; eso mueve el embudo de COBRO a CUENTAS_ENVIADAS (`funnel_for`) y hace que la segunda llamada devuelva `omitido`.
- **Interfaz de salida.** `TurnOutbound` suma `send_text(body)`; `LiveOutbound` lo manda por el proveedor (con la ventana de 24 h y el mismo reintento) y `SandboxOutbound` solo escribe la fila. Ambos dejan una fila `messages` de tipo `text` sin `payload` (el simulador la dibuja como texto). Cambio pequeño de interfaz no previsto en `tasks.md`: sin él la tool no podía mandar el texto.
- **Una vez por pedido (C-21).** Usa el hecho derivado `metodos_pago_enviados` (pedido activo y posterior al último rechazo) comparando el `order_id` del registro con el pedido activo en el momento de la llamada; así un pedido creado en el mismo turno tras uno cerrado sí los recibe. Tests: segunda vez `omitido`; reenvío tras un rechazo (una sola vez); pedido nuevo después de CERRADO vuelve a recibirlos; embudo CUENTAS_ENVIADAS con el pipeline completo.
- **Imagen.** La de cada método (`media_asset_id`, sin repetir), y si ningún método la tiene, el recurso con clave `metodos_pago` si está activo. Las claves enviadas se suman a `recursos_enviados`. **Presupuesto (C-11):** el texto y cada imagen gastan un mensaje; sin lugar para el texto no se envía ni se registra nada ("ofrécelo en tu próximo mensaje"); con lugar solo para el texto, sale el texto, se registra y se avisa que falta la imagen (`imagenes_pendientes`); si falla el texto no se registra nada.
- **Solo pedidos en borrador.** Sin pedido, o con uno `por_confirmar`, devuelve `no_disponible`. Sin métodos configurados, `no_disponible` con la instrucción de usar `derivar_a_humano` (no inventar cuentas). El texto de la tool incluye el total del pedido leído de la base para que el modelo no lo calcule.
- **Cobertura:** `tools/payments.py` 100 %.

### 8.24 ProofPipeline y visión (TASK-429)

- **Visión: puerto aparte, no se toca `LLMProvider`.** `§5.2` y `C-08` hablaban de ampliar `LLMProvider`, una interfaz protegida. En su lugar `providers/llm/base.py` agrega un puerto pequeño, `VisionProvider.read_image(image, mime_type, *, instruction, schema, timeout_seconds) -> VisionReading`, que `OpenAICompatibleProvider` implementa con `httpx` (imagen en línea como data URL base64 y `response_format` con JSON schema; sin SDK ni Pillow). `MockLLMProvider` lo guioniza (`vision_responses`, `vision_delay`) y `OfflineLLMProvider` lo rechaza (un hash no lee comprobantes; el comprobante queda sin datos). Quien llama pregunta `isinstance(llm, VisionProvider)`.
- **Qué modelo lee.** El de chat del perfil (`vision_model` es opcional y por defecto es el chat). `plan.md` dice "con el perfil `openai` usa el modelo del nivel medio": qué modelo corresponde a cada nivel es de TASK-445, así que hoy no se distingue. **Decisión abierta:** con el perfil gratuito de NVIDIA el modelo de chat probablemente no ve imágenes; la llamada falla y el comprobante queda `sin_datos`.
- **`orders/vision.py`.** `read_proof()` nunca lanza: sin visión, timeout (8 s, `asyncio.wait_for` más el timeout HTTP), error o respuesta que no sigue el schema devuelven `ProofReading(legible=False, reason=...)`. Los montos se piden como texto y se convierten a `Decimal` (`"S/ 1,250.50"`, `"120,50"`); moneda ISO (`S/` -> PEN, `US$` -> USD); fecha ISO; confianza entre 0 y 1 o descartada.
- **`orders/proofs.py`.** `ProofPipeline.handle(conversation, image, message_id)`:
  - Solo actúa sobre el pedido activo si es `borrador` y `modalidad_pago = previo`. Contra entrega no tiene comprobante (C-07): una imagen ahí es solo contexto. Sin pedido o con uno ya `por_confirmar` tampoco.
  - Si la visión dice que **no** es comprobante no guarda nada (ni imagen ni fila) y el modelo recibe "la imagen NO es un comprobante...". Si **no se pudo leer** (sin visión, timeout, error) es otro caso: se guarda como comprobante `sin_datos` para que el operador lo vea.
  - Guarda la imagen en `tenants/{tenant}/proofs/{uuid}.{ext}` (almacenamiento privado), crea el `payment_proof` `por_verificar` con `extraccion` (salida cruda, sin imagen) y pasa el pedido a `por_confirmar` con `OrderService.submit` (copia `datos_cliente`). Si el pedido no se puede enviar (incompleto) el comprobante queda registrado y se avisa igual.
  - **Observaciones** (nunca rechazo): `monto_distinto` (monto o moneda distintos del total del pedido), `codigo_repetido`, `baja_confianza`, `sin_datos` (ni monto ni código legibles). El umbral de baja confianza es `0.70` (`LOW_CONFIDENCE`): **no está en los docs**, es una constante a mover cuando haya comprobantes reales.
  - **`codigo_repetido`**: compara sin mayúsculas, dentro del tenant, **solo entre pedidos del mismo tipo**: un comprobante sandbox nunca cuenta contra uno real (C-19) y el simulador sí puede mostrar el aviso contra sus propios comprobantes (`PaymentProofRepository.code_seen`). Lectura mía de "no cuenta para `codigo_repetido`".
  - El aviso al operador entra por `ProofNotices.new_proof(order, proof)` (un puerto): lo implementa TASK-431. Los pedidos sandbox no avisan a nadie.
- **Paso 4 del pipeline (`agent/pipeline.py`).** Corre el `ProofPipeline` cuando hay imágenes en la ráfaga y `stage_previa` es COBRO, o VERIFICACION con `pago_estado = rechazado` (RF-022), **también con derivación o pausa** (antes del gate). Si registró un comprobante recarga los hechos y guarda la etapa con `reason = "comprobante"`. El modelo recibe el resultado en "ESTE TURNO" (`TurnContext.comprobante`) y la etapa VERIFICACION en el ESTADO. Una imagen que no se pudo descargar dice "pídele que la envíe de nuevo". Fuera de esas etapas, o sin lector/almacenamiento, sigue siendo la línea `[El cliente envió una imagen]`.
- **Imagen entrante del canal real (`§5.2`, resuelve "quién descarga").** El parser de YCloud ahora lee `whatsappInboundMessage.image` (`id`, `link`, `mime_type` o `mimeType`, `caption`) en `InboundMessage.media` (`InboundMedia`); `ChannelEventProcessor` lo guarda en `messages.payload.media` (la columna existe desde 0021: **sin migración**). El turno descarga con `WhatsAppProvider.download_media(credentials, link=, max_bytes=)` (nuevo en el puerto y en `YCloudProvider`): `GET` con `X-API-Key`, como pide la doc de YCloud ("always include an `X-API-Key` header to download this file within 30 days"), tope de 5 MB, y **solo si el enlace es del host de YCloud**: la llave nunca se manda a otro dominio aunque el payload lo nombre. Fuente de la doc: `docs.ycloud.com`, ejemplos de webhook de mensajes entrantes (consultada el 2026-10-13). El fixture `inbound_image.json` pasó a la forma documentada (`link`, `mime_type`).
- **Simulador.** `POST .../messages` con `image` ahora guarda el archivo en `tenants/{tenant}/sandbox/{uuid}.{ext}` y la fila `image` lleva `payload.image = {storage_key, mime_type}`; sin almacenamiento sigue siendo solo contexto. Lo lee `InboundImageReader` (`services/inbound_media.py`), que sirve a los dos canales. `SandboxService.send(image=ReceivedImage | None)` reemplaza al booleano. El objeto de la fila `image` del simulador no se borra al reiniciar (el reinicio no toca el almacenamiento): queda huérfano e inerte.
- **Cobertura.** `orders/vision.py`, `orders/proofs.py`, `services/inbound_media.py` y `process/inbound.py` están cubiertos por `test_vision.py`, `test_proof_pipeline.py`, `test_inbound_images.py` y `isolation/test_proofs_isolation.py`; el adaptador `read_image` y `download_media` por `test_vision.py` y `test_ycloud_provider.py`. Nada llama a un modelo real.
- **Para revisión humana:** toca el flujo de pagos (el sistema nunca confirma un pago, solo registra y marca).

### 8.25 API de pedidos y avisos al cliente (TASK-430)

- **Rutas (las del repo, §3.2).** `/api/v1/tenant/orders`: `GET ""` (lista), `GET /summary`, `GET /{id}`, `GET /{id}/proofs/{proof_id}/image`, `POST /{id}/confirm`, `POST /{id}/reject`, `POST /{id}/status`. Cualquier miembro activo del negocio puede leer y decidir (`MemberDep`; "operador" = cualquier miembro). Errores con el sobre `ApiError`: `not_found` 404 (también el pedido de otro negocio), `already_reviewed` y `invalid_transition` 409, `validation_error` **400** (no 422, convención del repo).
- **Lista.** Filtros `estado` (repetible; uno desconocido es 400), `desde`/`hasta` (fechas, inclusivas, sobre `created_at` en UTC), `q` (número con o sin `#`, nombre que dio el cliente, nombre de WhatsApp o parte del teléfono; `%` y `_` son literales), `sandbox=false`, `page`, `page_size` ≤ 50. Teléfono enmascarado (`+51 9•• ••• 628`) y nombre abreviado (`Ana P.`); en el detalle va completo. Un teléfono que no es +51 muestra solo los 3 últimos dígitos.
- **Resumen.** `periodo=hoy|7d|30d` (`hoy` empieza a medianoche en la zona horaria del negocio). `conversiones` y `valor_total` cuentan los pedidos con `confirmed_at` en el periodo que están en `confirmado`/`en_curso`/`completado`; `por_estado` y `pendientes_revision` cuentan los pedidos **creados** en el periodo. **Lectura mía:** el contrato no dice si "pendientes" mira el periodo o todo el tiempo; `spec.md` RF-024 dice "pendientes por periodo", así que sigue el periodo (un pedido por confirmar de hace 8 días no aparece en "hoy"; el panel debe ofrecer 30 d para verlo). Nunca cuenta pedidos del simulador.
- **Detalle.** Ítems con selecciones y las que faltan, totales, promoción (el snapshot), entrega, `datos_cliente` completos (el snapshot del pedido, o los datos actuales del contacto si todavía es borrador), comprobantes **sin** `storage_key` ni URL, `ventana_abierta` (siempre true en sandbox) e `historial`. **Desvío:** no existe una tabla de historial de estados del pedido y crearla es una migración que ninguna task pide; el `historial` se arma con lo que hay: creación, cada comprobante recibido y las acciones del operador de `audit_logs` (`order.confirmed`, `order.rejected`, `order.status_changed`, con el estado de origen y de destino). Los cambios que hace el sistema (p. ej. un borrador descartado a los 7 días) no aparecerán hasta que exista ese job.
- **Imagen del comprobante.** URL firmada de 10 minutos (`presigned_url(expires_in=600)`) más `expires_at`. Un comprobante de otro pedido o de otro negocio es 404.
- **Confirmar / rechazar (`orders/review.py`, `OrderReviewService`).** Una transacción: se bloquea la fila del pedido (`SELECT ... FOR UPDATE`, así una doble confirmación hace cola y la segunda recibe 409), el comprobante pendiente más reciente pasa a `aprobado` o `rechazado` (con quién y cuándo), el pedido cambia de estado, se escribe `audit_logs` (`order_id`, número, estado de origen y destino, `proof_id`, motivo; nunca datos del cliente) y se recalcula la etapa con `process/recompute.py` (`operador:confirmar`, `operador:rechazar`, `operador:estado`).
  - `already_reviewed` = el pedido ya estaba `confirmado`/`en_curso`/`completado`. Cualquier otro estado que no sea `por_confirmar` es `invalid_transition`.
  - Rechazar con comprobante deja el pedido en `borrador` con `rechazo_vigente = true`; sin comprobante (contra entrega) con `rechazo_vigente = false` y el aviso `pedido_rechazado`. El `motivo` va de 5 a 300 caracteres (sin espacios sobrantes).
  - **Un evento solo cuando cambia algo.** `StateRepo.save` no escribe evento si no cambia la etapa ni el embudo (§8.8). Al rechazar un pago con comprobante la etapa ya era VERIFICACION (por verificar) y sigue siéndolo (rechazado): no hay evento `operador:rechazar`, aunque el contrato lo nombre. Con contra entrega la etapa pasa de VERIFICACION a COBRO y sí lo hay. Lo mismo `POST /status`: solo deja evento `operador:estado` si cambia la etapa (descartar un pedido por confirmar, sí; pasar de confirmado a en curso, no).
- **Aviso al cliente (`services/order_notices.py`, `CustomerNotifier`), después del commit y sin deshacerlo nunca.** El texto es el mensaje de sistema que el dueño editó (`agent_configs.system_messages`) o el de `DEFAULT_SYSTEM_MESSAGES`; `{nombre_cliente}` es el nombre que dio el cliente (primer nombre; nunca la etiqueta de WhatsApp), y lo que falta desaparece con su puntuación (`¡Tu pago fue confirmado!`), igual que un `{marcador}` desconocido.
  - Canal real: ventana de 24 h cerrada -> **no se escribe nada ni se llama al proveedor** y la respuesta dice `aviso_cliente: ventana_cerrada`; abierta -> fila `queued` (`author = system`) entregada con `ReplySender` (reintentos, estados de entrega, mismas reglas que una respuesta del bot).
  - Simulador (C-19): solo la fila de saliente, sin proveedor ni ventana; la ve `GET .../messages?after=`.
  - **Valor que el contrato no prevé: `aviso_cliente: no_enviado`.** Cuando el proveedor falla o no hay número conectado, la decisión queda guardada y la respuesta lo dice (`fallido` en la fila). El contrato solo conoce `enviado` y `ventana_cerrada`. **Decisión abierta para Giannier.**
  - Avisos al negocio: `OrderNotices` (`orders/notices.py`) es el puerto por donde se le avisa al operador (`ventana_cerrada`/`envio_fallido`, comprobante nuevo, pedido contra entrega). TASK-431 lo implementa y lo conecta; en esta task el router no pasa ninguno.
- **Cobertura.** `orders/review.py`, `process/recompute.py`, `services/order_notices.py`, `api/v1/orders.py` y `repositories/audit.py` con `tests/integration/test_orders_api.py` (31 casos: acceso sin sesión, operador, otro negocio en cada ruta, filtros, resumen, detalle, imagen, confirmar, doble confirmación, ventana cerrada, simulador, fallo del proveedor, texto del dueño, rechazar con y sin comprobante, estado), `tests/unit/test_order_notices.py` y `tests/isolation/test_orders_api_isolation.py`.
- **No se regeneró `frontend/lib/api/schema.d.ts`** (hace falta el backend levantado con el código nuevo y `npm run api:types`); la TASK-432 lo debe hacer al empezar.
- **Para revisión humana:** toca el flujo de pagos (confirmar o rechazar un pago, mensajes al cliente, auditoría).
