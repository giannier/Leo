# Tasks — Canal WhatsApp Real

**Feature ID:** 002 · **Estimación:** 1.5–2 semanas · Prefijo commits: `feat(channel): ... [TASK-2XX]`

- [x] **TASK-201: Migraciones del canal** — contacts, webhook_events, message_status_events, ALTERs a conversations/messages. Verificación: upgrade/downgrade limpios; suite de aislamiento extendida a contacts.
  - **Resultado:** migración `0007_whatsapp_channel`. Upgrade y downgrade verificados;
    `alembic check` no detecta deriva y ahora corre en CI. Modelos en
    `app/models/channel.py`, repositorios en `app/repositories/channel.py`.
    `webhook_events` es a propósito la única tabla nueva SIN `tenant_id`: a qué tenant
    pertenece un evento es una conclusión que se saca del payload, y sacarla es
    justamente el paso que puede fallar. `message_status_events` tampoco tiene la
    columna pero sí es dato de tenant, así que su repositorio llega a las filas por
    join contra `messages`. La suite de aislamiento (`test_channel_isolation.py`, 19
    casos) monta a los dos tenants con **el mismo teléfono de cliente**: cualquier
    query que se olvide del filtro devuelve la fila equivocada, no vacío.
- [x] **TASK-202: YCloudProvider (envío texto + parseo de webhooks)** — implementación de `WhatsAppProvider` con mapeo de errores; fixtures de payloads; contract tests con firma HMAC. Depends: 201.
  - **Resultado:** `app/providers/whatsapp/ycloud.py` reemplaza a `ManualYCloudProvider`
    (validar credenciales sigue igual; ahora además envía y lee webhooks). Formato de
    cable verificado contra la doc de YCloud: `POST /v2/whatsapp/messages` con header
    `X-API-Key`; webhook `{id,type,apiVersion,createTime,<objeto>}` con
    `whatsapp.inbound_message.received` y `whatsapp.message.updated`; firma
    `YCloud-Signature: t=<unix>,s=<hex>` HMAC-SHA256 sobre `"<t>.<body crudo>"`.
    Los errores se traducen a la decisión que el que llama necesita: `retryable`
    True para 429/5xx/timeout, False para 401/403 y cualquier otro 4xx.
    Fixtures JSON reales anonimizados en `tests/fixtures/ycloud/`. 44 casos nuevos:
    el parser degrada a `unsupported` y nunca levanta excepción (el evento crudo ya
    está guardado y se puede reprocesar), y la firma rechaza cuerpo alterado, secreto
    ajeno, timestamp cambiado, header malformado y secreto sin configurar.
  - **Config nueva:** `YCLOUD_WEBHOOK_SECRET` (sin él el endpoint rechaza todo, que
    es la falla correcta), `YCLOUD_BASE_URL`, `CHANNEL_DEBOUNCE_SECONDS`,
    `CHANNEL_SEND_MAX_ATTEMPTS`. `deploy/env.reference` regenerado.
- [x] **TASK-203: Endpoint de webhook + idempotencia + encolado** — persistencia cruda, verificación de firma, 200 <2s, dead-letter tras 5 intentos. Depends: 202.
  - **Resultado:** `POST /webhooks/ycloud` (fuera de `/api/v1`, sin JWT, fuera del
    esquema publicado). El endpoint hace tres cosas y nada más: verificar, guardar,
    encolar. Guarda el evento crudo ANTES de entender nada, que es lo que hace
    reprocesable un arreglo del parser. Commitea antes de encolar: si Redis está
    caído devuelve 503, el evento ya está guardado y la redelivery lo vuelve a
    encolar. Nunca 500 — la URL es pública.
  - `app/workers/channel.py::process_event` aplica el evento contra la BD. Un evento
    es una transacción. Nada escapa a ARQ: una excepción se traduce en `attempts+1`
    y, al llegar a 5, `dead_letter` — que NO es descartar (el payload sigue ahí y se
    puede reprocesar). Un mensaje a un número que nadie tiene va directo a
    dead-letter: significa que alguien sigue escribiéndole a un número desconectado.
  - **Regrupación respecto al plan:** el upsert de contacto, la resolución de
    conversación (nueva tras 24h, C-002) y el guardado del mensaje entraron acá y no
    en TASK-204. Un transporte que recibe el webhook y tira el mensaje no es
    entregable, y HU-01 dice que un mensaje no se descarta nunca. TASK-204 queda con
    debounce, lock por conversación y agrupación de ráfagas.
  - 41 tests nuevos entre endpoint y worker.
- [ ] **TASK-204: Pipeline inbound** — upsert de contacto, resolución de conversación (nueva tras 24h), debounce 8s en Redis, lock por conversación, fallback de tipos no soportados. Depends: 203. Verificación: test de ráfaga (5 msgs → 1 respuesta).
- [ ] **TASK-205: Invocación del agente en canal real + envío + estados** — integración agent-core, registro sent/delivered/read/failed desde webhooks de estado, reintentos de envío con backoff. Depends: 204.
- [ ] **TASK-206: Ventana 24h + opt-out + quality rating** — guard de sesión, detección de STOP/BAJA configurable, webhook/poll de quality con alertas. Depends: 204.
- [ ] **TASK-207: Cuota en vivo + notificaciones 80/100%** — incremento atómico, cierre con gracia al agotar, endpoint /tenant/usage, banner en frontend. Depends: 205. Verificación: test de concurrencia (50 simultáneos, contador exacto).
- [ ] **TASK-208: Admin de dead-letters (API+UI) + métricas de canal** — listado, retry manual, métricas p50/p95 por tenant. Depends: 203, 205.
- [ ] **TASK-209: Test de caos + endurecimiento** — kill de worker a mitad, replays masivos, revisión de logs sin PII. Depends: todo. Milestone humano: Giannier prueba con su propio número real end-to-end.

Dependencias: 201 → 202 → 203 → 204 → 205 → 207 → 209; 204 → 206; 203/205 → 208.
