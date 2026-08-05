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
- [x] **TASK-204: Pipeline inbound** — upsert de contacto, resolución de conversación (nueva tras 24h), debounce 8s en Redis, lock por conversación, fallback de tipos no soportados. Depends: 203. Verificación: test de ráfaga (5 msgs → 1 respuesta).
  - **Resultado:** verificación cumplida — 5 mensajes producen 1 respuesta, y el test
    parametrizado lo comprueba con ráfagas de 1, 2 y 10.
  - Debounce: marcador Redis `channel:burst:{conv}` con `SET NX EX 8`. El primer
    mensaje de la ráfaga reserva la ventana y agenda el job diferido 8s; los demás
    encuentran el marcador tomado y no agendan nada. Ventana **fija** desde el primer
    mensaje, no reiniciable: un timer que se reinicia significa que quien sigue
    escribiendo nunca recibe respuesta, y el criterio de aceptación es <10s p95.
    (El plan proponía un sorted set; un marcador con TTL hace lo mismo y es una
    operación en vez de tres.)
  - Lock por conversación: `channel:lock:{conv}`, con token, para que soltarlo tras
    expirar no le borre el lock a otro worker — esa es justamente la forma de que un
    cliente reciba dos respuestas. El job libera el marcador de debounce ANTES de
    leer la transcripción, así un mensaje que llega desde ese punto pertenece a la
    ráfaga siguiente y puede agendar la suya.
  - La ráfaga no se guarda en ningún lado: es todo lo entrante desde el último
    mensaje saliente. Sigue siendo correcto tras un crash, una redelivery o un job
    que corrió dos veces.
  - Tipos no soportados: nueva columna `agent_configs.unsupported_fallback`
    (migración 0008) con default incorporado. Al modelo NO se le pasa `[audio]`:
    mostrarle un placeholder le enseña a hablar de algo que no puede ver. Una foto
    **con** texto sí es pregunta y va al agente.
  - La respuesta se guarda con `delivery_status='queued'` y se contabiliza acá
    (`record_ai_message`), donde se incurre la cuenta del modelo. TASK-205 la envía.
- [x] **TASK-205: Invocación del agente en canal real + envío + estados** — integración agent-core, registro sent/delivered/read/failed desde webhooks de estado, reintentos de envío con backoff. Depends: 204.
  - **Resultado:** `ReplySender` toma la respuesta `queued` y la entrega. Separado de
    producirla porque fallan distinto y solo una vale la pena reintentar: generar la
    respuesta dos veces cuesta una llamada al modelo y arriesga dos respuestas
    distintas; entregar la MISMA respuesta ya hecha otra vez no cuesta nada.
  - Reintentos con backoff 1s/2s/4s, y solo para lo que es reintentable
    (`WhatsAppError.retryable`): timeout, 429 y 5xx sí; credencial rechazada o
    mensaje refutado, no. Tres intentos contra una key mal puesta son tres veces la
    misma respuesta, una respuesta del cliente más tarde que la primera.
  - Estados: `queued` → `sent` → `delivered` → `read`, con `failed` terminal. Los
    webhooks de estado ya los aplicaban desde TASK-203. `failed` gana sobre un
    `delivered` tardío a propósito: un mensaje mal marcado como fallido le muestra al
    dueño algo que revisar, y un fallo pisado por un éxito tardío es una venta
    perdida que nadie le cuenta.
  - **Barrido de respuestas varadas** (`retry_stranded_replies`, cron cada 5 min):
    la ventana entre commitear la respuesta y enviarla. Si el worker muere ahí, el
    job que debía enviarla ya no existe, no hay reintento que dispare ni error que
    ver, y desde el lado del cliente es idéntico a que el negocio lo ignore.
  - **Arreglo colateral, migración 0009:** `messages.created_at` pasa de `now()` a
    `clock_timestamp()`. Postgres congela `now()` al inicio de la transacción, así
    que dos mensajes escritos en la misma quedaban con el MISMO timestamp y el
    `ORDER BY created_at` devolvía lo que quisiera el planner. Ese orden es el
    historial que se le pasa al agente y la definición de "todo lo nuevo desde la
    última respuesta". Salió como test intermitente; era un empate real.
- [ ] **TASK-206: Ventana 24h + opt-out + quality rating** — guard de sesión, detección de STOP/BAJA configurable, webhook/poll de quality con alertas. Depends: 204.
- [ ] **TASK-207: Cuota en vivo + notificaciones 80/100%** — incremento atómico, cierre con gracia al agotar, endpoint /tenant/usage, banner en frontend. Depends: 205. Verificación: test de concurrencia (50 simultáneos, contador exacto).
- [ ] **TASK-208: Admin de dead-letters (API+UI) + métricas de canal** — listado, retry manual, métricas p50/p95 por tenant. Depends: 203, 205.
- [ ] **TASK-209: Test de caos + endurecimiento** — kill de worker a mitad, replays masivos, revisión de logs sin PII. Depends: todo. Milestone humano: Giannier prueba con su propio número real end-to-end.

Dependencias: 201 → 202 → 203 → 204 → 205 → 207 → 209; 204 → 206; 203/205 → 208.
