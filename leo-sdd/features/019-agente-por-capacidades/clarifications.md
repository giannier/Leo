# 019 · Clarificaciones

## C-01: el DNI en el chat (pendiente de Giannier; datos personales)
**Qué pasa.** La plantilla `certificaciones` pide `dni`, nombres, apellidos, correo y teléfono antes de cerrar
(`process/templates/certificaciones.py`). La política de mensajería de WhatsApp Business (sección 3, actualizada el
2026-09-23) dice no compartir ni pedir "números completos de tarjeta, de cuenta, de documento de identidad u otros
identificadores sensibles" (`investigacion.md` 2.1). Meta decide a su criterio y puede restringir el número.

**Lo que ya hace Leo para proteger el dato:** el DNI registrado se enmascara en el historial que va al modelo
(`agent/privacy.py`).

**Opciones (la decisión es de Giannier, no se cambió nada):**
1. Dejar el DNI como dato opcional en el chat y recogerlo después, al emitir el certificado, por un formulario o por una persona.
2. Pedirlo solo cuando la persona ya pagó y por un enlace a un formulario propio.
3. Mantenerlo como está y aceptar el riesgo.

**Recomendación:** la 1; el cierre no depende del DNI y el riesgo de perder el número es mayor que la comodidad.

## C-02: la agenda (fase D)
**Decidido por conservadurismo, sin tocar dinero ni datos nuevos del cliente:**
1. **Un servicio agendable es un producto** con duración y pausa; no hay un catálogo de servicios aparte.
2. **Los recordatorios empiezan apagados** y no se pueden encender sin una plantilla de utilidad aprobada: salen días
   antes, cuando la ventana de 24 h del cliente cerró, y cada mensaje cuesta (utilidad, ver `meta-reglas-2026-10.md`).
   Dentro de la ventana el recordatorio va como texto. De 08:00 a 21:00 en la hora del negocio.
3. **Máximo 2 citas vivas por persona** y anticipación de 2 horas a 30 días, editables.
4. **En el simulador no se escribe la cita**: una prueba no debe ocupar el lugar de un cliente real.
5. **El recordatorio de 24 h no se envía** a una cita agendada con menos de 24 h de anticipación.

**Fuera de alcance por ahora:** Google Calendar y Cal.com (verificación de Google de varios días y dependencias
nuevas), pagos o señas para reservar, citas grupales, varias zonas horarias.

## C-03: captar y calificar (fase E)
**Decidido por conservadurismo (no toca dinero, datos nuevos del cliente ni el costo del modelo):**
1. **El asistente juzga el interés** (`alto`, `medio`, `bajo`) con una rúbrica en su prompt y no con puntos sumados:
   alto = necesidad concreta y plazo corto, o pide precio, cita o propuesta; medio = interés con plazo o presupuesto
   sin definir; bajo = solo curiosidad. Se guardan las respuestas y el motivo, para que una persona vea por qué.
   El puntaje del lead es `caliente`, `tibio` o `frio`. (La investigación del mercado describe puntajes por puntos,
   pero no da umbrales validados; con pocas preguntas por conversación un juicio con rúbrica es más honesto.)
2. **Las preguntas** son las del rubro (3 o 4 por rubro, migración 0057) y el negocio puede escribir las suyas (hasta
   5). Se hacen **de a una**, solo las que falten, y el prompt prohíbe pedir tarjeta o documentos (política de
   WhatsApp, `investigacion.md` 2.1).
3. **Aviso de lead caliente** por correo y aviso del panel la **primera** vez que un cliente pasa a `alto`; no se
   repite mientras siga en `alto`.
4. **Un cliente calificado ya es un lead** aunque no haya pedido (`nuevo` si es frío, `interesado` si no).
5. Medido con el modelo real: 3 de 3 casos en 3 corridas seguidas (`evals/sales_flows.py`, negocio `captacion`).

**Pendiente de esta fase:** el destino del lead (a quién se asigna) y agendar la reunión desde el lead.

## C-04: modo piloto y tiempo de respuesta humana (fase C)
1. **Modo piloto = porcentaje de clientes, no aprobación de cada respuesta.** `agent_configs.piloto_pct` (0 a 100, 100 por
   defecto: nada cambia). El lado se decide **por persona** (`contact.id % 100 < piloto_pct`): un cliente siempre
   lo atiende el mismo lado y el porcentaje es el mismo para cualquiera que mire. Los de fuera del piloto quedan en la
   bandeja para el equipo (el motivo de la compuerta es `outside_pilot`). El simulador no se ve afectado.
   *Por qué no aprobar cada respuesta:* duplicaría la bandeja y haría esperar a cada cliente; el porcentaje deja
   subir de a poco y medir con los resultados.
2. **Tiempo de respuesta humana:** desde el evento que pasa la conversación a DERIVADO hasta el primer mensaje de una
   persona (ventana de 7 días). Mediana y percentil 90 en minutos, y aparte los traspasos **sin responder**, porque
   la mediana de los respondidos los escondería. Se calcula en vivo en el reporte (Resultados), sin dinero.
3. **Pendiente:** conjunto de prueba generado desde el negocio como requisito para encender (cuesta llamadas al modelo:
   decisión de Giannier) y muestreo semanal de transcripciones.

## C-05: postventa (fase G)
1. **`consultar_pedidos`** (solo lectura) está disponible para todo negocio que vende. Lee los **3 últimos pedidos del
   mismo cliente** (nunca el borrador en armado, nunca los de otra persona; el simulador ve los suyos) y dice el estado en
   palabras, las líneas, el total y lo que el negocio escribió como siguiente paso. **No promete fechas** que el negocio
   no escribió. Un pedido cancelado con devolución pendiente se pasa a una persona.
2. **Repetir un pedido** (el cliente B2B que pide "lo mismo") no es una herramienta aparte: el asistente lee el último
   pedido y agrega los mismos productos con `gestionar_pedido` (la herramienta devuelve `producto_id` y cantidad).
3. Medido con el modelo real: 10 de 10 casos de `productos` en 4 corridas con la redacción final de la herramienta (antes
   de afinarla, "lo mismo que la vez pasada" fallaba: el modelo contestaba que no veía nada y no llamaba a la herramienta).

## C-06: el chat web (fase F)
**Por qué este canal primero:** Instagram y Messenger exigen la revisión de la aplicación de Meta (semanas) y no se pueden
probar de punta a punta aquí. El chat web no depende de nadie y sirve para lo que el dueño pidió, "canalizar a quien llega de
sus redes": el **enlace del chat** va en la biografía de Instagram o TikTok y el **código** va en su página.
1. **Un canal real:** `conversations.channel = 'web'` (migración 0059). Aparece en la bandeja, en Resultados y cuenta para la
   cuota (`REAL_CHANNELS`). El asistente es el mismo: agenda, cotiza, vende, califica. Una persona del equipo responde desde la
   bandeja y el visitante lo ve. Solo texto (las herramientas que mandan archivos o botones contestan `no_disponible`).
2. **Visitante sin teléfono:** un contacto `web:<uuid>`; la sesión se crea **al primer mensaje**, no al abrir la página, y su
   token (firmado, 7 días) vive en el navegador. Un visitante solo lee y escribe su conversación.
3. **Un endpoint público cuesta dinero a quien lo encuentre**, por eso: apagado hasta que el dueño lo active; 10 sesiones por
   dirección cada 10 min; 12 mensajes por minuto por sesión; **tope diario por negocio** (300 por defecto, entre 10 y 5000);
   500 caracteres por mensaje; la clave pública se **rota** si se filtra. Un chat apagado y uno que no existe contestan igual.
4. **Sin seguimientos ni encuestas por WhatsApp** a un visitante web (no hay teléfono): `followups` lo salta.
5. **Sin CORS:** el widget es un `iframe` a la página `/chat/<clave>?embed=1` del mismo dominio de la API.
**Pendiente:** Instagram y Messenger (adaptador de Meta, cuando se apruebe la aplicación) y la atribución de origen
(`ctwa_clid` ya llega en los anuncios de WhatsApp).

## C-07: lo que quedó sin depender de Meta (fases C, E, F y G)
1. **Aviso de que es un asistente (regla 10 de la plataforma).** Si el cliente pregunta si habla con una persona o un robot, el
   asistente lo dice en una frase y ofrece pasarlo a una persona. Primera línea del prompt: «Eres {asistente}, asistente
   virtual de {negocio}». Medido con el modelo real (caso nuevo en `evals/sales_flows.py`).
2. **Traspaso tibio:** el mensaje al cliente dice cuándo lo atienden (fuera de horario: «te escribimos a las 09:00 del lunes»),
   y la nota para el equipo trae lo que ya se le ofreció y el ánimo del cliente. Motivo nuevo `postventa`.
3. **Origen del cliente** (`contacts.origen`, `leads.origen`): sale del enlace (`?o=instagram` en el chat web, el texto «Hola, te
   escribo desde Instagram» del enlace de WhatsApp) o de lo que el cliente dice. «Directo» si no hay dato. Resultados lo cuenta con
   los ganados. El panel genera un enlace por red social.
4. **Destino del lead caliente** (`agent_configs.destino_leads`): debe ser alguien del equipo; el lead se le asigna (si no tenía
   dueño) y se le avisa por correo, además del dueño. Sin destino llega a todo el equipo.
5. **Revisión semanal** (`conversation_reviews`): una muestra de conversaciones reales de la semana que el revisor marca bien,
   regular o mal con el motivo (inventó un dato, tono, no resolvió, debió derivar, muy largo, repetitivo). Sin llamadas al modelo.
   **Preguntas de prueba** del simulador, armadas con los datos del negocio (producto, servicio agendable, pago, entrega).
   *Decisión de Giannier pendiente:* calificar solas esas preguntas con el modelo (cuesta llamadas).
6. **Agenda:** las citas que dejaron de caber (cambio de horario, recurso apagado, bloqueo) salen en un aviso con motivo, **sin
   cancelarse solas**; cada cita muestra las faltas anteriores del cliente; una reunión se agenda desde el lead y queda ligada a su
   contacto. Dos primeras solicitudes a la vez ya no duplican el recurso «Atención» ni la fila de ajustes (lo encontró la prueba de
   concurrencia: 100 intentos al mismo minuto dan exactamente una cita; con 2 recursos y 5 horas, exactamente 10).
7. **Rubro:** preguntas sugeridas para cotizar por rubro (`rubros.datos_a_pedir_sugeridos`), con botón en el catálogo.
8. **Resultados por capacidad:** citas agendadas (por el asistente / por una persona, completadas, faltas), cotizaciones pedidas y
   enviadas, clientes calificados (calientes, tibios, fríos). Un bloque en cero no aparece.
**Siguen fuera, por decisión:** Instagram y Messenger (revisión de Meta), seguimientos con plantilla pasadas las 24 h (costo;
Giannier lo dejó para después), Google Calendar y Cal.com (verificación y cuentas externas), la fase H de aprendizaje (consentimiento
y Ley 29733), el calificado automático de preguntas de prueba (costo del modelo) y el DNI (C-01).

## C-08: devoluciones y documentos de postventa (fase G)
**Pedido de Giannier (2026-10-21):** «devoluciones y documentos de post venta», y en documentos eligió **ambos**: adjuntar y
enviar los del negocio, y también emitir boleta/factura electrónica con un proveedor.
1. **El asistente registra, una persona decide.** `solicitar_devolucion` crea una solicitud (`order_returns`: devolución,
   cambio, garantía o reclamo; motivo; lo que dijo el cliente; las fotos que mandó en las últimas 3 h) y avisa al equipo por el
   panel y por correo. **Nunca** promete reembolso, cambio, recojo ni fechas: devolver dinero es del negocio. La decide una
   persona del equipo (aprobar o rechazar con el texto que leerá el cliente, resolver, cancelar), y el cliente lo lee en su
   chat dentro de la ventana de 24 h (si está cerrada, el panel avisa). Cada paso deja un evento (`order_return_events`).
2. **La política del negocio** (sección «cambios y devoluciones») la respeta la herramienta **solo para un cambio de
   opinión** (`no_conforme` u `otro`): si el negocio no hace devoluciones, o el pedido pasó su plazo, no se registra y el
   asistente explica la política y pregunta si quiere que una persona evalúe su caso (entonces deriva). Un producto
   defectuoso, equivocado o incompleto, una garantía y un reclamo **se registran siempre**: no son culpa del cliente.
   *Lectura conservadora, para ratificar.* Una persona del equipo puede registrar cualquier excepción desde el panel.
3. **Una solicitud abierta por tipo y pedido** (índice único parcial): pedirla dos veces encuentra la primera.
4. **Los documentos no son biblioteca.** `order_documents` vive aparte de `media_assets`: la biblioteca es el catálogo de
   archivos del negocio y se alcanza por una clave que el modelo puede nombrar; la boleta de un cliente no. El asistente solo
   envía un documento del pedido **del propio cliente** (se comprueba por la conversación), nunca uno anulado, y si no está
   adjunto no lo inventa: avisa al equipo (`documento_solicitado`) y le dice al cliente que se lo harán llegar.
5. **Resumen del pedido en PDF** hecho por Leo (sin librería nueva: `aftersale/pdf_writer.py`), que dice que **no es un
   comprobante de pago**. Emitir un comprobante electrónico es otra cosa (ver C-09).
6. **Costo del prompt:** las dos herramientas y su bloque «POSTVENTA» suman unos 350 tokens por turno a los negocios que venden
   (tope de herramientas 1 900 → 2 100; total de la mayor configuración ~5 250 de 6 000).

## C-09: facturación electrónica (fase G, «documentos»: ambos)
**Pedido de Giannier:** emitir boleta y factura electrónicas, además de adjuntar los documentos del negocio.
**Qué se encontró (2026-10-21).** Para emitir un comprobante electrónico, el negocio necesita ser emisor electrónico ante la
SUNAT (RUC y clave SOL), y desde un sistema propio debe usar un OSE o un PSE; la SUNAT también ofrece una emisión gratuita desde
su portal para boletas. **Nubefact** es un PSE/OSE con una API REST en JSON (ruta y token por cuenta, y un entorno de pruebas en
`demo.nubefact.com`). **Verificado** con sus archivos de ejemplo y su código PHP, descargados de nubefact.com/integracion: el
formato del cuerpo (los 48 campos y los 15 del ítem, que la prueba compara uno a uno), los códigos de comprobante, de documento,
de IGV y de unidad, la cabecera `Authorization: Token token="…"`, y los campos de la respuesta. **No verificado** (el manual es un
documento de Google tras un inicio de sesión que no se pudo leer): el modo de identificar a un comprador sin documento
(`"-"`), el umbral de S/ 700 para una boleta sin documento (regla general de la SUNAT, no leída aquí) y la respuesta exacta de
`consultar_comprobante`. Si alguno falla, el proveedor lo dice con su propio texto y el panel lo muestra tal cual.
**Decisiones (lectura conservadora, para ratificar):**
1. **Nunca emite el asistente.** Lo emite una persona del equipo con un clic, en dos pasos (ver cómo quedaría, emitir). Un
   comprobante es un documento tributario y el negocio responde por él. Anular lo hacen solo el dueño y los administradores.
2. **La cuenta es del negocio**: su ruta y su token, escritos en el panel (nunca por el chat), cifrados, jamás devueltos, y el
   cambio queda en la bitácora sin el token. La ruta solo puede ser https y de `nubefact.com` o `pse.pe` (si no, un negocio haría
   que nuestro servidor llame a la dirección que quiera). Apagada hasta que el dueño la encienda.
3. **Los números de una serie son seguidos**: Leo lleva el siguiente número de cada serie bajo un candado y lo mueve solo cuando
   el proveedor acepta; si rechaza, el número se devuelve. Si la respuesta se pierde después de enviar, se consulta el número
   antes de hacer nada más (nunca dos comprobantes). Un pedido tiene un solo comprobante vivo (índice único).
4. **Importes:** los precios incluyen el impuesto; la base de cada línea es el total entre 1,18 (o sin impuesto si el negocio es
   exonerado o inafecto); el comprobante suma el total del pedido al céntimo, y si no suma se rechaza en vez de emitir algo
   aproximado; el descuento del pedido es el descuento global del comprobante; el envío es una línea de servicio. Probado con
   500 pedidos al azar y con los dos ejemplos oficiales (708 con base 600 e IGV 108; con descuento global de 300, base 300, IGV 54).
5. **Por ahora:** solo soles; boleta (DNI, RUC o sin documento hasta S/ 700) y factura (RUC válido con su dígito de control).
   **No se hacen notas de crédito**: una devolución con reembolso se anula (si procede) o se resuelve con tu contador.
6. **El DNI no se pide por el chat** (política de WhatsApp, C-01): se toma del pedido si el cliente ya lo dio, o lo escribe una
   persona que lo pidió por teléfono o en persona.
7. **Para probar:** abre una cuenta de pruebas en Nubefact, pega su ruta y su token, emite una boleta y revísala. El panel avisa
   mientras la ruta sea la de pruebas. *Antes de usarla con clientes reales hace falta esa prueba: el adaptador no se ha
   ejecutado contra el servicio real.*

## C-10: la fase de aprendizaje (017 fases 2 a 4; 019 fase H)
**Pedido de Giannier (2026-10-21), con sus respuestas:** audios con tope por importación; entre negocios solo el texto de la
regla y con su revisión; costo apagado hasta activarlo y con tope semanal. Se ratifican así C-06 a C-08 de la 017.
1. **El agente propone y una persona decide.** Todo lo aprendido es una *sugerencia* (`suggestions`): una situación, un ejemplo real,
   un dato para el conocimiento o una forma de escribir. Entra en vigor al aprobarla (tal cual o editada), deja una línea en el
   historial con cómo estaba antes y se puede **deshacer** (`config_history`: situaciones, interruptores de reglas, perfil, preguntas
   frecuentes, ejemplos y estilo). Una rechazada no vuelve a proponerse igual (la huella es única por negocio). Un dato exige que el
   revisor escriba la respuesta: el asistente no inventa lo que el negocio contesta.
2. **Costo (decisión de Giannier).** `learning_settings.activo` empieza **apagado**; encendido, hay un **tope semanal de llamadas al
   modelo** (20 por defecto, editable por el dueño; la semana empieza el lunes) y cada análisis lo descuenta antes de hacerse. Una
   importación necesita 5 y se rechaza antes de empezar si no alcanzan. Las preguntas sin respuesta se cuentan sin llamar al modelo.
3. **Importar chats.** El archivo exportado (.txt, o .zip con los audios) se lee **en memoria**; el original espera en un almacén
   temporal para el worker (2 h como máximo) y se **borra en un `finally`**, también si falla; no se guarda ni su texto ni los nombres.
   El negocio **autoriza** el uso (quién y cuándo en `chat_imports` y en la bitácora). Los nombres de quien escribe se vuelven
   «cliente» y «vendedor» al leer; los datos dentro de los mensajes pasan por reglas (correo, teléfono, DNI, RUC, tarjeta, enlace,
   dirección con número, «me llamo …») y luego por una pasada del modelo para nombres y direcciones sin número; **una conversación que
   no vuelve limpia de esa pasada se descarta**, no se guarda a medias. Solo se analizan 14 conversaciones elegidas por
   utilidad. Salen: el estilo (solo de mensajes escritos), hasta 8 ejemplos reales y hasta 5 reglas sobre objeciones (aquí cuentan
   los audios). *No probado aún con un chat real de un negocio.*
4. **Audios.** Se transcriben solo los de las conversaciones elegidas, los más nuevos primero, con tope de **30 minutos por
   importación** (unos US$ 0,18 con `whisper-1` a US$ 0,006 por minuto; precio de referencia, a confirmar en la factura de OpenAI) y se
   descarta cualquiera de más de 2 MB. Si una falla se omite. *El adaptador sigue la documentación pública y no se ha probado con una
   nota de voz real.*
5. **Ejemplos reales** (`example_conversations`): anonimizados, con embedding de «de qué trata»; en cada turno el asistente busca el más
   parecido (si está cerca, distancia < 0,45) y lo ve con la orden de guiarse por su enfoque **sin copiar datos, precios ni promesas**.
   Un negocio sin ejemplos no paga nada (ni consulta ni embedding). Se aprueban en lote, como pidió el brief.
6. **Estilo del vendedor** (`agent_configs.estilo_vendedor`, 600 letras): una línea más en «ESTILO» del prompt, solo si se aprobó.
7. **Fuentes.** *Una persona tomó la conversación*: una llamada pregunta qué regla general habría seguido el asistente; se guarda la
   regla, no la conversación. *Venta confirmada*: hasta 3 por corrida, anonimizadas, como ejemplos. *Venta perdida*: los lunes, una
   llamada sobre los pedidos descartados sin confirmar (hace falta al menos 2). *Pregunta sin respuesta*: una pregunta hecha 2 veces
   sin que el conocimiento la responda. Cada conversación se analiza una vez (`learning_marks`). Todo corre en una pasada diaria
   (`learning_sweep`, 10:35 UTC).
8. **El rubro aprende (fase 4).** Solo con el permiso del negocio (`compartir_con_rubro`, **apagado por defecto**, que el dueño
   enciende sabiendo que se comparte el texto de sus situaciones aprobadas, nunca chats ni datos de clientes). El operador ve, por
   rubro, las situaciones que **al menos dos negocios** escribieron con el mismo sentido (distancia < 0,2), la lee y la **ofrece**: pasa al
   borrador de la siguiente versión del rubro como situación sugerida y **sigue pasando por los evals** antes de publicarse. Una tarjeta
   en la que las reglas aún encuentran datos personales queda marcada y no se puede ofrecer.
9. **Redactor asistido.** El dueño escribe la regla con sus palabras; una llamada la convierte en tarjeta y la compara con sus otras
   situaciones y con las reglas de su rubro y de su plantilla; además se busca por significado una situación que diga lo mismo. Avisa,
   no guarda; descuenta del mismo tope.
**Fuera, por decisión:** reentrenar el modelo, aprendizaje sin aprobación, transcribir mensajes en vivo, volver a pasar conversaciones
antiguas por la configuración nueva (así lo dice el brief).

