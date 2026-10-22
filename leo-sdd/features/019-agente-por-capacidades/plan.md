# 019 · Leo como agente general: capacidades, oferta, rubros y canales

Estado: **dirección ratificada por Giannier (2026-10-21)**; el orden y los detalles se afinan fase a fase. Se apoya en
`investigacion.md` (mercado, reglas de plataforma y rubros, con el respaldo de cada afirmación).

## 0. El objetivo
Un agente que cualquier negocio configure para **atender, vender, cotizar, agendar y captar clientes** que llegan por
sus redes, y que sea mejor que lo que hay: capacidades que se componen según el rubro, traspaso a una persona con
contexto, resultados de negocio medibles y un arranque que no exige ser técnico.

## 1. Por qué el diseño actual no alcanza (resumen)
1. El cerebro (`process/engine.py`) es un embudo de pedidos con una sola cadena de etapas; las tres plantillas solo
   cambian textos y opciones. No hay cotización, cita, visita, captación ni soporte: lo que no es pedido se deriva.
2. El comportamiento lo fija el tipo de negocio y debería fijarlo la oferta (cada ítem se vende, se cotiza, se agenda).
3. Un solo canal, con la identidad atada al teléfono.
4. La configuración sigue al tipo de negocio, no a lo que el agente hará: pide lo que no usa y arrastra ejemplos de un
   rubro (cursos). Se conserva la base: aislamiento, puertos, reglas versionadas, conocimiento, bandeja, CRM, guardas,
   idempotencia y evals.

## 2. El modelo

### 2.1 Núcleo (siempre activo)
Conversación y memoria, contactos con identidad por canal, conocimiento, perfil y horarios, **traspaso tibio a una
persona**, CRM, métricas y guardas (no inventar, no prometer, declararse asistente virtual si preguntan, no salirse del
negocio: la política de Meta solo permite IA **del negocio**).

### 2.2 Capacidades (las enciende el negocio)
| Capacidad | Cerrar significa | Pasos de configuración | Estado |
|---|---|---|---|
| **Atender** | resolver y dejar siguiente paso | conocimiento, horarios, ubicación, preguntas frecuentes | hecho |
| **Vender** | pedido pagado | catálogo, pagos, entregas | hecho (plantillas) |
| **Cotizar** | cotización con datos completos, enviada, seguida hasta aceptada | datos a pedir por servicio, precio referencial, a quién se deriva, plantilla de propuesta | parcial |
| **Agendar** (citas, visitas, reservas, pruebas de manejo) | cita confirmada y recordada | servicios agendables, duración, recursos y capacidad, horarios, calendario | especificada (014) |
| **Captar y calificar leads** | lead calificado (caliente, tibio, frío) con siguiente paso | preguntas de calificación del rubro, destino del lead | parcial (CRM) |
| **Postventa** | pedido o trámite resuelto | estado del pedido, devoluciones, garantía | parcial (avisos de pedido) |
| **Seguimiento y campañas** | respuesta o recompra | plantillas, cadencias | hecho (011) y por extender |

Cada capacidad trae: pasos de configuración, datos, herramientas para el modelo, **su cierre**, su efecto en el CRM y
sus **comprobaciones de calidad** (evals).

### 2.3 Motores transversales (sirven a varias capacidades)
1. **Recordatorios y confirmaciones**: 24 h y 2 h antes (configurable), con confirmar, reprogramar o cancelar; varios
   mejor que uno; plantilla aprobada fuera de 24 h. Sirve a citas, reservas, reuniones, pagos pendientes.
2. **Seguimiento con cadencia**: cotizaciones, leads y pedidos sin pagar; día 2 a 3, 7, 14 y cierre a los 21 a 25; cada
   toque aporta algo; mensaje de cierre cortés; respeta opt-out y la ventana.
3. **Traspaso tibio**: resumen estructurado (qué quiere, qué se le ofreció, datos recogidos, ánimo, motivo), petición
   explícita atendida de inmediato, fuera de horario ofrece llamada de vuelta, tiempo hasta respuesta humana medido.
4. **Calificación y puntaje** (caliente, tibio, frío) por reglas del rubro; el agente pregunta, no adivina.
5. **Calidad y piloto**: conjunto de prueba generado del negocio como requisito para encender; **modo piloto** (una
   persona aprueba las primeras respuestas o solo un porcentaje de conversaciones va al agente); muestreo semanal de
   transcripciones; contención medida junto con exactitud, escalamiento apropiado y contactos repetidos.
6. **Atribución de origen**: anuncio o enlace de red social (clic de anuncio guardado), para medir costo por resultado.

### 2.4 Oferta (cada ítem lleva su forma de venderse)
Tipo (producto, servicio, plan, curso, reserva…) y **cómo se vende**: pedido directo, cotización (con o sin precio
referencial), cita o solo informativo. Más: periodo del precio (único, mes, año), categoría, duración y recursos (para
agendar), **los datos que hay que pedir** (para cotizar o agendar) y fotos. El agente se comporta según el ítem del que
se habla. Las tres plantillas actuales pasan a ser **presets**.

### 2.5 Paquetes de rubro (datos, no código)
Un paquete trae: capacidades sugeridas, preguntas de calificación, objeciones y preguntas frecuentes, situaciones,
mensajes de recordatorio, campos de oferta típicos, ejemplos y **avisos de política** (qué no se puede vender por
WhatsApp). Rubros a cubrir (de `investigacion.md`): restaurante y café, tienda y comercio electrónico, salud y
bienestar, belleza y fitness, educación, inmobiliaria, automotriz, servicios del hogar y técnicos, servicios
profesionales, turismo y hotelería, B2B y distribución, tecnología y software, y "otro" con ejemplos neutros.

### 2.6 Canales
Puerto de canal con identidad por contacto (`canal`, `id externo`), y una sola bandeja y CRM. Orden: (1) WhatsApp con
atribución de origen; (2) chat web; (3) Instagram y Messenger (24 h como límite duro; la etiqueta de agente humano solo
para respuestas manuales; revisión de Meta). Redes sin API de mensajes entran por enlace a WhatsApp. Varias sedes:
número único que pregunta la sede, o un número por sede, con conocimiento y calendario **por sede**.

### 2.7 El motor: enrutador y subflujos
Siguiendo el consejo de usar flujos predefinidos donde los pasos son fijos y un agente donde es abierto:
- un **enrutador** deduce el objetivo de la conversación (vender, cotizar, agendar, calificar, informar, postventa) de la
  oferta de la que se habla y del mensaje (clasificación barata, posible modelo menor);
- cada capacidad es un **subflujo con sus etapas y herramientas**: el de pedidos (el actual) se mantiene sin cambios; las
  transacciones (reservar un horario, registrar una cotización) son **flujos fijos con bloqueo y validación**, no
  decisiones libres del modelo;
- el bloque de venta ya escrito (sondear, recomendar, objeciones, siguiente paso) se generaliza: cada capacidad aporta su
  propio cierre.

## 3. La configuración que sale de ahí
1. **Tu negocio**: nombre, rubro, **web (una vez, con "leer mi web")**, moneda, zona.
2. **Qué hará tu agente** (capacidades) y **por dónde te escriben** (canales).
3. **Tu oferta**: ítems con tipo y cómo se venden, categorías (con "crear"), descripciones.
4. **Pasos de cada capacidad encendida**: cobros; datos a pedir al cotizar; servicios, horarios y calendario al agendar;
   preguntas de calificación.
5. **Tu agente**: nombre, tono, saludo, objetivo.
6. **Pruébalo y enciéndelo**: preguntas de prueba, modo piloto, una sola lista de progreso calculada.
Cada dato tiene un solo dueño. El Asistente es el mismo recorrido como menú. Los ejemplos salen del rubro elegido.

## 4. Hoja de ruta
| Fase | Contenido | Plazos externos |
|---|---|---|
| **A · Oferta y capacidades** (hecha: migraciones 0049 a 0052; falta el aviso de política por rubro) | oferta con tipo y forma de venta; periodo del precio; categorías con pantalla y creación; web una vez; ejemplos neutros; capacidades como interruptores y lista de progreso única; presets en lugar de plantillas; avisos de política por rubro; rubros nuevos | ninguno |
| **B · Cotizar de verdad** | datos a pedir por servicio, resumen estructurado, estado de la cotización en el CRM, seguimiento con cadencia | ninguno |
| **C · Traspaso, calidad y piloto** (hecha: modo piloto, tiempo de respuesta humana, traspaso tibio con fuera de horario / lo ofrecido / ánimo, aviso de que es un asistente, revisión semanal de conversaciones reales y preguntas de prueba generadas del negocio; queda decidido por Giannier el calificado automático de ese conjunto, que cuesta llamadas al modelo) | traspaso tibio, tiempo de respuesta humana, conjunto de prueba, modo piloto, muestreo | ninguno |
| **D · Agendar y recordar** (hecha: migración 0055, `scheduling/`, tres herramientas, agenda y configuración en el panel; falta Google Calendar y el piloto) | 014: calendario propio con bloqueo anti doble reserva, recursos y capacidad, recordatorios; adaptadores Cal.com y Google Calendar | plantillas de WhatsApp para recordatorios; verificación de Google |
| **E · Captar y calificar** (hecha: migraciones 0057 y 0060, herramienta `calificar_cliente`, puntaje en el CRM, aviso de lead caliente, destino del lead —asignado y avisado por correo— y reunión desde el lead) | preguntas por rubro, puntaje, destino del lead, reunión | ninguno |
| **F · Canales** (hecha en parte: chat web con página pública y widget, migración 0059, y atribución de origen —enlace por red social, texto del cliente, Resultados por origen—; faltan Instagram y Messenger, que esperan la revisión de Meta) | identidades por canal, chat web, atribución de origen, luego Instagram y Messenger; sedes | revisión de Meta (semanas) |
| **G · Postventa y B2B** (hecha: el asistente consulta el estado de los pedidos y los repite, registra devoluciones, cambios, garantías y reclamos (una persona decide) y envía los documentos del pedido; documentos adjuntos y resumen en PDF; boleta y factura electrónicas con Nubefact desde el panel; migraciones 0061 y 0062; ver C-08 y C-09) | estado de pedido, devoluciones, repetir pedido, documentos | cuenta propia con el proveedor de facturación |
| **H · Aprender** (hecha: bandeja de sugerencias con historial y deshacer, importación de chats con audios, ejemplos reales, estilo, cuatro fuentes, el rubro aprende y el redactor asistido; migraciones 0063 a 0065; ver C-10) | resultados por capacidad y rubro; mejora de instrucciones con revisión humana (017, fases 2 a 4) | tope semanal de costo, consentimiento, Ley 29733 |
| **I · Pedidos y entregas** (hecha: 016, migración 0066; etapas del trabajo sobre el pedido, comanda con sonido y tiempos, avisos «listo» y «en camino», ubicación de WhatsApp como dirección, reembolso resuelto y tiempo medio de entrega; ver D-1 a D-8 en `016-pedidos-delivery`) | el pedido tiene el recorrido que el negocio hace después de cobrar | ninguno |

## 5. Riesgos
- Cambiar el motor es lo más grande: se hace por capas y con los evals actuales (24 flujos y calidad de venta) como red.
- Agendar necesita un calendario confiable: bloqueo para no duplicar reservas y reglas de zona horaria.
- Recordatorios fuera de 24 h exigen plantillas aprobadas; Instagram y Messenger exigen revisión de Meta; Google exige
  verificar el permiso sensible.
- La política de Meta sobre IA: Leo debe seguir siendo del negocio y no un asistente abierto.
- Costos de mensajes de Meta desde el 1-oct-2026: mostrar costo estimado por resultado.

## 6. Decisiones
1. **Ratificada:** el modelo (núcleo, capacidades, oferta, canales).
2. **Ratificada:** orden A, B, C, D, E, F, G, H (con C antes de D por ser de calidad).
3. Agendar: calendario propio primero; Cal.com y Google como adaptadores (por la verificación de Google).
4. Canales: iniciar el trámite con Meta cuando termine la fase C.
