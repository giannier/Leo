# Spec — Onboarding de Tenant + Conexión de Número

**Feature ID:** 001
**Módulos:** 01-tenants-auth, 02-channels (parcial), 03-agent-core (v0), 05-catalog (parcial), 06-knowledge (parcial)
**Estado:** v2 — actualizado con clarifications.md

## Resumen
Un negocio debe poder pasar de "no conozco Leo" a "mi agente está respondiendo mi WhatsApp
real" con el mínimo de fricción y con garantía de calidad de datos. La investigación de
mercado muestra que los agentes fallan cuando el negocio los activa con información
desordenada; por eso el onboarding de Leo no es un formulario opcional sino un flujo
guiado con checklist duro de activación: Registro → Wizard → Modo Prueba → Conexión de
número (asistida) → Activación real.

Valor de negocio: es la puerta de entrada de todo tenant y el principal determinante de
la conversión trial→pago. Diferenciadores frente a competidores: conexión por API oficial
(sin QR no oficial), sandbox ilimitado y onboarding asistido en la conexión del número.

## Contexto de Usuario
**Usuario principal:** Dueño de negocio (rol `owner`).
**Otros usuarios:** `tenant_admin`, `operator` (invitados), `platform_admin` (Giannier).
**Flujo actual (sin Leo):** contratar una agencia que clona flujos n8n a mano, o usar
plataformas extranjeras caras o con conexión QR riesgosa.

## User Stories

### HU-01: Registro self-service con trial
**Como** dueño de negocio, **quiero** registrarme solo y probar Leo gratis, **para**
evaluar si me sirve antes de pagar.

**Criterios de aceptación:**
- WHEN completo el registro (nombre, email, contraseña, teléfono, país), THEN se crea mi
  usuario y un tenant en estado `trial` con 14 días y 250 mensajes de IA, sin pedir tarjeta.
- WHEN no he verificado mi email, THEN no puedo acceder al panel (solo a la pantalla de
  verificación con opción de reenviar).
- WHEN accedo al panel, THEN veo siempre días y mensajes restantes del trial.
- WHEN mi teléfono de contacto ya está en otro tenant en trial, THEN el sistema me lo
  indica y no permite crear un segundo trial con él.

### HU-02: Wizard de configuración del negocio
**Como** dueño, **quiero** que Leo me guíe paso a paso, **para** que mi agente responda
como si conociera mi negocio.

Pasos (autosave por paso; re-entrante):
1. **Identidad:** nombre comercial, rubro (restaurante, instituto/academia, retail,
   servicios, salud, otros), descripción libre ("cuéntale a Leo tu negocio como a un
   empleado nuevo"), web/redes (opcional), moneda, zona horaria, horarios.
2. **Personalidad del agente:** nombre (default "Leo"), tono (profesional/cercano/juvenil),
   objetivo (vender/atender/ambos), restricciones libres ("no ofrecer descuentos"),
   reglas de escalamiento a humano (defaults: cliente molesto, pide humano, fuera de catálogo).
3. **Catálogo mínimo:** ≥1 producto con nombre, precio, descripción. Carga manual o
   importación CSV/Excel con plantilla. Opcionales: stock, categoría, imagen, PDF asociado.
4. **Base de conocimiento (recomendado):** FAQs, políticas, PDFs, texto libre → RAG.
5. **Pagos que acepta el negocio:** métodos (Yape, Plin, transferencia, tarjeta, efectivo,
   link) + instrucciones + toggle "validar comprobantes por foto".
6. **Resumen + checklist de activación.**

**Criterios de aceptación:**
- WHEN completo un paso, THEN queda persistido aunque cierre el navegador.
- WHEN abandono el wizard 24h, THEN recibo un único email recordatorio con deep-link.
- WHEN importo un CSV válido, THEN veo un preview y confirmo antes de crear productos.
- WHEN el CSV tiene filas inválidas, THEN se importan las válidas y recibo el detalle de
  las rechazadas (fila y motivo).

### HU-03: Modo Prueba (sandbox)
**Como** dueño, **quiero** conversar con mi agente en un simulador, **para** verificarlo
antes de exponerlo a clientes reales.

**Criterios de aceptación:**
- WHEN mi checklist (ítems 1–5) está completo, THEN el sandbox se habilita, incluso sin
  número conectado.
- WHEN converso en el sandbox, THEN el agente usa mi configuración, catálogo y RAG reales
  (agent-core v0) y NO consume mi cuota de trial; es ilimitado (rate limit 30 msg/min).
- WHEN presiono "Reiniciar conversación", THEN empiezo un hilo nuevo limpio.
- WHEN el agente responde en sandbox, THEN puedo ver la traza (tools llamadas y fuentes
  usadas) en un panel expandible.

### HU-04: Conexión asistida del número (fase YCloud)
**Como** dueño, **quiero** conectar mi número de forma segura y oficial, **para** operar
sin riesgo de bloqueo.

**Criterios de aceptación:**
- WHEN mi teléfono de contacto está verificado por OTP, THEN puedo crear la solicitud de
  conexión con: número deseado, display name, foto de perfil, descripción.
- WHEN el número ya existe en Leo, THEN la solicitud se rechaza al instante con motivo.
- WHEN creo la solicitud, THEN aparece en el panel admin de plataforma y veo su estado:
  `requested → in_progress → connected | rejected` (con motivo y opción de corregir/reenviar).
- WHEN el admin marca `connected`, THEN los IDs/credenciales del número quedan cifrados y
  asociados a mi tenant, y recibo notificación.
- La abstracción `WhatsAppProvider` se implementa de modo que el reemplazo futuro por
  Embedded Signup (Meta Tech Provider) no toque el resto del sistema.

### HU-05: Activación real y estado del agente
**Como** dueño, **quiero** encender/apagar mi agente cuando quiera.

**Criterios de aceptación:**
- WHEN checklist completo (ítems 1–6) y número `connected`, THEN puedo activar el agente.
- WHEN el agente está OFF, THEN los mensajes entrantes se almacenan en la bandeja sin
  respuesta automática y se me notifica.
- WHEN activo por primera vez, THEN veo una guía de test real ("escríbele a tu número").

### HU-06: Invitar operadores
**Como** dueño, **quiero** invitar empleados, **para** que atiendan la bandeja.

**Criterios de aceptación:**
- WHEN invito por email con rol (`operator` | `tenant_admin`), THEN se envía invitación
  con token que expira en 7 días (reenviable).
- WHEN el invitado acepta, THEN crea su usuario (o vincula el existente) y entra al tenant
  con su rol.
- WHEN alcanzo el límite de asientos de mi plan (trial: 2), THEN no puedo invitar más y
  el sistema me lo explica.
- WHEN desactivo a un miembro, THEN sus sesiones se revocan al instante.

## Requerimientos Funcionales
1. [RF-001] El sistema debe crear tenant + usuario `owner` en una transacción al registrar.
2. [RF-002] El sistema debe bloquear el panel hasta verificar email (token de un solo uso, expira 24h).
3. [RF-003] El sistema debe calcular el checklist de activación en tiempo real (nunca almacenado).
4. [RF-004] El sistema debe contar mensajes de IA solo del canal `whatsapp` contra la cuota del trial.
5. [RF-005] Cuando el trial expira (fecha o cuota), el sistema debe transicionar el tenant a `paused`, cerrar con gracia la conversación en curso y notificar (emails día −3, −1, 0 y banner).
6. [RF-006] El sistema debe retener datos de tenants `paused` 30 días y luego transicionar a `archived` (recuperable por admin; eliminación definitiva solo a solicitud — Ley 29733).
7. [RF-007] El sistema debe exigir OTP del teléfono de contacto antes de crear solicitudes de conexión.
8. [RF-008] El sistema debe garantizar unicidad global E.164 de números conectados y de solicitudes abiertas.
9. [RF-009] El sistema debe cifrar en reposo los IDs/credenciales del número (campo cifrado, clave fuera de BD).
10. [RF-010] El sistema debe registrar trazas del agente (tools llamadas, fuentes RAG, tokens) por mensaje.
11. [RF-011] El sistema debe validar en la ingesta de conocimiento los límites del plan (trial: 500K caracteres, 20 archivos, PDFs ≤ 10MB).
12. [RF-012] Toda acción del panel admin de plataforma (cambiar estado de solicitud, extender trial) debe quedar en audit log.
13. [RF-013] El sistema debe aplicar autorización por rol (`owner`/`tenant_admin`/`operator`) en cada endpoint del tenant.

## Requerimientos No Funcionales
- **Performance:** respuestas de API < 300ms p95 (excluye llamadas a LLM); primera respuesta del sandbox < 6s p95.
- **Seguridad:** JWT + refresh; rate limiting por IP en auth (5 intentos/min) y por tenant en sandbox (30 msg/min); aislamiento multi-tenant verificado por tests.
- **Disponibilidad:** panel web responsive (uso desde celular); sin app nativa.
- **i18n:** interfaz solo en español (v1); arquitectura de textos preparada para i18n futura.

## Fuera de Scope (Esta Versión)
- Cobro de suscripción y conversión trial→pago (Feature 009 — billing).
- Embedded Signup directo con Meta (fase Tech Provider; la interfaz ya lo prevé).
- Webhooks entrantes de mensajes reales y bandeja de inbox (Features 002/007/008); esta
  feature deja el número conectado y el agente activable, y define el contrato del webhook.
- Envío de media, visión para comprobantes y demás tools (Features 003/004).
- Campañas, plantillas, CRM, analytics (post-MVP).

## Open Questions
- [ ] Proveedor de email transaccional (propuesto en plan.md: Resend; requiere aprobación por regla de dependencias).
- [ ] Textos legales (términos y condiciones, política de privacidad) — redactar antes del lanzamiento público.

## Criterios de Aceptación de la Feature
- [ ] Un usuario nuevo puede registrarse, verificar email, completar el wizard e interactuar con su agente en sandbox sin intervención del admin.
- [ ] El admin puede procesar una solicitud de conexión de punta a punta y el tenant queda con número `connected` y agente activable.
- [ ] La suite de aislamiento multi-tenant pasa (ningún acceso cruzado posible).
- [ ] Expiración del trial probada en integración: pausa, avisos y retención según RF-005/006.
- [ ] % de cobertura según Constitution (80% en agent-core/channels; 70% resto).
- [ ] Métricas instrumentadas: registro→checklist completo, registro→agente activo, uso de sandbox pre-activación.

## Dependencias
- **Features previas:** ninguna (es la primera).
- **Sistemas externos:** YCloud (conexión de números — operado manualmente por el admin en v1), proveedor LLM (vía `LLMProvider`), proveedor de email transaccional, OTP por WhatsApp/SMS (v1: envío manual documentado o vía YCloud si el plan lo permite — decidir en implementación de TASK-014).
