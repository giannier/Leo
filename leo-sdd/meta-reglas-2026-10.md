# Reglas de Meta (WhatsApp Business Platform) vigentes a octubre 2026

Investigado el 2026-10-19 a pedido de Giannier ("las políticas cambiaron a partir del 1 de octubre").
Las fuentes son sobre todo de partners de Meta (YCloud, Wati, respond.io, Gupshup). La documentación de Meta
estaba desactualizada al 13 de octubre, así que **todo lo marcado "verificar" se confirma en el WhatsApp
Manager / factura de YCloud con el número real (TASK-459)**.

## 1. Cobro de mensajes de servicio (desde el 1 de octubre de 2026)

- Las respuestas libres dentro de la ventana de 24 h (los mensajes del asistente, de un operador, la
  pregunta de promociones, la encuesta) **ya no son gratis**: se cobran por mensaje entregado.
- Hay **1,000 mensajes de servicio gratis al mes por número** (reportado por partners; Meta no lo documentaba
  aún; verificar).
- Las **plantillas de Utilidad enviadas dentro de la ventana** también pasan a cobrarse.
- Perú (aprox., fuentes en conflicto): servicio US$ 0.030, utilidad US$ 0.030–0.0345, marketing
  US$ 0.0703–0.08085. Leo usa la cifra **más alta** para no subestimar el costo (C-111 de la 011).
- Lo cobra Meta (vía YCloud) al negocio, no Leo. El canal sigue siendo gratis para el cliente final.

**Impacto en Leo:** cada burbuja que envía el asistente cuenta como un mensaje. Un asistente que responde
en tres burbujas cuesta tres veces más que uno que responde en una. → Decisión para Giannier (no
implementada: toca el prompt y el costo): agrupar respuestas en menos burbujas.

## 2. Marketing

- Tope por persona: ~2 plantillas de marketing por usuario cada 24 h **entre todas las empresas**; si se
  pasa, Meta rechaza con el error **131049**. No aplica a utilidad ni autenticación. Leo marca a ese
  destinatario como "WhatsApp limitó los mensajes de marketing a esta persona hoy" (no es una falla del negocio).
- Siguen vigentes: plantilla aprobada fuera de la ventana, opt-in verificable, baja respetada, calidad del número.

## 3. Límites de envío

- Desde octubre de 2025 el límite diario de conversaciones iniciadas por la empresa se aplica **por portafolio
  comercial (Business Portfolio)**, no por número. Un portafolio nuevo sin verificar empieza en **250**;
  verificado sube (1K/2K según fuente) y escala a 10K, 100K e ilimitado con buena calidad.
- **Impacto:** si las empresas del holding comparten portafolio, comparten el tope. Leo cuenta el tope por
  número (`whatsapp_numbers.limite_diario`, 250 por defecto); el operador debe repartirlo entre los números
  del mismo portafolio.

## 4. IA en WhatsApp (desde el 15 de enero de 2026 para cuentas existentes)

- Meta prohíbe los asistentes de IA **de propósito general** como producto principal ("AI Providers").
- **Permitido:** asistentes de una empresa para atender a sus propios clientes (productos, pedidos, citas,
  preguntas frecuentes), aunque un tercero (Leo) los provea. Leo encaja aquí: cada asistente vende solo lo
  de su negocio. Mantenerlo así (no abrir el asistente a temas generales).

## 5. Anuncios Click-to-WhatsApp

- Las conversaciones que llegan por un anuncio tienen una ventana gratis de 72 h (algunas fuentes hablan de
  hasta 7 días "próximamente"). La ventana para mensajes libres sigue siendo de 24 h desde el último mensaje
  del cliente. Leo no cambia su regla de 24 h.

## Qué cambió en Leo por esto (2026-10-19)

- Tarifas de Meta: nueva categoría **servicio**; Perú actualizado desde el 2026-10-01 (marketing 0.08085,
  utilidad 0.0345, servicio 0.030), editable en Plataforma → Tarifas Meta.
- Resultados muestra el **gasto estimado en WhatsApp del mes** (mensajes de servicio sobre los 1,000 gratis
  y plantillas por categoría).
- Campañas: el error 131049 se registra como tope de Meta por persona.
- Encuesta de satisfacción (013): solo dentro de las 24 h, como texto fijo (decisión de Giannier); ahora cuesta
  como un mensaje de servicio más, y la pantalla lo dice.

## Fuentes

- https://www.ycloud.com/blog/whatsapp-api-message-pricing-update-effective-october-1-2026
- https://www.wati.io/en/blog/whatsapp-service-message-pricing/
- https://respond.io/blog/whatsapp-pricing-change-2026
- https://sendpulse.com/blog/whatsapp-service-message-pricing
- https://chat2desk.com/en/blog/articles/whatsapp-business-api-billing-to-change
- https://www.flowcall.co/blog/whatsapp-business-api-pricing
- https://www.gupshup.ai/resources/blog/all-you-need-to-know-about-whatsapp-business-api-frequency-capping/
- https://clickatell.com/help-center/whatsapp/sending-receiving-messages/messaging-limits-whatsapp-business/
- https://respond.io/blog/whatsapp-general-purpose-chatbots-ban
- https://techcrunch.com/2025/10/18/whatssapp-changes-its-terms-to-bar-general-purpose-chatbots-from-its-platform/
