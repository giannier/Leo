# Milestone 011: Campañas, plantillas y seguimientos (TASK-1101..1108)

Fecha: 2026-10-19. Claude lo verificó con pruebas automáticas, sin llamadas al modelo real: nada de la 011 usa el LLM ni cambia el prompt (C-102, C-104, C-110). Giannier lo prueba con el número real (TASK-459). Queda pendiente TASK-1109, la primera campaña real supervisada.

## Qué hay

### Menú Campañas, con cuatro pestañas

- **Campañas**
  - Nueva campaña: plantilla aprobada, a quién (etiquetas, etapa del CRM, compró o no, producto, días sin escribir), cuándo (ahora o programada) y horario de envío (entre 08:00 y 21:00 del negocio).
  - Muestra en vivo a cuántas personas llega y el costo aproximado en US$, que cobra Meta vía YCloud.
  - Avisos de tope del plan.
  - Confirmación antes de enviar. Se puede pausar, reanudar y cancelar.
  - Resultado de cada campaña: destinatarios, enviados, leídos, respondieron, ventas en 72 h (con monto), bajas y motivos de omisión.
- **Plantillas**
  - Biblioteca por rubro.
  - Editor con `{{nombre}}`, `{{negocio}}` y `{{producto}}` y vista previa como en WhatsApp.
  - Botón "Enviar a Meta"; el estado se actualiza solo con el webhook.
  - Si Meta rechaza una plantilla, se muestra el motivo en palabras simples. Para cambiar una aprobada, se duplica.
- **Contactos**
  - Quién acepta promociones y cómo (por WhatsApp o importado), con etiquetas editables y filtros.
  - Importar CSV con declaración de consentimiento: tope de 200 por semana, y nunca vuelve "sí" a quien dijo no o escribió BAJA.
- **Ajustes**
  - Preguntar por promociones después de una venta confirmada (encendido por defecto).
  - Plantilla de Utilidad para el seguimiento después de 24 horas.

### Lo que pasa por WhatsApp

- **Pregunta de permiso.** Al confirmar un pago, Leo pregunta una vez con dos botones. La respuesta se guarda como evidencia y se contesta con un texto fijo, sin el asistente.
- **BAJA.** Revoca el permiso de promociones.
- **Campañas.** Salen en lotes de 30 por minuto, con un tope de 250 plantillas por número en 24 h (el operador puede subirlo).
  - Al momento de enviar se revisan de nuevo el permiso y la baja de cada destinatario.
  - Ningún contacto recibe dos veces la misma campaña.
  - Si la calidad del número baja a YELLOW o RED, las campañas se pausan solas y no se pueden reanudar hasta que se recupere.
- **Respuestas.** La respuesta del cliente sigue en la misma conversación de la plantilla, así que el asistente ve qué se le envió.
- **Seguimiento después de 24 h.** Va como la plantilla de Utilidad elegida, sin el modelo. Máximo 1 por conversación y 2 por cliente al mes.

### Panel de plataforma

- "Tarifas Meta": tarifas por país y categoría.
- Tope diario por número, configurable por API.

## Evidencia

- **Backend:** suite completa en verde (3,666 + los nuevos). Las pruebas de la 011 son:
  - `test_campaign_templates.py`
  - `test_campaign_optins.py`
  - `test_campaigns.py`
  - `test_followup_templates.py`
  - `test_campaigns_api.py`
  - `isolation/test_campaigns_isolation.py`
  - `unit/test_ycloud_templates.py`
- **e2e:** `frontend/e2e/campanas.spec.ts` pasa en escritorio y en Android. Recorre la biblioteca, la plantilla a mano con su error, la importación con declaración, las etiquetas y el filtro, la campaña a una etiqueta con estimado, programar, pausar, reanudar, cancelar y los ajustes.
- **Capturas:** `campanas-plantilla-*`, `campanas-contactos-*`, `campanas-nueva-*` y `campanas-lista-*`.

## Decisiones

**Bloqueantes:** ninguna.

**Para ratificar:**
- C-103: BAJA revoca el permiso, y volver a escribir no lo devuelve.
- C-106: tope de 250 plantillas por número en 24 h; lotes de 30 por minuto.
- C-107: tarifas de Meta para Perú (marketing 0.0703, utility 0.0200). **Hay que verificarlas antes del piloto.**
- C-108: topes por plan. Con el cobro apagado solo avisan.

**Informativas:**
- C-102: opt-in por botón tras una venta confirmada.
- C-104: el interés de compra sale del proceso de venta.
- C-105: variables con nombre.
- C-109: la respuesta sigue en la misma conversación y las ventas se atribuyen en 72 h.
- C-110: seguimiento con plantilla después de 24 h.
- El evento de YCloud para la revisión de plantillas se acepta como `whatsapp.template.reviewed` o como `whatsapp.template.updated`.

**A verificar con el número real (TASK-459):**
- Formato exacto de alta de plantilla con imagen (`example.header_url`).
- Nombre del evento de revisión en YCloud.

## Milestone humano pendiente (TASK-1109)

Primera campaña real con un negocio piloto de 100 contactos o menos, supervisada por Giannier de punta a punta. Antes:
1. Verificar las tarifas.
2. Aprobar una plantilla real en Meta.
3. Revisar que los clientes estén aceptando el botón de promociones.
