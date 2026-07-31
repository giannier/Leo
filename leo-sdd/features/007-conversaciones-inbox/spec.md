# Spec — Conversaciones e Inbox con Takeover Humano

**Feature ID:** 007 · **Módulos:** 07-conversations + 08-inbox · **Estado:** Draft para ratificación
**Depende de:** 002 (canal), 003 (needs_human, takeover flag)

## Resumen
La bandeja en vivo donde el dueño y sus operadores ven todas las conversaciones, toman el
control cuando hace falta y devuelven al agente. La investigación de mercado fue
contundente: el takeover instantáneo con la IA deteniéndose al momento es la funcionalidad
que decide la confianza del negocio en la plataforma. Sin bandeja no hay ventas asistidas,
no hay aprobación de pagos y no hay adopción.

## User Stories

### HU-01: Bandeja en vivo
**Como** operador, **quiero** ver todas las conversaciones en tiempo real.
- Lista con filtros: `todas | atendidas por agente | necesitan humano | en takeover | cerradas`; orden por última actividad; indicador de no leídos.
- WHEN llega mensaje nuevo o cambia un estado, THEN la bandeja se actualiza sin recargar (WebSocket) en <2s.
- Vista de conversación: historial completo (incluye media con preview, transcripciones de audio, trazas del agente expandibles), perfil del contacto (nombre, teléfono, resumen del perfil F003, historial de compras informal).

### HU-02: Takeover instantáneo
**Como** operador, **quiero** intervenir y que el agente se calle al instante.
- WHEN presiono "Tomar conversación", THEN el agente queda pausado en esa conversación de inmediato (incluso si estaba generando: la respuesta en curso se descarta antes de enviarse) y los demás operadores ven quién la tiene.
- WHEN tengo el control, THEN escribo mensajes como el negocio (etiquetados `human_operator` con mi nombre interno; el cliente final ve la conversación normal).
- WHEN presiono "Devolver al agente", THEN el agente se reactiva y su contexto incluye lo que el humano conversó (F003 HU-01).
- WHEN no devuelvo el control, THEN auto-devolución tras inactividad configurable (default 30 min, avisando al operador 5 min antes) — configurable por tenant, incluible "nunca".

### HU-03: Cola de "necesitan humano"
**Como** dueño, **quiero** enterarme al instante cuando el agente escala.
- WHEN el agente ejecuta `escalar_a_humano` o un comprobante queda `flagged`, THEN la conversación entra al filtro "necesitan humano" con motivo visible + notificación (push del navegador + email si nadie la toma en 10 min, configurable).
- WHEN un operador la toma, THEN sale de la cola y queda asignada.

### HU-04: Acciones sobre la conversación
- Notas internas (invisibles al cliente final), cerrar/reabrir conversación, marcar contacto opt-out manualmente, aprobar/rechazar comprobantes (UI de F004), eliminar datos del contacto a solicitud (Ley 29733 — acción de owner, auditada).

### HU-05: Ventana de 24h visible
- WHEN la ventana está por cerrar (>20h desde el último mensaje del cliente), THEN la conversación lo indica; al cerrarse, el compositor humano se bloquea con explicación ("podrás responder cuando el cliente escriba; las plantillas llegan pronto") — evita violaciones de política por parte de operadores.

## Requerimientos Funcionales
1. [RF-001] WebSocket autenticado por tenant (JWT) con canales por rol; reconexión con re-sync de estado.
2. [RF-002] Takeover con lock atómico (`takeover_user_id`) — dos operadores presionando a la vez: uno gana, el otro recibe aviso; el guard de F003 C-004 impide cualquier generación durante takeover, incluida la descartada en curso.
3. [RF-003] Asignación y presencia: quién está viendo/atendiendo cada conversación.
4. [RF-004] Notificaciones: browser push + email de cola desatendida (configurables por tenant).
5. [RF-005] Notas internas y eventos del sistema (takeover, devolución, cierre, aprobación de pago) como items del hilo con tipo propio.
6. [RF-006] Búsqueda: por contacto (nombre/teléfono) y texto en mensajes del tenant (índice GIN, español).
7. [RF-007] Auditoría de acciones sensibles (opt-out manual, eliminación de datos, aprobación de pagos) en audit_logs.
8. [RF-008] Todo respeta roles: operator solo bandeja; tenant_admin además configura reglas de auto-devolución y notificaciones.

## Decisiones y Edge Cases (Clarify integrado)
- **C-001 Mensaje del cliente durante takeover:** va a la bandeja normal (el humano responde); el agente no interviene ni cuenta cuota.
- **C-002 Operador escribe fuera de ventana 24h:** bloqueado por el sistema (RF de HU-05) — el compositor lo impide, no solo lo advierte.
- **C-003 Conversación tomada por operador que se desconecta:** la auto-devolución aplica igual; el dueño puede "robar" el takeover (acción de owner/tenant_admin, notificada).
- **C-004 Cuota agotada / tenant paused durante takeover:** el humano SÍ puede seguir respondiendo (los mensajes humanos no consumen cuota de IA); solo el agente queda pausado.
- **C-005 Volumen:** paginación por cursor en hilos largos; virtualización en UI; adjuntos con presigned URLs con expiración.

## Fuera de Scope
- Apps móviles nativas (web responsive + push del navegador), asignación automática round-robin (post-MVP), SLAs y reportes de atención (F010/analytics), respuestas rápidas/snippets (post-MVP corto — anotado para F010+).

## Criterios de Aceptación
- [ ] Takeover corta al agente en <1s incluso con generación en curso (test de carrera).
- [ ] Dos operadores simultáneos: lock correcto, presencia visible, cero mensajes duplicados.
- [ ] Flujo completo de escalamiento: agente escala → notificación → takeover → resolución → devolución con contexto.
- [ ] Bloqueo de ventana 24h verificado.
- [ ] WebSocket sobrevive reconexiones sin perder eventos (re-sync).
- [ ] Cobertura ≥70% (módulo no crítico según Constitution, pero takeover y guards al 80%).
