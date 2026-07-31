# Post-MVP — Resumen de Features 011–016 + Plan Tech Provider

Estado: **documentación SDD completa disponible** en `features/011-*` … `features/016-*`
(spec con clarify integrado, plan técnico con data model y API, y tasks). Este archivo
queda como resumen ejecutivo de una página + el plan de migración a Meta Tech Provider
(que es trámite y adaptador, no una feature con spec propio). Ver `post-mvp/README.md`
para el orden sugerido de la fase.

## 011 — Campañas, Plantillas y Follow-ups
Gestión de plantillas de WhatsApp (creación, envío a aprobación de Meta vía provider,
estados), broadcasts segmentados SOLO a contactos con opt-in registrado, límites por
quality rating y tier del número, y follow-ups automáticos del agente a conversaciones
frías (lead caliente que dejó de responder → 1 recordatorio con plantilla, configurable,
con tope). Compliance es el corazón: opt-in verificable, opt-out inmediato, ventanas de
envío. Depende de: 002, 007. Riesgo mayor: dañar quality ratings — lanzar con límites
conservadores por defecto.

## 012 — Mini-CRM de Leads
Pipeline configurable por tenant (default: nuevo → interesado → por pagar → ganado →
perdido), calificación automática por el agente (tool `actualizar_lead`), vista kanban en
el panel, motivos de pérdida, y conexión con follow-ups (011). Métrica estrella:
conversión por etapa en el dashboard. Depende de: 003, 007, 010.

## 013 — Analytics Avanzado + CSAT
Encuesta CSAT post-conversación (plantilla, 011), NPS del tenant hacia Leo, embudos
(consulta→pedido→pago), atribución de ventas por producto y horario, comparativas entre
períodos. Depende de: 010, 011.

## 014 — Agendamiento de Citas
Tool `agendar_cita` con calendario propio simple + sincronización Google Calendar
(adaptador `CalendarProvider`), recordatorios por plantilla (011), reprogramación y
no-show tracking. Abre los rubros de servicios (clínicas, barberías, institutos con
charlas). Depende de: 003, 011.

## 015 — Integraciones y API Pública
Webhooks salientes por eventos (pedido, pago aprobado, lead ganado), API pública con
API keys por tenant (scopes de lectura primero), y conectores iniciales según demanda
real de clientes (Google Sheets export continuo suele ser el primero en LATAM). Depende
de: estabilidad general del MVP.

## 016 — Pedidos y Delivery (candidato, validar con clientes)
Formalizar el "pedido informal" de F004: estados (recibido → preparando → en camino →
entregado), datos de entrega, integración con validación de pagos, notificaciones de
estado por plantilla. Priorizar si los primeros tenants son mayormente restaurantes/retail.

---

## Migración a Meta Tech Provider (trámite paralelo, no una feature de código al inicio)
1. **Ya** (durante el MVP): verificación del negocio Zentek360 en Meta Business Manager
   (web activa, teléfono, dirección verificables).
2. Crear la app en Meta for Developers, configurar WhatsApp product, solicitar acceso
   avanzado a `whatsapp_business_messaging` y `whatsapp_business_management` (App Review
   con screencast del flujo de Leo — el sandbox y el panel ya existentes sirven de demo).
3. Al aprobarse: implementar `MetaCloudProvider` (Embedded Signup **v4** — obligatorio,
   v2/v3 se deprecan el 15-oct-2026) detrás de la interfaz `WhatsAppProvider`; el
   onboarding asistido de F001 se reemplaza por el popup self-service sin tocar el resto.
4. Migrar tenants de YCloud a conexión directa de forma opt-in y gradual (runbook:
   misma WABA, desactivar 2FA, ventana de migración coordinada con cada cliente).
5. Beneficio final: cero dependencia de BSP, onboarding 100% self-service, argumento
   comercial "conexión oficial directa con Meta".
