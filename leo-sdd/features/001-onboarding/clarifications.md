# Clarifications — Onboarding de Tenant + Conexión de Número

**Feature ID:** 001
**Fecha:** 2026-07-30
**Registrado por:** Decisiones delegadas por Giannier a Claude, fundamentadas en el reporte de investigación comparativa de mercado (Wati, Respond.io, Darwin, Zaia, AiSensy, Kommo, Cliengo, Interakt, SleekFlow, ManyChat, Treble, YCloud). Pendientes de ratificación final por Giannier.

---

## Clarification 001: Duración y límites del trial
**Situación:** El spec dejaba abiertos la duración del trial y su límite de mensajes.
**Decisión:** Trial de **14 días, sin tarjeta de crédito**, con tope de **250 mensajes de IA** (se cuenta cada respuesta generada por el agente en canal real WhatsApp). Lo que ocurra primero (día 14 o mensaje 250) finaliza el trial.
**Justificación (investigación):** 14 días es el estándar en plataformas orientadas a LATAM/PyME (AiSensy, Kommo, Interakt, Cliengo, Clientify); no pedir tarjeta es la norma del mercado y maximiza registros; 250 mensajes ≈ 30–50 conversaciones reales, suficiente para percibir valor sin permitir uso productivo gratuito indefinido.
**Impacto en implementación:** Campos `trial_ends_at` y `ai_messages_used` en `tenants`; contador incrementado por el agent-core solo en canal `whatsapp`; job diario que transiciona tenants vencidos a estado `paused`.

---

## Clarification 002: ¿El sandbox consume cuota?
**Situación:** No estaba definido si los mensajes del modo prueba descuentan del límite del trial.
**Decisión:** **No.** El sandbox es ilimitado y gratuito, durante el trial y después. Solo los mensajes en el canal real consumen cuota.
**Justificación:** El sandbox es donde el cliente ajusta tono y RAG y "se enamora" del producto; cobrarlo desincentiva la experimentación que genera conversión. Ninguna plataforma líder cobra el modo prueba (Zaia lo limita a 10 mensajes y resulta insuficiente — antipatrón a evitar).
**Impacto en implementación:** El agent-core recibe `channel: whatsapp | sandbox`; el medidor de consumo ignora `sandbox`. Guardrail anti-abuso: rate limit de 30 mensajes sandbox/minuto por tenant (protege costos de LLM sin limitar el uso legítimo).

---

## Clarification 003: Operadores por estado/plan
**Situación:** Límite de operadores en trial y planes sin definir.
**Decisión:** Trial: **2 asientos** (dueño + 1). Planes de referencia: Emprende (2 asientos), Crece (5), Pro (10); asiento adicional US$10/mes. En Feature 001 solo se aplica el límite del trial; los límites por plan se leen de la configuración de planes (tabla `plans`) para no acoplar.
**Justificación:** Rango de mercado: ManyChat 3, Wati 5, Gallabox 6; asiento extra US$10–15 (Kommo/Gallabox ~US$15, Treble US$40). Trial no es momento de vender asientos.
**Impacto en implementación:** Validación en servicio de invitaciones contra `plans.max_seats`; el plan `trial` es un registro más de la tabla `plans`.

---

## Clarification 004: Fin del trial y retención de datos
**Situación:** Qué pasa exactamente al agotar el trial (por fecha o por mensajes).
**Decisión:** El tenant pasa a estado `paused`: el agente deja de responder; los mensajes entrantes se siguen recibiendo y almacenando en la bandeja; el panel queda accesible en modo lectura + configuración, con CTA de pago. Avisos automáticos en día −3, −1 y 0 (email) y banner permanente en panel. Si el tope de mensajes se agota a mitad de una conversación, el agente cierra esa conversación con un mensaje de despedida configurado y luego se pausa. Datos retenidos 30 días tras la pausa; luego el tenant pasa a `archived` (soft, recuperable por admin). Eliminación definitiva solo a solicitud del titular (Ley 29733).
**Impacto en implementación:** Máquina de estados del tenant: `trial → paused → archived` (+ `active` al pagar, Feature 009); jobs programados para avisos y archivado; el webhook nunca descarta mensajes por estado del tenant.

---

## Clarification 005: Anti-abuso del registro
**Situación:** Trial sin tarjeta abre la puerta a cuentas descartables que queman cuota de LLM.
**Decisión:** Verificación de **email obligatoria** para entrar al panel + verificación del **teléfono de contacto por OTP (WhatsApp/SMS)** requerida antes de crear la solicitud de conexión de número. Un mismo teléfono de contacto verificado no puede estar en más de un tenant en trial simultáneamente.
**Impacto en implementación:** Tabla `otp_codes` con expiración; unique parcial sobre teléfono verificado en tenants con estado `trial`; el sandbox queda disponible con solo email verificado (fricción mínima para probar).

---

## Clarification 006: Abandono del wizard
**Situación:** Cliente deja el wizard a medias.
**Decisión:** Autosave por paso (cada paso persiste al completarse o al perder foco). Email recordatorio único a las 24h de inactividad con deep-link al paso pendiente. El wizard es re-entrante: siempre se puede volver y editar cualquier paso.
**Impacto en implementación:** No existe "estado wizard" aparte: cada paso escribe directamente en su entidad (perfil, agente, productos…). El progreso se calcula desde el checklist de activación, no se almacena.

---

## Clarification 007: Número duplicado o ya registrado en otra WABA
**Situación:** El número solicitado ya existe en Leo o está vinculado a otra plataforma/WABA.
**Decisión:** Duplicado dentro de Leo: rechazo inmediato en la creación de la solicitud (validación por E.164 único global). Número con WABA existente fuera de Leo: la solicitud se crea igual y el admin gestiona la migración manualmente con el cliente (proceso documentado en runbook operativo, fuera de código).
**Impacto en implementación:** Constraint unique sobre `whatsapp_numbers.phone_e164` y sobre solicitudes abiertas; estado `rejected` con campo `rejection_reason` visible al cliente.

---

## Clarification 008: Concurrencia y permisos sobre configuración
**Situación:** Dueño y admin del tenant editan la misma configuración a la vez; operador intenta editar.
**Decisión:** Roles: `owner` (todo), `tenant_admin` (todo excepto eliminar cuenta y gestionar billing), `operator` (solo bandeja — en esta feature: solo lectura de su propia membresía). Escrituras de configuración con last-write-wins + `updated_at` devuelto al frontend para aviso de "alguien más guardó cambios" (sin bloqueo pesimista, innecesario para un equipo pequeño).
**Impacto en implementación:** Enum de roles en `tenant_users`; dependencia de autorización por rol en cada endpoint; el frontend compara `updated_at` antes de sobrescribir.

---

## Clarification 009: Checklist de activación — fuente de verdad
**Situación:** ¿El checklist se almacena o se calcula? ¿Qué bloquea exactamente?
**Decisión:** Se **calcula en tiempo real** desde el estado de las entidades (nunca se almacena, evita desincronización). Ítems: (1) email verificado, (2) identidad del negocio completa, (3) personalidad del agente definida, (4) ≥1 producto activo, (5) ≥1 método de pago si el objetivo incluye "vender", (6) teléfono de contacto verificado. El **sandbox** se habilita con ítems 1–5. La **activación real** exige además número en estado `connected`.
**Impacto en implementación:** Servicio `ActivationChecklistService` puro (sin side effects) reutilizado por API y por guards de activación.

---

## Clarification 010: Alcance del agent-core en esta feature
**Situación:** El sandbox necesita un agente funcional, pero el agent-core completo es la Feature de mayor tamaño del proyecto.
**Decisión:** Feature 001 incluye un **agent-core v0** mínimo y real (no mock): adaptador LLM + system prompt construido desde la configuración del tenant + dos tools (`consultar_catalogo`, `consultar_conocimiento` vía RAG) + registro de trazas. Sin envío de media, sin visión, sin memoria entre conversaciones. El mismo v0 sirve al sandbox y, al conectar el número, al canal real. Las demás tools llegan en la Feature 003/004.
**Justificación:** El patrón ganador del mercado (Darwin, Cliengo, Zaia) es que el cliente converse con su agente minutos después de registrarse; un sandbox con respuestas simuladas destruiría esa magia.
**Impacto en implementación:** El v0 se construye ya sobre las interfaces definitivas (`LLMProvider`, registro de tools) para que las features siguientes solo agreguen, no reescriban.

---

## Clarification 011: Límites de conocimiento (RAG) en trial
**Situación:** Sin límite, un tenant en trial podría indexar documentos masivos (costo de embeddings).
**Decisión:** Trial: hasta **500 K caracteres** indexados y máx. 20 archivos (PDF ≤ 10 MB c/u). Límites por plan en tabla `plans` (referencia: Emprende 1 M, Crece 4 M, Pro 8 M — benchmark Zaia).
**Impacto en implementación:** El pipeline de ingesta valida tamaño acumulado antes de indexar y devuelve error claro con el uso actual.

---

## Clarification 012: Fallo de la conexión asistida
**Situación:** La conexión en YCloud falla (OTP no llega, número inválido, rechazo de Meta).
**Decisión:** El admin marca la solicitud `rejected` con motivo visible y recomendación; el cliente puede corregir datos y reenviar (nueva solicitud enlazada a la anterior). El estado del trial NO se pausa por demoras de conexión: si la conexión tarda por causas de la plataforma, el admin puede extender el trial manualmente (+7 días, acción auditada).
**Impacto en implementación:** `connection_requests` con `previous_request_id`; acción admin `extend_trial` con registro en audit log.

---

## Cambios al Spec Original
- HU-01 actualizada: valores concretos del trial (14 días / 250 mensajes IA / sin tarjeta) — Clarification 001.
- HU-03 actualizada: sandbox ilimitado sin consumo de cuota, con rate limit anti-abuso — Clarification 002.
- HU-06 actualizada: 2 asientos en trial; límites por plan leídos de `plans` — Clarification 003.
- Reglas de Negocio: máquina de estados del tenant y retención 30 días → `archived` — Clarification 004.
- Nuevo requisito: verificación OTP del teléfono de contacto antes de solicitar conexión — Clarification 005.
- Checklist definido como cálculo en tiempo real con ítems exactos — Clarification 009.
- Alcance explícito del agent-core v0 dentro de la feature — Clarification 010.
