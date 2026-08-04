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
- [ ] **TASK-202: YCloudProvider (envío texto + parseo de webhooks)** — implementación de `WhatsAppProvider` con mapeo de errores; fixtures de payloads; contract tests con firma HMAC. Depends: 201.
- [ ] **TASK-203: Endpoint de webhook + idempotencia + encolado** — persistencia cruda, verificación de firma, 200 <2s, dead-letter tras 5 intentos. Depends: 202.
- [ ] **TASK-204: Pipeline inbound** — upsert de contacto, resolución de conversación (nueva tras 24h), debounce 8s en Redis, lock por conversación, fallback de tipos no soportados. Depends: 203. Verificación: test de ráfaga (5 msgs → 1 respuesta).
- [ ] **TASK-205: Invocación del agente en canal real + envío + estados** — integración agent-core, registro sent/delivered/read/failed desde webhooks de estado, reintentos de envío con backoff. Depends: 204.
- [ ] **TASK-206: Ventana 24h + opt-out + quality rating** — guard de sesión, detección de STOP/BAJA configurable, webhook/poll de quality con alertas. Depends: 204.
- [ ] **TASK-207: Cuota en vivo + notificaciones 80/100%** — incremento atómico, cierre con gracia al agotar, endpoint /tenant/usage, banner en frontend. Depends: 205. Verificación: test de concurrencia (50 simultáneos, contador exacto).
- [ ] **TASK-208: Admin de dead-letters (API+UI) + métricas de canal** — listado, retry manual, métricas p50/p95 por tenant. Depends: 203, 205.
- [ ] **TASK-209: Test de caos + endurecimiento** — kill de worker a mitad, replays masivos, revisión de logs sin PII. Depends: todo. Milestone humano: Giannier prueba con su propio número real end-to-end.

Dependencias: 201 → 202 → 203 → 204 → 205 → 207 → 209; 204 → 206; 203/205 → 208.
