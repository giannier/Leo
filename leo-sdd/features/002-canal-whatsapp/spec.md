# Spec — Canal WhatsApp Real (YCloud)

**Feature ID:** 002 · **Módulo:** 02-channels · **Estado:** Draft para ratificación
**Depende de:** 001 (número conectado, agent-core v0, conversations/messages)

## Resumen
Con el número conectado (Feature 001), esta feature hace que Leo reciba y responda
mensajes reales de WhatsApp: webhooks entrantes de YCloud, envío de respuestas, respeto
de la ventana de 24 horas, medición de cuota en vivo y monitoreo de la salud del número.
Es el puente entre el agente y el mundo real; su requisito central es **no perder jamás
un mensaje** y **no violar jamás las políticas de Meta**.

## User Stories

### HU-01: Recepción confiable de mensajes
**Como** dueño, **quiero** que todo mensaje que llegue a mi número quede registrado y
respondido, **para** no perder ventas.
- WHEN llega un webhook de YCloud, THEN se responde 200 en <2s y el procesamiento pasa a cola.
- WHEN el webhook trae firma inválida, THEN se rechaza (401) y se registra alerta.
- WHEN llega un mensaje duplicado (retry del proveedor), THEN se procesa una sola vez (idempotencia por `provider_message_id`).
- WHEN el agente está OFF o el tenant `paused`, THEN el mensaje se almacena en la bandeja igual y se notifica al dueño (nunca se descarta).

### HU-02: Respuesta del agente en el canal real
**Como** cliente final del negocio, **quiero** respuestas rápidas y correctas por WhatsApp.
- WHEN el mensaje entra y el agente está ON, THEN el agent-core genera respuesta con las tools del tenant y se envía vía `WhatsAppProvider`.
- WHEN la respuesta se envía, THEN se registra el estado de entrega (sent → delivered → read → failed) desde los webhooks de estado.
- WHEN el envío falla, THEN se reintenta (backoff, máx 3) y si persiste se marca `failed` con alerta al tenant.
- WHEN varios mensajes del mismo cliente llegan en ráfaga (<8s), THEN se agrupan y el agente responde una sola vez al conjunto (debounce configurable).

### HU-03: Ventana de 24 horas y compliance
**Como** plataforma, **quiero** cumplir las reglas de Meta, **para** proteger los números de mis clientes.
- WHEN la última interacción del cliente final supera 24h, THEN el sistema bloquea mensajes de sesión salientes (el agente no puede iniciar; plantillas llegan en Feature 011).
- WHEN un cliente final escribe "STOP"/"BAJA" o solicita no ser contactado, THEN se marca opt-out y el agente no vuelve a responder salvo nuevo mensaje entrante de esa persona (registro auditable).
- WHEN Meta/YCloud reporta cambio de quality rating del número, THEN se actualiza `whatsapp_numbers.quality_rating` y si baja a `RED/FLAGGED` se alerta al tenant y al admin.

### HU-04: Cuota en vivo
**Como** dueño en trial, **quiero** ver mi consumo en tiempo real.
- WHEN el agente responde en canal `whatsapp`, THEN `ai_messages_used` se incrementa atómicamente.
- WHEN el consumo llega a 80% y 100%, THEN se notifica (banner + email); al 100% aplica el cierre con gracia (Clarification 004 de F001).

## Requerimientos Funcionales
1. [RF-001] Webhook `POST /webhooks/ycloud` con verificación HMAC, respuesta <2s, encolado en Redis (ARQ) con dead-letter tras 5 reintentos.
2. [RF-002] Idempotencia por `provider_message_id` (unique) — mensajes y estados.
3. [RF-003] `WhatsAppProvider.send_text()` implementado para YCloud (media llega en F004); mapeo de errores del proveedor a estados internos.
4. [RF-004] Registro de contactos (`contacts`): teléfono, nombre de perfil, opt-out, última interacción (base para ventana 24h).
5. [RF-005] Debounce de ráfagas: agrupar mensajes del mismo contacto en ventana de 8s antes de invocar al agente.
6. [RF-006] Cola por conversación (orden FIFO por contacto; conversaciones distintas en paralelo).
7. [RF-007] Dead-letter visible en panel admin con reproceso manual.
8. [RF-008] Métricas por tenant: mensajes in/out, latencia de respuesta p50/p95, fallos de envío.

## Decisiones y Edge Cases (Clarify integrado)
- **C-001 Mensajes no soportados en v1** (audio, ubicación, contactos, stickers entrantes): se almacenan con tipo, el agente responde un fallback configurable ("¿puedes escribirlo en texto?") y cuenta como interacción para la ventana 24h. Audio se transcribe recién en F004.
- **C-002 Conversación**: se reutiliza la abierta del contacto; se considera nueva tras 24h sin mensajes (alineado a la ventana de Meta).
- **C-003 Orden de llegada fuera de secuencia**: se ordena por timestamp del proveedor, no de recepción.
- **C-004 Caída del worker**: los jobs son re-entrantes e idempotentes; ningún side effect sin marca de completado.
- **C-005 Dos números por tenant**: fuera de scope v1 (1 número por tenant; el modelo de datos ya lo soporta a futuro).

## Fuera de Scope
- Envío de media (F004), plantillas y mensajes proactivos (F011), inbox UI en vivo (F007), transcripción de audio (F004).

## Criterios de Aceptación de la Feature
- [ ] Un mensaje real de WhatsApp recibe respuesta del agente end-to-end en <10s p95.
- [ ] Test de caos: caída del worker a mitad de proceso no pierde ni duplica mensajes.
- [ ] Ventana 24h y opt-out verificados por tests de integración.
- [ ] Contador de cuota exacto bajo concurrencia (test con 50 mensajes simultáneos).
- [ ] Cobertura ≥80% (módulo crítico).
