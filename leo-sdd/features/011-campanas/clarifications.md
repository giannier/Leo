# Clarificaciones — 011 Campañas, plantillas y seguimientos

## C-101: Ratificación (Giannier, 2026-10-18 y 2026-10-19)
"Continúa, necesito todo el proyecto terminado". Se construye la 011 después de la 012. Las cuatro
decisiones de dinero, datos personales y costo del LLM se consultaron y Giannier eligió (2026-10-19):
1. **Opt-in por botón, sin IA** (C-102).
2. **Importar listas sí, con tope**: casilla de responsabilidad, texto de la declaración guardado y máximo
   200 contactos importados por semana (C-001 de la spec).
3. **Interés de compra desde el proceso de venta**, no desde una señal del asistente (C-104).
4. **Costo estimado visible y topes por plan**, contados pero no aplicados mientras el cobro esté apagado
   (`billing_enforced()`).
**Registrado por:** Claude — ratificado en el chat

## C-102: Opt-in por botón tras una venta confirmada (reemplaza la tool `registrar_optin`)
Cuando el negocio confirma un pago, junto al aviso de pago confirmado Leo envía **una sola vez** por
contacto un mensaje con dos botones: "¿Quieres que {negocio} te avise de promociones y novedades por
aquí?" → "Sí, avísame" / "No, gracias". La respuesta se guarda como evidencia (`contact_optins`: id del
mensaje, texto mostrado, fecha) y se contesta con un texto fijo; el asistente no interviene (cero tokens,
sin cambios de prompt ni evals). El negocio lo apaga en Campañas → Contactos (`agent_configs.optin_preguntar`,
encendido por defecto). Solo va si la ventana de 24 h está abierta (siempre lo está tras un pago).
**Registrado por:** Claude — ratificado (opción elegida por Giannier)

## C-103: Baja = revoca el permiso de promociones
Escribir BAJA/STOP (o marcar la baja en la bandeja) marca `contacts.opted_out_at` (F002) **y** revoca el
opt-in de promociones. Si después el cliente vuelve a escribir, el asistente le responde (F002), pero no
vuelve a recibir campañas hasta aceptar de nuevo. Lectura conservadora: el consentimiento para publicidad no
se presume por volver a conversar.
**Registrado por:** Claude — por ratificar

## C-104: Intención de compra = el proceso de venta
Sin señal `interes` del asistente (costo del LLM). Para segmentar se usan la etapa del lead del CRM (012)
y los pedidos: "en Nuevo/Interesado/Por pagar", "compró alguna vez", "compró o consultó el producto X".
Los seguimientos fuera de ventana usan las mismas etapas que los seguimientos de la 004.
**Registrado por:** Claude — ratificado (opción elegida por Giannier)

## C-105: Variables con nombre en vez de {{1}}
El editor ofrece `{{nombre}}`, `{{negocio}}` y `{{producto}}`; al enviar a Meta se convierten a `{{1}}`,
`{{2}}`… con ejemplos automáticos. Un destinatario sin valor para una variable se omite con el motivo
(C-004 de la spec). El negocio no escribe posiciones ni ejemplos.
**Registrado por:** Claude — informativa

## C-106: Tope diario del número
Meta limita los contactos nuevos por día según el nivel del número (250 al empezar). Mientras no sepamos el
nivel, Leo envía como máximo **250 plantillas por número en 24 h**; el operador de plataforma puede subirlo
por número (`whatsapp_numbers.limite_diario`). Lo que no entra hoy sigue mañana dentro del horario.
Lotes de 30 por minuto; la conversación en vivo va en su propia cola y no espera a las campañas.
**Registrado por:** Claude — por ratificar

## C-107: Tarifas de Meta
Tabla `meta_rates` (país, categoría, US$ por mensaje, vigente desde), mantenida por el operador de
plataforma. Semilla con las tarifas publicadas por Meta para Perú (marketing 0.0703, utility 0.0200,
vigentes desde 2025-07-01); **verificarlas antes del piloto**. El estimado se muestra como "aprox." y aclara
que lo cobra Meta a través de YCloud, no Leo.
**Registrado por:** Claude — por ratificar

## C-108: Topes por plan (referencia de la spec)
Prueba 1 campaña × 100 destinatarios, Emprende 2 × 500, Crece 8 × 2 000, Pro 20 × 10 000. Con el cobro
apagado se muestran como aviso y no bloquean.
**Registrado por:** Claude — por ratificar

## C-109: La respuesta a una campaña sigue en la misma conversación
La plantilla se guarda en la conversación del contacto (se abre una nueva si la anterior quedó vieja,
marcada con la campaña de origen). Cuando el cliente responde, esa conversación sigue (no se cierra por la
regla de 24 h de F002), el asistente ve qué se le envió y la bandeja muestra la campaña. Una respuesta
dentro de 72 h marca al destinatario como "respondió"; una venta confirmada en 72 h cuenta como venta
atribuida (se calcula al ver el reporte, como las métricas del CRM).
**Registrado por:** Claude — informativa

## C-110: Seguimiento fuera de ventana
Se suma a los seguimientos de la 004: si la ventana de 24 h ya cerró (o cierra en menos de 30 min) y el
negocio eligió una plantilla **utility aprobada**, el último seguimiento va como esa plantilla, sin el
modelo. Topes duros: 1 por conversación y 2 por contacto al mes. Respeta derivación, baja y horario.
**Registrado por:** Claude — informativa

## C-111: Reglas de Meta desde el 1 de octubre de 2026
Giannier pidió revisar los cambios de Meta (2026-10-19). Resumen en `leo-sdd/meta-reglas-2026-10.md`:
las respuestas dentro de las 24 h (mensajes de servicio) se cobran pasadas las 1,000 gratis al mes por
número, y las plantillas de utilidad dentro de la ventana también. En Leo: categoría `service` en
`meta_rates` y tarifas de Perú desde el 2026-10-01 con la cifra más alta de las fuentes (marketing 0.08085,
utilidad 0.0345, servicio 0.030; migración 0042), el gasto estimado del mes en Resultados, y el error
131049 (tope de ~2 marketing por persona al día) como motivo propio en el reporte. La pregunta de permiso y
la encuesta ya no son "sin costo": cuentan como un mensaje de servicio. Pendiente para Giannier: agrupar
las respuestas del asistente en menos burbujas (cada burbuja es un mensaje cobrado).
**Registrado por:** Claude — por ratificar
