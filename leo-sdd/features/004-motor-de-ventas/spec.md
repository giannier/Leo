# Spec — Motor de ventas

**Feature ID:** 004 (v2 — reemplaza la spec 004 "Tools" y completa la 003 "Agent-core")
**Módulo:** agente + panel del cliente
**Estado:** Draft para ratificación
**Fecha:** 2026-10-10

## Resumen

Hoy Leo responde preguntas, pero no vende. Su agente es perfil + catálogo + conocimiento + tono:
no sabe en qué momento de la venta está la conversación, no puede enviar imágenes ni menús, no arma
pedidos ni recibe pagos, y el cliente de Leo tiene que describir su negocio escribiendo
instrucciones. Es el mismo diseño que hizo fallar a la primera versión del agente de Escuela
Capacita: cada promoción o producto nuevo obligaba a reescribir el prompt hasta que el agente dejó
de funcionar.

Esta feature convierte a Leo en un **vendedor que corre solo**. El dueño del negocio no escribe
prompts: llena formularios (pagos, horarios, envíos, preguntas frecuentes, promociones), elige
cómo quiere que hable el asistente y elige el proceso de venta de su rubro. El sistema calcula en
qué etapa está cada conversación a partir de hechos, le da al modelo solo las instrucciones y
herramientas de esa etapa, arma el pedido con precios de la base de datos, recibe el comprobante y
lo deja para que una persona lo confirme.

La referencia de experiencia es Ventor (configuración guiada, fácil para el cliente). La
diferencia de Leo es el **proceso por etapas calculado por el sistema**: el agente no decide en qué
etapa está, y por eso no se pierde ni repite pasos. La referencia funcional es el agente de Escuela
Capacita v21 en n8n, que ya funciona con este modelo.

## Contexto de Usuario

**Usuario principal:** dueño o administrador del negocio (tenant) que configura su asistente.
**Otros usuarios involucrados:**
- **Cliente final:** escribe por WhatsApp y compra.
- **Operador del tenant:** revisa pedidos y confirma o rechaza comprobantes.
- **Administrador de plataforma (Zentek360):** mantiene las plantillas de proceso.

**Flujo actual (sin esta feature):** el tenant completa perfil, catálogo, métodos de pago y
documentos; el agente responde con catálogo y RAG. Para vender, un humano tiene que tomar la
conversación, pedir datos, enviar cuentas y revisar el pago a mano.

## User Stories

### Historia 1: Configurar el asistente sin escribir prompts
**Como** dueño de un negocio,
**quiero** completar formularios sobre mi negocio y elegir cómo habla mi asistente,
**para** tenerlo vendiendo sin saber de inteligencia artificial.

**Criterios de aceptación:**
- WHEN el tenant abre "Asistente", THEN ve las secciones Información básica, Información del negocio, Comportamiento, Proceso de venta, Productos, Multimedia y Conocimiento, cada una con su estado (completa / opcional / falta).
- WHEN completa una sección y guarda, THEN el simulador refleja el cambio en el siguiente mensaje, sin publicar ni reiniciar nada.
- WHERE una sección es opcional y está vacía, THEN el asistente no inventa esa información y deriva si el cliente pregunta por ella.
- WHEN el tenant abre el panel, THEN ve una lista de activación con su progreso ("Tu asistente está 70 % listo") y el siguiente paso sugerido.

### Historia 2: Elegir el proceso de venta de su rubro
**Como** dueño de un negocio,
**quiero** elegir una plantilla de proceso (productos, cursos y certificaciones, servicios) y ajustar sus opciones,
**para** que el asistente lleve la venta como la llevaría mi mejor vendedor.

**Criterios de aceptación:**
- WHEN elige una plantilla, THEN ve sus etapas en orden, qué hace el asistente en cada una y qué opciones puede activar (calificación con hasta 3 caminos, pago previo o contra entrega, datos requeridos). La entrega (delivery, zonas, recojo) sale de Información del negocio.
- WHEN edita las instrucciones de una etapa, THEN puede volver al texto por defecto con un clic.
- WHERE no edita nada, THEN la plantilla funciona con sus textos por defecto.

### Historia 3: Conversación que avanza sola por etapas
**Como** cliente final,
**quiero** que el asistente me guíe desde la consulta hasta el pago sin repetirme preguntas,
**para** comprar rápido por WhatsApp.

**Criterios de aceptación:**
- WHEN llega un mensaje, THEN el sistema calcula la etapa a partir de hechos registrados (no del historial ni de la opinión del modelo) antes de llamar al modelo.
- WHEN el modelo responde, THEN solo tuvo disponibles las herramientas permitidas en esa etapa.
- WHEN algo ya se envió en la conversación (saludo, flyer, cuentas), THEN no se vuelve a enviar aunque el modelo lo intente.
- WHEN el cliente llega desde un anuncio de Meta, THEN el asistente va directo a lo anunciado sin hacer la calificación.

### Historia 4: Armar el pedido con precios reales
**Como** cliente final,
**quiero** elegir productos o paquetes y ver el total correcto,
**para** saber exactamente cuánto voy a pagar.

**Criterios de aceptación:**
- WHEN el asistente agrega un producto al pedido, THEN el precio sale del catálogo y el total lo calcula el sistema, nunca el modelo.
- WHERE el producto es un paquete que exige N elecciones (ej. "5 certificaciones"), THEN el pedido no avanza a datos hasta tener las N elecciones.
- WHERE hay una promoción vigente que aplica, THEN el sistema la aplica y el mensaje muestra el ahorro.

### Historia 5: Recibir el pago y que una persona lo confirme
**Como** operador del negocio,
**quiero** recibir cada comprobante con la foto y los datos leídos, y confirmarlo o rechazarlo con un clic,
**para** no revisar chats uno por uno y no aceptar pagos que nadie verificó.

**Criterios de aceptación:**
- WHEN el cliente envía una imagen en la etapa de cobro, THEN el sistema detecta si es un comprobante, lee monto, fecha, código de operación y entidad, lo asocia al pedido y lo deja "por verificar".
- WHEN el comprobante queda por verificar, THEN el asistente le dice al cliente que está en verificación y NUNCA que ya está confirmado.
- WHEN el operador confirma, THEN el pedido pasa a confirmado, el cliente recibe un aviso por WhatsApp y la conversación pasa a la etapa CERRADO.
- WHEN el operador rechaza con un motivo, THEN el cliente recibe el motivo y el asistente lo ayuda a corregir.
- WHEN el código de operación ya existe en otro comprobante del mismo tenant, THEN se marca como posible duplicado.

### Historia 6: Enviar imágenes, documentos y menús
**Como** dueño del negocio,
**quiero** subir mis imágenes y documentos una vez e indicar cuándo usarlos,
**para** que el asistente los envíe en el momento justo y una sola vez.

**Criterios de aceptación:**
- WHEN subo un archivo a Multimedia con una clave y "cuándo usarlo", THEN el asistente puede enviarlo con esa clave.
- WHEN el asistente necesita que el cliente elija, THEN envía botones (hasta 3) o una lista (hasta 10 filas) armados desde la base de datos, respetando los límites de WhatsApp.
- WHEN el cliente toca un botón o una fila, THEN el sistema registra su elección como hecho antes de llamar al modelo.

### Historia 7: Pagar lo justo a Meta
**Como** dueño del negocio,
**quiero** que el asistente responda en un solo mensaje cuando se puede,
**para** no pagar de más, porque Meta cobra cada mensaje desde el 1 de octubre de 2026.

**Criterios de aceptación:**
- WHEN el modelo responde, THEN el texto sale en 1 mensaje, o en 2 solo si supera el largo configurado y el nivel de detalle lo permite.
- WHERE el turno incluye imágenes o menús, THEN el total de mensajes salientes del turno no supera 3.

### Historia 8: Seguimiento y derivación
**Como** dueño del negocio,
**quiero** que el asistente retome a quien dejó de responder y que me pase los casos que no puede resolver,
**para** no perder ventas ni dejar clientes sin atender.

**Criterios de aceptación:**
- WHERE el seguimiento está activo, WHEN el cliente no responde en las horas configuradas, THEN el asistente envía seguimientos hasta el máximo configurado (2 por defecto), solo dentro de la ventana de 24 h de WhatsApp.
- WHEN el cliente pide una persona, se queja del bot o pregunta algo que ninguna herramienta resuelve, THEN el asistente deriva, se pausa y el tenant recibe un aviso.
- WHEN pasan las horas de pausa configuradas sin que un humano responda, THEN el asistente se reactiva.

## Requerimientos Funcionales

### Configuración guiada
1. **[RF-001]** El sistema debe ofrecer las secciones de "Información del negocio": métodos de pago, locales y horarios, entregas y envíos, tipos de cliente, preguntas frecuentes, promociones vigentes, cambios y devoluciones, contacto y redes. Cada una es opcional, salvo métodos de pago si la plantilla exige cobro.
2. **[RF-002]** El sistema debe ofrecer "Comportamiento" con: ritmo (directo, equilibrado, paso a paso), nivel de detalle (breve, moderado, extenso), seguimiento automático (activo, horas de espera, máximo de intentos), nivel de inteligencia (económico, medio, mejor) y modo (responde y gestiona, pausado).
3. **[RF-003]** Ninguna sección debe exigir que el tenant escriba un prompt. Las "reglas adicionales" son el único campo de texto libre y son opcionales.
4. **[RF-004]** El sistema debe mostrar una lista de activación con el progreso del tenant y el siguiente paso sugerido.
5. **[RF-005]** El simulador existente debe usar siempre la configuración guardada más reciente y mostrar, si el tenant lo activa, la etapa actual y los hechos de la conversación de prueba.

### Proceso de venta
6. **[RF-006]** El sistema debe ofrecer al menos 3 plantillas de proceso: Venta de productos, Cursos y certificaciones, Servicios con cotización.
7. **[RF-007]** Cada plantilla debe definir sus etapas, el orden de cálculo, la columna del embudo de cada etapa, las herramientas permitidas por etapa y las instrucciones por defecto.
8. **[RF-008]** El tenant debe poder activar o desactivar opciones de la plantilla (calificación con hasta 3 caminos y sus textos, pago previo o contra entrega, datos requeridos del cliente; la entrega se configura en Información del negocio) y editar las instrucciones de cada etapa, con opción de restaurar el texto por defecto.
9. **[RF-009]** Antes de cada llamada al modelo, el sistema debe calcular la etapa con una función determinista a partir de los hechos de la conversación.
10. **[RF-010]** Los hechos solo los escriben el sistema y las herramientas, nunca el texto libre del modelo.
11. **[RF-011]** El sistema debe registrar cada cambio de etapa y de columna del embudo con fecha y motivo.
12. **[RF-012]** El modelo solo debe recibir las herramientas permitidas en la etapa actual, más las globales (derivar, conocimiento y enviar recurso).
13. **[RF-013]** El prompt debe armarse con: reglas de plataforma, identidad, información del negocio en forma compacta, bloque de ESTADO con los hechos, instrucciones de la etapa actual y excepciones globales.

### Herramientas de acción
14. **[RF-014]** `enviar_recurso(clave)`: envía imagen, documento o video de la biblioteca una sola vez por conversación. Si ya se envió, no reenvía y le indica al modelo que no lo mencione.
15. **[RF-015]** `mostrar_opciones(tipo, texto)`: envía botones o lista nativa armados desde la base de datos (productos, categorías, promociones, caminos de calificación), truncando a los límites de WhatsApp.
16. **[RF-016]** `gestionar_pedido(accion, ...)`: agregar, quitar, cambiar cantidad, elegir ítems de un paquete, ver y vaciar. Precios y totales los calcula el sistema.
17. **[RF-017]** `registrar_datos_cliente(...)`: guarda los datos requeridos por la plantilla, acepta datos parciales y devuelve cuáles faltan.
18. **[RF-018]** `enviar_metodos_pago()`: envía los métodos de pago (texto e imagen o QR si existe) una sola vez.
19. **[RF-019]** `elegir_camino(camino)`: registra el camino de la calificación cuando el cliente responde escribiendo en vez de tocar un botón.
20. **[RF-020]** `derivar_a_humano(motivo)`: pausa el bot en esa conversación, registra el motivo y notifica al tenant.
21. **[RF-021]** Toda herramienta debe devolver al modelo un resultado explícito: hecho, omitido (ya se hizo) o no disponible, con la instrucción de qué decir.

### Pedidos y comprobantes
22. **[RF-022]** Cuando entra una imagen en las etapas COBRO o VERIFICACION (pago rechazado), el sistema debe clasificarla y, si es comprobante, extraer monto, fecha, código, entidad y nivel de confianza, sin depender de que el modelo llame una herramienta.
23. **[RF-023]** Cada comprobante queda "por verificar" asociado al pedido. El sistema nunca lo confirma solo.
24. **[RF-024]** El panel debe mostrar "Pedidos" con resumen (conversiones, valor, pendientes por periodo), filtros por estado y un detalle con lo que pidió el cliente, sus datos, la imagen del comprobante, los datos leídos y los botones Confirmar y Rechazar.
25. **[RF-025]** Al confirmar o rechazar, el sistema debe avisar al cliente por WhatsApp y actualizar los hechos de la conversación.
26. **[RF-026]** El sistema debe notificar al tenant cada comprobante nuevo (en el panel y por correo).

### Mensajes y costos de Meta
27. **[RF-027]** Un formateador determinista (sin LLM) debe convertir la respuesta al formato de WhatsApp, quitar notas internas y partirla en máximo 2 mensajes según el nivel de detalle y el largo configurado.
28. **[RF-028]** Ningún turno debe enviar más de 3 mensajes salientes en total.
29. **[RF-029]** Los contactos deben identificarse por el ID de usuario de Meta (BSUID) con el teléfono como dato opcional, y el envío debe funcionar sin teléfono.

### Seguimiento
30. **[RF-030]** Un trabajo programado debe enviar seguimientos a conversaciones en IDENTIFICAR, PEDIDO, DATOS, COBRO o VERIFICACION con pago rechazado, sin respuesta del cliente, respetando: seguimiento activo, horas de espera, máximo de intentos, ventana de 24 h, sin derivación y sin opt-out.
31. **[RF-031]** Si el tenant tiene una promoción marcada "para seguimiento", el último intento puede ofrecerla.

## Requerimientos No Funcionales

- **Performance:** p95 menor a 10 s desde que llega el mensaje hasta que sale la respuesta (meta existente de la 002). El cálculo de etapa y el armado del prompt no deben sumar más de 150 ms.
- **Costo:** prompt base (sin historial) de 6 000 tokens como máximo. Ninguna llamada extra al LLM para formatear.
- **Aislamiento:** todas las tablas nuevas llevan `tenant_id` y entran en la suite `tests/isolation`.
- **Calidad:** cobertura de 80 % o más en `process`, `agent` y `orders`; mypy y ruff limpios.
- **Evals:** nueva suite "flujos de venta" con al menos 8 conversaciones guionadas por plantilla; deben pasar el 90 % o más antes del merge.
- **Seguridad y datos personales:** comprobantes en MinIO privado con URL firmada de 10 minutos; DNI y dirección visibles solo para roles del tenant; auditoría al confirmar o rechazar un pago.
- **Facilidad de uso:** un tenant nuevo debe poder dejar su asistente vendiendo en el simulador en 30 minutos o menos, sin ayuda, usando una plantilla.

## Fuera de Scope (Esta Versión)

- Bandeja de entrada con respuesta humana desde el panel (feature 007). Aquí solo se marca la conversación como derivada y se notifica.
- Tablero de embudo tipo Kanban y métricas por etapa (feature 010). Aquí solo se guardan las etapas y sus eventos.
- Envío de etapas a la base central `control` del holding (se hace en la 010).
- Cobros de la suscripción de Leo (feature 009).
- Plantillas de Meta, envíos masivos y seguimientos fuera de la ventana de 24 h (fase 2 del roadmap).
- Constructor de automatizaciones, tablas libres y marketplace estilo QuryShare.
- Negociación de precios por el modelo. Los descuentos solo salen de promociones configuradas.
- Citas y reservas con calendario.
- Instagram, Messenger, TikTok y otros canales.
- Conexión directa como Tech Provider de Meta (trámite aparte; el adaptador de proveedor ya lo permite).

## Open Questions

- [ ] ¿El aviso al operador por WhatsApp (además del correo y el panel) entra ahora o en la 007? Decisión provisional: correo y panel ahora; WhatsApp al operador en la 007.
- [x] ¿La plantilla "Servicios con cotización" cobra servicios de precio fijo (ej. hosting) o siempre deriva? Resuelto: hace ambas cosas según `products.modo_venta` (ver `plantillas.md`).

## Criterios de Aceptación de la Feature

- [ ] **Capacita en Leo:** Escuela Capacita configurada como tenant con la plantilla "Cursos y certificaciones", solo con formularios, pasa las 6 conversaciones de prueba del agente n8n v21 (orgánica nueva, calificación respondida escribiendo, entrada por anuncio, paquete de 5 con 2 temas, comprobante, "lo pensaré").
- [ ] **Tienda en Leo:** un tenant con la plantilla "Venta de productos" y delivery completa un pedido de 3 productos con promoción, datos, cobro, comprobante y confirmación del operador.
- [ ] **Zentek360 en Leo:** un tenant con la plantilla "Servicios con cotización" deriva una cotización con resumen y vende un plan de hosting de precio fijo.
- [ ] En los tres casos, ningún recurso se envía dos veces y ningún turno supera 3 mensajes salientes.
- [ ] Suite de evals "flujos de venta" con 90 % o más; suite completa de tests, aislamiento, mypy y ruff en verde; cobertura de 80 % o más en módulos nuevos.
- [ ] Un usuario que no participó en el desarrollo configura un tenant nuevo hasta dejarlo vendiendo en el simulador en 30 minutos o menos.

## Dependencias

- **Features previas necesarias:** 001 (onboarding, catálogo, simulador), 002 (canal WhatsApp YCloud), 003 (motor v0 del agente).
- **Sistemas externos:** YCloud (mensajes de texto, media, interactivos), proveedor LLM con entrada de imagen (para comprobantes), MinIO, servicio de correo (Resend o el de desarrollo).
- **Referencia funcional:** agente Escuela Capacita v21 (n8n): tabla `etapas_agente`, subflujo *Calcular Etapa*, *Enviar Media*, *Enviar Interactivo*, *Registrar Pago*.
