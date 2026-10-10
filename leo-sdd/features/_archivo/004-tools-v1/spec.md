# Spec — Tools Avanzadas del Agente

**Feature ID:** 004 · **Módulos:** 04-tools, 05-catalog (cierre) · **Estado:** Draft para ratificación
**Depende de:** 002 (canal), 003 (agent-core completo)

## Resumen
Convierte al agente en vendedor completo: envía media (imágenes, PDFs, stickers), lee
imágenes (valida comprobantes de pago, digitaliza listas de precios), consulta y descuenta
stock, y entiende audios. Las tools se activan por tenant según su configuración y plan
(principio: catálogo de tools modular).

## User Stories

### HU-01: Envío de media
**Como** cliente final, **quiero** recibir el menú/malla/foto del producto por WhatsApp.
- Tools `enviar_imagen`, `enviar_documento` (PDF), `enviar_sticker`: el agente elige el archivo desde los assets del tenant (imagen/PDF de producto, documentos del negocio) — nunca URLs externas arbitrarias.
- WHEN el agente menciona un producto con imagen/PDF asociado y es pertinente, THEN puede adjuntarlo (máx 2 media por respuesta; anti-spam).
- WHEN el archivo excede límites de WhatsApp (imagen 5MB, doc 100MB), THEN se comprime (imagen) o se rechaza en la carga con mensaje claro (documento).

### HU-02: Validación de comprobantes por visión
**Como** dueño, **quiero** que el agente pre-valide los pagos por Yape/Plin/transferencia.
- WHEN el cliente envía una imagen y la conversación está en contexto de pago, THEN la tool `validar_comprobante` extrae: monto, fecha/hora, destinatario, número de operación, y compara contra lo esperado (monto del pedido, titular configurado).
- WHEN todo coincide, THEN el agente confirma "pago recibido, sujeto a verificación" y marca el comprobante `pre_approved`; el dueño confirma o rechaza desde la bandeja (decisión final SIEMPRE humana — Constitution: nunca confirma un pago sin validación).
- WHEN hay discrepancia (monto distinto, captura recortada, fecha vieja, posible edición), THEN estado `flagged` con motivos y escalamiento a humano.
- Todos los comprobantes quedan en storage privado del tenant con su análisis.

### HU-03: Stock
**Como** dueño, **quiero** que el agente no venda lo que no tengo.
- WHEN un producto controla stock (stock NOT NULL), THEN `consultar_catalogo` incluye disponibilidad y el agente no ofrece agotados (configurable: ofrecer alternativa).
- Tool `reservar_stock` (opcional por tenant): descuenta al confirmar pedido, con reserva TTL 30 min si el pago está pendiente; liberación automática al expirar.
- WHEN el stock llega a umbral configurado, THEN alerta al dueño.

### HU-04: Catálogo por foto (cierre del caso borde de F001)
**Como** dueño sin catálogo digital, **quiero** subir la foto de mi lista de precios.
- WHEN subo imagen/PDF de lista de precios en el wizard o el panel, THEN visión extrae borrador de productos (nombre, precio, categoría) para revisión y confirmación (nunca se publica sin confirmar).

### HU-05: Audios entrantes
**Como** cliente final, **quiero** mandar audios como hago siempre.
- WHEN llega un audio ≤2 min, THEN se transcribe y el agente responde al contenido (en texto). Audios más largos: fallback cortés.

## Requerimientos Funcionales
1. [RF-001] `WhatsAppProvider` extendido: `send_image/send_document/send_sticker` (+ subida/caché de media al proveedor); descarga de media entrante a MinIO privado.
2. [RF-002] Registro de tools con flag por tenant (`tenant_tools`): activables desde panel según plan; `validar_comprobante` requiere `validate_receipts` activo en el método de pago.
3. [RF-003] Análisis de comprobantes vía `LLMProvider` (task `vision`, modelo de model_policies) con salida estructurada validada (JSON schema); confianza < umbral → `flagged`.
4. [RF-004] Tabla `receipts` con estados `pre_approved | flagged | approved | rejected` (aprobación/rechazo solo por humano, auditado).
5. [RF-005] Reservas de stock atómicas (sin sobreventa bajo concurrencia) con TTL y liberación por job.
6. [RF-006] Digitalización de listas: pipeline visión → borrador → confirmación (reusa preview de import de F001).
7. [RF-007] Transcripción de audio vía `LLMProvider.transcribe` (o proveedor STT dentro del adaptador; decidir en TASK-405 y documentar).
8. [RF-008] Toda media entrante pasa por límites (tamaño/tipo) y antivirus básico (clamav opcional — decidir por costo/beneficio en implementación).

## Decisiones y Edge Cases (Clarify integrado)
- **C-001 El agente nunca "aprueba" pagos**: vocabulario fijo "recibido y en verificación"; solo el humano aprueba. Evita fraude por capturas editadas.
- **C-002 Detección de imagen editada**: v1 solo heurísticas del modelo de visión (inconsistencias visuales) + reglas (fecha/monto); análisis forense queda fuera de scope — el diseño lo deja como validador adicional futuro.
- **C-003 Sticker saliente**: pack propio del tenant (sube sus stickers) o pack default de Leo; el agente los usa solo con tono `cercano/juvenil` y máx 1 por conversación.
- **C-004 Stock multi-conversación**: reserva con `SELECT ... FOR UPDATE`; dos clientes por la última unidad → uno reserva, al otro se le ofrece alternativa.
- **C-005 Pedidos**: v1 registra "pedido informal" (JSON en conversación: items+total) para dar contexto al pago; módulo formal de pedidos/delivery es post-MVP (candidato a F016).

## Fuera de Scope
- Pasarela de pago del cliente final (links de pago) — post-MVP; catálogo nativo de WhatsApp (Meta Commerce) — post-MVP; OCR de PDFs escaneados del conocimiento (F006).

## Criterios de Aceptación
- [ ] Flujo restaurante demo end-to-end: pide menú → recibe PDF → ordena → envía captura Yape → pre-aprobado → dueño aprueba en bandeja.
- [ ] Test de sobreventa: 10 compradores concurrentes, stock 3, cero sobreventa.
- [ ] Evals nuevos: uso pertinente de media (no spamea), no aprueba pagos, ofrece alternativas ante agotados.
- [ ] Comprobantes accesibles solo por su tenant (test de aislamiento de storage).
- [ ] Cobertura ≥80% (módulo crítico).
