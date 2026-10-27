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


## C-11: cotizar bien (una conversación real que los evals daban por buena)
**Origen (Giannier, 2026-10-26):** seis capturas del simulador de «Nayu» (Bebidas yanayacu, agua en bidones) y un 👎: repetía «Sí,
contamos con…» en cada mensaje, volvía a preguntar lo que el cliente ya había dicho («ya te dije que 20 semanales»), cerraba una vez
con tres datos y otra con dos, entregaba a una persona con un «ya le pasé lo que necesitas» sin decir qué, y justificaba con una frase
de venta que nada respaldaba. Los evals estaban en verde.
**Causas (comprobadas con el modelo real, no supuestas):**
1. *Tres jefes distintos.* El manual de venta («recomienda UNA opción y por qué»), el texto de la etapa de *servicios* («pide nombre y
   correo y deriva») y la pista dentro de `buscar_productos` («antes de derivar pide lo que falte») se contradecían; el modelo
   obedecía a uno distinto cada vez.
2. *Sin memoria de la cotización.* Lo único que había era el historial: el modelo lo releía y adivinaba qué faltaba, y a veces
   anotaba «20 semanales» como la fecha o «para negocio» como el producto.
3. *Nadie decidía cuándo cerrar.* Derivaba «por ánimo»; el mensaje de entrega era genérico y no devolvía al cliente lo anotado.
4. *Los evals miraban otra cosa.* Revisaban la etapa final y qué herramientas se usaron, no leían la conversación como la lee un
   cliente (repeticiones, preguntas repetidas, afirmaciones sin respaldo). Un flujo podía terminar en la etapa correcta con una
   conversación mala.
**Qué se hizo:**
- `anotar_cotizacion` (capacidad `cotizar`): un formulario con un campo por cada dato que el negocio pide (`products.datos_a_pedir`);
  lo anotado queda en el hecho `cotizacion` y el bloque ESTADO le muestra al modelo qué ya dio y qué falta. La herramienta responde
  qué dato preguntar ahora; con todo completo **pasa sola a una persona** y le devuelve al cliente lo anotado («Anoté: …»).
- Una fecha y una cantidad tienen que parecerlo (no se anota «para negocio» como fecha); «sin dato» solo vale para el dato que se
  acaba de preguntar y si el cliente mismo dijo que no sabe o no quiere darlo; `derivar_a_humano` no deja cerrar una cotización
  incompleta (salvo que el cliente se niegue).
- Si el modelo escribe «ya anoté» sin llamar a la herramienta, se le avisa una vez (`UNBACKED_CLAIMS` en `agent/core.py`).
- Un solo conjunto de instrucciones: bloque COTIZACIÓN en el prompt (~260 tokens), texto de *servicios* corregido como **versión 4**
  de las tres plantillas (migración 0067; las versiones publicadas no se tocan y las reglas conservan sus claves), y la pista de
  `buscar_productos` cambia cuando la herramienta existe. Un negocio con `cotizar` y `captar` también califica el lead al cotizar.
- **Un eval honesto** (`evals/real_conversations.py`): 9 conversaciones copiadas de las capturas (escritas por el dueño) y 9 distintas
  que se escribieron *después* del arreglo para no ajustarlo a ellas. Mide repeticiones, preguntas repetidas, datos ya dados, cierres
  inconsistentes, afirmaciones sin respaldo y una frase de venta inventada; con `JUDGE=1` un segundo modelo la lee como cliente.
  Se pide 85 % (medido en más de 20 corridas: casi siempre 16 a 18 de 18; una dio 13); corre dentro de `make evals`.
**Costo del modelo (informativo):** un negocio con `cotizar` paga ~260 tokens más en el prompt de cada turno donde vende y entre 100 y
170 tokens por la herramienta; los demás negocios no pagan nada. La plantilla y el manual de venta cambiaron unas palabras para todos.
**Los datos reales de Nayu (leídos en producción el 2026-10-26 con permiso de Giannier):** los productos a cotizar no tienen datos
cortos sino **preguntas completas, distintas por producto** («¿Cuántas botellas de Agua Alcalina de 625 ml necesitas?», «¿Para qué
fecha necesitas el pedido?», «¿Es para tu hogar, oficina o negocio?»…), de 4 a 5 por producto, y el negocio tiene las capacidades
atender, vender, cotizar y captar. En el panel se llaman «preguntas» (una por línea) y los rubros sugieren frases libres. La primera
versión (ya desplegada) suponía nombres cortos; con estos datos la validación de fecha y cantidad nunca se activaba. Se rehízo así:
- Cada entrada se traduce a un **nombre corto** (`process/quote_data.py`: cantidad, fecha, distrito o dirección, uso, frecuencia,
  personas, presupuesto; si no es ninguno, la frase misma limpia) y se conserva la **pregunta del dueño**, que la herramienta
  devuelve para preguntar con sus palabras cuando es la misma en todos los productos. La herramienta, el hecho `cotizacion` y el aviso
  final hablan en nombres cortos.
- El producto se elige por lo que dice el cliente o el modelo (palabras que distinguen un producto de otro); cada producto pide solo
  lo suyo (los envases de 625 ml no preguntan la frecuencia).
- Un dato libre (uso, frecuencia…) solo se anota si sale de lo que escribió el cliente: el modelo inventaba «frecuencia: recurrente»
  para cerrar antes. El dato recién preguntado se acepta con las palabras del modelo.
- Si lo preguntado antes sigue sin respuesta, la herramienta pide **otro** dato y vuelve a él después (la misma pregunta cuatro veces
  seguidas mientras el cliente contestaba otras cosas); «todavía no sé la fecha» cuenta como respuesta y se anota como «sin dato».
- El eval incluye ahora **8 conversaciones sobre el catálogo exacto de Nayu** (la del dueño del 23 de octubre y 7 más): 22 de 24 en
  tres pasadas. La del cliente que contesta «para mi hogar» justo cuando se le pregunta el distrito sigue fallando a veces.
**Límites que quedan (primera versión, 2026-10-26 por la mañana):** no es 100 %; el modelo podía poner un dato en el campo
equivocado; y el último mensaje del cliente puede aparecer dos veces en el historial que ve el modelo (no corregido).

### C-11, actualización (2026-10-26, tarde): con los datos reales de Nayu
Giannier dio permiso total para leer la configuración (solo lectura; sin credenciales ni datos de clientes). Lo que se encontró cambió el
diseño:
1. **`datos_a_pedir` son preguntas, no nombres.** El panel las llama «preguntas» (una por línea) y los productos de Nayu tienen cinco,
   distintas por producto («¿Cuántas botellas de Agua Alcalina de 625 ml necesitas?», «¿Es para tu hogar, oficina o negocio?»). La
   primera versión suponía nombres cortos («cantidad», «fecha»): con los datos reales las validaciones nunca se activaban y los campos
   de la herramienta salían con nombres de 40 letras. Ahora cada entrada recibe un **nombre corto** (`process/quote_data.py`: cantidad,
   fecha, distrito o dirección, uso, frecuencia, personas, presupuesto; o la propia frase limpia) y se conserva la **redacción del dueño**,
   que se muestra junto a lo que falta cuando es la misma en todos los productos.
2. **Un modelo más grande no arregla la rigidez.** Prueba con gpt-5.4 contra gpt-5.4-mini (mismo código, 3 conversaciones de Nayu, 3
   pasadas cada uno, unos US$ 1,23 en total con precios de terceros): ambos contestaban como formulario («Anotado: *8 cajas*. ¿Para qué
   fecha…?»; 11 de 17 respuestas del mini y 8 de 17 del 5.4 con forma de recibo). La causa eran mis instrucciones («pregunta SOLO por X en
   una frase», rotar la pregunta). **No se cambia el modelo** (sigue gpt-5.4-mini, decisión de costo de Giannier).
3. **Rediseño para que use criterio.** La herramienta ya no dicta la siguiente pregunta: dice qué se sabe y qué falta (con la redacción
   del dueño) y pide contestar primero lo que el cliente dijo o preguntó, aclarar una respuesta vaga («mi hogar» a una dirección →
   «¿en qué distrito queda?»), juntar dos preguntas que van juntas y no recitarle lo que acaba de decir. Reglas duras en código, pocas:
   no inventar datos (cada dato debe salir de lo que el cliente escribió, salvo el que se acaba de preguntar), un uso dicho para la
   dirección se anota como uso, «sin dato» solo si el cliente lo rechaza (uno por llamada; si es lo único que falta, se cierra solo),
   no se deriva una cotización sin ningún dato, una respuesta del modelo sin pregunta mientras falta algo recibe un aviso, y lo mismo
   si da una «razón de venta» que nadie respalda (`el más usado…`) o si no anotó lo que el cliente acaba de decir.
4. **Medición** (catálogo exacto de Nayu, 8 conversaciones): respuestas con forma de recibo de 65 % a 0–4 %; 8/8 y 7/8 en las dos
   últimas pasadas; regresión completa: 24/26 conversaciones (92 %, se pide 85 %), calidad de venta 88 %, flujos de venta 39/40
   (`certificaciones` falla de forma intermitente, con casos distintos, también con el código anterior).
5. **Costo (informativo):** el bloque COTIZACIÓN pasó de ~260 a ~360 tokens por turno de venta; la herramienta, ~120. Unas centésimas de
   centavo por turno con gpt-5.4-mini. Los avisos del núcleo añaden una llamada al modelo solo en los turnos donde el modelo se saltó
   algo.
6. **Pendiente, decisión de Giannier:** llave de OpenAI por negocio. El código ya soporta una llave propia por negocio
   (`tenant_llm_settings`, solo chat) pero no hay pantalla para cargarla, y la tabla de precios `model_costs` está vacía en producción
   (ninguna respuesta tiene costo en dólares). Ver la conversación del 2026-10-26: opciones 1 (una llave y medir en Leo), 2 (un proyecto de
   OpenAI por negocio) y 3 (llave del cliente).

### C-11, memoria completa (2026-10-26, noche)
**Pedido de Giannier:** «el agente tenga memoria completa de toda la conversación, para que sepa bien de lo que habla el cliente», y
corregir el mensaje duplicado y la variación, **sin correr evals** con el modelo real (por el gasto). Se verificó con pruebas
automáticas (sin costo de API); la regla del CLAUDE.md de adjuntar evals a un cambio de prompt queda pendiente para cuando Giannier
lo autorice.
- **El mensaje del turno iba dos veces.** El canal guarda el mensaje del cliente antes de llamar al modelo; el historial lo incluía y
  además se mandaba como mensaje a responder. Ahora el turno lleva los ids de sus mensajes (`InboundTurn.message_ids`) y el historial los
  excluye; un llamador que no los da recibe el último descartado si es el mismo texto. Lo que el cliente escribió mientras el asistente
  estaba en pausa sigue en el historial.
- **Toda la conversación.** Antes eran los últimos 20 mensajes. Ahora va completa hasta ~12 000 tokens (cientos de mensajes de
  WhatsApp); solo un hilo más largo se corta, y entonces lo que el cliente dijo antes del corte va como resumen literal (sin llamar al
  modelo, sin inventar). Imágenes, audios y documentos se leen como «[El cliente envió una imagen]».
- **Regla 10 de plataforma:** «Tienes toda la conversación: úsala para entender qué necesita; no preguntes lo que ya dijo, aunque fuera
  hace mucho». Para caber en el presupuesto (6 000 tokens) se compactaron las reglas 1, 5, 6, 7 y 9 sin cambiar su sentido.
- **Variación:** las respuestas del agente van con temperatura 0 (antes 0,2): la misma conversación se contesta igual de una vez a
  otra. No elimina toda la variación del modelo.
- **«¿Bidón o caja?» cuando dijo «bidón»:** `buscar_productos` le dice al modelo qué producto nombró el cliente cuando nombró uno solo
  (cuentan solo las palabras que distinguen los productos).
- **Costo (informativo):** una conversación típica (10–60 mensajes) manda 1 000–3 000 tokens de historial por turno; el tope, ~13 500.
  Con gpt-5.4-mini (US$ 0,75 por millón de entrada, precio de terceros) el peor caso es ~US$ 0,01 por turno.

## C-12: auditoría de producción y de coherencia del agente (2026-10-26)
**Pedido de Giannier:** auditoría exhaustiva, «cuando realicé un cambio a mi producto vi que no se actualizó en las respuestas
del agente», y el agente al 100 % en coherencia, criterio y humanidad. Lectura en producción solo de configuración y metadatos.
**Por qué un cambio de producto no llegaba al agente (tres causas, corregidas):**
1. La búsqueda mostraba solo los primeros **200 caracteres** de cada descripción, cortados a media palabra. Las de Nayu tienen
   359–486: la segunda mitad (donde el dueño escribió cómo se vende: «dime cuántas unidades necesitas y dónde deseas recibirlas»)
   nunca llegó al modelo. Ahora las descripciones de un listado se reparten 2 400 caracteres (cada una entre 200 y 700), cortadas en
   frase o palabra, y van rotuladas como dato del producto.
2. La configuración del proceso se guarda en caché **por proceso** (producción corre tres: dos web y el worker) y editar un producto
   no la limpiaba. Ahora crear, editar, borrar, duplicar o importar productos la limpia, y el caché dura 10 s (antes 60).
3. En una conversación en curso el modelo repetía lo que dijo antes (no ve los resultados viejos de las herramientas, solo sus
   mensajes). Ahora, si un producto cambió después del último mensaje del asistente, el turno lo dice y pide volver a buscar.
   Además los cambios de productos quedan en `audit_logs` (campo, antes y después): antes no había forma de saber qué cambió.
**Contradicciones del prompt (corregidas):** VENTA y PROCESO decían «recomienda UNA opción y por qué» y COTIZACIÓN «no recomiendes»;
el RUBRO b2b dice «si no hay precio, deriva» (por eso a veces derivaba sin datos): ahora COTIZACIÓN dice que manda sobre VENTA,
PROCESO y RUBRO para lo «a cotizar». CAPTACIÓN pedía cantidad y frecuencia, que ya pide la cotización: con cotización no hace
preguntas aparte. ESTILO decía el tratamiento dos veces y dos reglas de emojis distintas (el texto del tono traía las suyas): el tono
ya no las trae. La descripción del negocio se cortaba a media palabra. La regla de memoria quedó como 11 (había dos «10.»).
**Operación:** contenedores sanos, 0 errores en logs, 151 trabajos sin fallas, disco al 26 %. **No había backup automático de Leo**
(el cron no existía, el script hacía `source` de un archivo que no es bash y la carpeta por defecto no existía): el script quedó
corregido y probado; falta instalar la línea del cron (RUNBOOK sección 6), cambio en el servidor que confirma Giannier.
**Para el dueño (configuración, no código):** en Nayu, «Mayorista: ofrécele precio al por mayor» choca con que todo es a cotizar
(mejor: «el precio por mayor lo cotiza una persona»); y las descripciones traen saludos («¡Hola! Soy Nayu…») que conviene quitar.
**Sin evals con el modelo real** en este cambio (por el costo, a pedido de Giannier); verificado con la suite automática.

## C-13: gpt-4.1-mini para todo, y el prompt escrito para un modelo literal (2026-10-27)
**Decisión de Giannier (costo del LLM):** «usaremos gpt 4.1 mini, para todo». Pasa a ser el modelo por defecto
(`openai_chat_model`, `deploy/env.reference`, `deploy/env.prod.example`); en producción manda la línea `OPENAI_CHAT_MODEL` de
`deploy/.env.prod`, que cambia Giannier o el despliegue con su visto bueno. Precio de lista: US$0,40 / 1,60 por millón de tokens de
entrada / salida, contra 0,75 / 4,50 de gpt-5.4-mini. En la comparación del mismo día gpt-4.1-mini fue más flojo en pedidos
(certificaciones), por eso se revisó el prompt y el proceso para él antes de cambiar.
**Cómo lee gpt-4.1-mini (guía de prompting de OpenAI para GPT-4.1):** sigue las instrucciones al pie de la letra; si dos chocan,
gana la que está más abajo; copia tal cual las frases de ejemplo; conviene repetir lo crítico al inicio y al final, y pedirle una
herramienta a la vez. Lo que se vio en los evals: recorría «Paso 1, Paso 2, Paso 3» con un cliente que ya había dicho qué paquete
quería y no agregaba nada; pedía permiso («¿quieres que te cuente…?») en vez de hacerlo; pedía el DNI antes de que hubiera pedido
(el ESTADO decía «faltan DNI»); copiaba «¡Buenísimo! Ya tienes [X]…» palabra por palabra; y tras la lista de paquetes escribía «ya
puedes elegir el que prefieras», un segundo mensaje que decía lo mismo.
**Cambios:**
1. Orden del prompt: lo general arriba y un bloque final corto «ANTES DE RESPONDER» (datos solo de herramientas; si el cliente pide
   algo que una herramienta hace, hacerlo en el turno sin pedir permiso; no repetir lo que una herramienta ya envió; responder y
   terminar con el siguiente paso). Las reglas numeradas de la plataforma siguen arriba y «nunca se rompen».
2. VENTA sin frases de ejemplo (el modelo las copiaba); «no pidas permiso para contarle o enviarle algo: hazlo. Al pedido, solo lo
   que pidió» (un modelo literal agregaría lo que solo se recomendó); señal de compra = agregar al pedido en ese mismo turno. Ya no
   pide «nombre y cómo contactarlo» por su cuenta.
3. ESTADO: las líneas de datos del cliente y métodos de pago solo aparecen si hay pedido o la etapa es de pedido. La línea de pago
   del negocio dice que los datos para pagar se envían aparte (no los escribe el modelo).
4. Avisos del sistema (uno por turno, como los demás): el cliente dijo «lo quiero / agrégalo» y no se llamó a `gestionar_pedido`
   (solo si aún no hay nada en el pedido: con pedido, «lo quiero para mañana» habla del pedido hecho y agregarlo otra vez lo
   duplicaría); preguntó cómo pagar y no se llamó a `enviar_metodos_pago` (solo si no se enviaron ya: el proceso dice «revisa el
   mensaje anterior»); el mensaje pide permiso para contar o enviar algo («¿te lo agrego?» no cuenta: es la pregunta de cierre).
5. Plantilla certificaciones v5 (migración 0068): si el cliente ya dijo qué certificación o paquete quiere, se agrega ya; el precio
   por unidad solo si aún no eligió; en PEDIDO, «cuántas tiene y cuántas le faltan» con sus palabras.
6. Mensajes duplicados: el texto del modelo se descarta si repite lo que una herramienta acaba de enviar (70 % de las palabras
   significativas, no solo el texto idéntico) o si, tras un menú, solo invita a elegir otra vez. `mostrar_opciones` envía un menú
   por turno (antes podía mandar dos con textos distintos). Los archivos y los métodos de pago ya se enviaban una sola vez.
7. `parallel_tool_calls=false` en OpenAI: una herramienta por paso, en orden (lo recomienda la guía; evita dos envíos a la vez).
8. Descripciones de `gestionar_pedido` («úsala en cuanto quiera algo de precio fijo, sin pedirle datos antes») y
   `enviar_metodos_pago` («no los escribas tú»).
**Validación con casos nuevos (2026-10-27, autorizada por Giannier: «genera nuevos ejemplos, altera las conversaciones reales de
Nayu»).** Casos escritos ese día y nunca usados para ajustar: 19 flujos (`SALES_FLOWS_SET=fresh`), 3 de calidad
(`SALES_QUALITY_SET=fresh`) y 12 conversaciones de Nayu alteradas (`REAL_SET=fresh`: jerga, dos productos, corrección, lugar vago,
pide una persona, solo mira). Todos los evals revisan además que no salgan dos mensajes iguales ni dos menús en un turno.
Resultado con gpt-4.1-mini: flujos 13/19 (productos 5/7, certificaciones 3/6, servicios 5/6), calidad 88 %, Nayu 9/12; **ningún
mensaje duplicado**. ~690 mil tokens de entrada y ~11 mil de salida: ~US$0,29 a precio de lista. Lo que se vio y se corrigió:
1. **Agregaba al pedido lo que nadie pidió** («¿cuánto cuesta la mochila?» y «pierdo tiempo pasando pedidos a Excel» terminaron en
   el pedido), efecto de «no pidas permiso» + «agrégalo»: ahora solo se agrega lo que el cliente dijo que quiere, y un «sí» a
   «¿te lo agrego?» cuenta como quererlo (aviso del sistema).
2. **«Pásame con alguien del equipo» no derivaba**: la herramienta rechazaba la derivación por faltar datos de la cotización, y la
   regla de VENTA «no derives por el precio» chocaba con la regla 5. Ahora pedir una persona deriva con lo anotado.
3. **Pregunta sin respuesta en el negocio → derivaba de inmediato** (reglas 5 y 6 se contradecían): ahora dice que lo confirma una
   persona y sigue; si no hay pedido ni cotización, ofrece pasarlo.
4. **Promesas sin hacer**: «te envío los datos para el pago» sin enviarlos, «voy a buscar… te las muestro enseguida», y el texto de
   respaldo del control de nombres prometía «te comunico con una persona» sin derivar. Ahora hay aviso del sistema y el respaldo
   ya no promete.
5. **Etapas sin la herramienta que su regla pide**: CALIFICAR debía responder un precio sin poder buscar; DATOS y COBRO no podían
   cambiar de producto. Ahora tienen `buscar_productos` (tabla de `plantillas.md` actualizada).
6. **«solo esa vez» no se reconocía como frecuencia** y la cotización de dos productos no se cerraba.
7. **Cotización como formulario**: en el primer mensaje pedía 3 o 4 datos juntos; ahora un aviso pide una o dos a la vez.
Queda visto y sin cambiar: a veces anota un dato un turno después de recibirlo; el resumen final («Anoté: …; cantidad: …») se lee
seco; el juez (el mismo modelo) marcó 8 de 12 de Nayu como poco naturales, pero sus fallas no siempre coinciden con la transcripción.
**Segunda pasada (autorizada, ~US$0,47):** lote 1 repetido tras las correcciones: flujos 14/19, Nayu 8/12, calidad 81 % (solo
productos); lote 2 nuevo (`*_SET=fresh2`: 12 flujos, 7 de Nayu, 2 de calidad): flujos 9/12, Nayu 4/7, calidad 90 %. Ningún
mensaje duplicado. Las primeras correcciones casi no movieron los números; leer las transcripciones mostró causas más hondas,
corregidas después (sin medir aún):
1. El bloque final «ANTES DE RESPONDER» decía «si el cliente pide algo que una herramienta hace (agregar al pedido…), hazlo»: para
   un modelo literal, preguntar el precio de la mochila es «pedirla». Ahora: «al pedido solo va lo que dijo que quiere».
2. `gestionar_pedido` rechazaba el nombre del producto en `producto_id` (el modelo copia mal los ids) y la respuesta decía igual
   «te agrego…» o «voy a agregar, un momento». Ahora un nombre que nombra un solo producto es ese producto; decir que se agregó sin
   que la herramienta lo hiciera, o prometer («voy a derivarte», «te envío la información», «un momento, por favor»), recibe aviso.
3. «me llevo un polo» y «quiero contratar…» no contaban como quererlo.
4. CALIFICAR (certificaciones) no podía agregar: «la quiero» terminaba pidiendo el DNI. Ahora tiene las herramientas de IDENTIFICAR
   (un pedido manda sobre el camino en el cálculo de la etapa).
5. Una duda que el negocio no respondió («¿distribuyen a Huacho?», «¿es tratada con ozono?») seguía derivando de inmediato aunque la
   regla 6 dice seguir. En un negocio que cotiza, con la cotización abierta, la derivación por esa duda ahora se rechaza («se lo
   confirma una persona con la cotización»), salvo que pida una persona o sea el camino de asesoría.
Siguen vistos sin corregir: inventó cómo es el proceso de certificar («un examen y documentos»: no está en ningún dato); anota a
veces un dato un turno tarde («reunión de mi empresa» no quedó como uso); la plantilla de certificaciones pide el nombre y el
camino antes de todo (diseño del dueño). Gasto de evals del día: ~US$1,62.
**Tercera pasada (lote 3, nuevo, autorizada, ~US$0,24):** flujos 10/14, Nayu 6/10, calidad 93 % (con un monto inventado). Las
tres mediciones honestas (cada lote corrido por primera vez) no suben: flujos 68 % → 75 % → 71 %, Nayu 75 % → 57 % → 60 %,
calidad 88 % → 90 % → 93 %. Cada corrección resuelve un tipo de falla y aparece otro: tras pedir consentimiento para agregar,
ahora pregunta «¿quieres que te lo agregue?» a quien dijo «quiero certificar Excel y Power BI»; dijo «Sí, emitimos factura» sin
que el negocio lo diga; anota «40 al mes» como cantidad y no la frecuencia, y la verificación rechaza «mensual» por no ser las
palabras del cliente, así que vuelve a preguntar y la cotización no se cierra. Lectura: con este proceso (varias herramientas,
reglas literales), gpt-4.1-mini queda en ~70 % de flujos y ~60 % de cotizaciones; los controles en código sirven con cualquier
modelo. Producción sigue con gpt-5.4-mini (`.env.prod`); el cambio de modelo no se despliega solo con el código.
**Comparación con el mismo lote 3 (autorizada, ~US$0,55):**

| | gpt-4.1-mini | gpt-5.4-mini | meta |
|---|---|---|---|
| Flujos | 10/14 (71 %) | 12/14 (86 %) | 90 % |
| Nayu | 6/10 (60 %) | 8/10 (80 %) | 85 % |
| Calidad | 93 % (un monto inventado) | 83 % | 75 % |
| Juez: conversación razonable | 3/10 | 7/10 | — |
| Costo por conversación de Nayu (lista) | ~US$0,010 | ~US$0,028 | — |

Las dos fallas de Nayu de gpt-5.4-mini (y una de gpt-4.1-mini) eran nuestras: «40 al mes» anotado como «mensual» se rechazaba
por no ser las palabras del cliente; corregido (3b1baad). gpt-5.4-mini dijo «ya quedó registrada» sin agregar: ahora recibe aviso.
Gasto de evals del día: ~US$2,41. **Pendiente (decisión de Giannier, costo del LLM):** qué modelo atiende la conversación;
producción sigue con gpt-5.4-mini. Propuesta aparte para bajar el costo: caché de prompt (ESTADO al final, medir `cached_tokens`).

## C-14: capacidad para 20 negocios y origen de los clientes (2026-10-27)
**Pregunta de Giannier:** con la misma API de OpenAI para las 20 empresas, ¿habrá errores o cruce de información?; ¿se distingue el
cliente que llega por publicidad pagada del orgánico?
**Cruce de información: no.** Cada llamada al modelo lleva solo el prompt de ese negocio y el historial de esa conversación; OpenAI no
guarda estado entre llamadas y la caché de prompt solo reutiliza un prefijo idéntico (no se comparte entre organizaciones). La suite
de aislamiento (639 pruebas) pasó ese día.
**Capacidad (corregido):** (1) un 429 o un 5xx de OpenAI no se reintentaba: el cliente recibía el mensaje de respaldo y tres fallas en
un minuto abrían el circuito del negocio por 2 minutos. Ahora se reintenta dos veces (espera de OpenAI, máximo 8 s); sin saldo no se
reintenta. (2) Un solo worker con 4 tareas a la vez, compartidas con la indexación de documentos: con 20 negocios los mensajes harían
cola. Ahora 20 a la vez, las pesadas de 2 en 2, y el pool de conexiones del worker es 20 + 10 (RUNBOOK §7).
**Origen:** el anuncio Click-to-WhatsApp de Meta llega en el webhook (`referral`) y queda como `entrada_anuncio` (etapa ANUNCIO; en el
CRM «Anuncio: <titular>»); lo orgánico sale del enlace `?o=` o del texto «te escribo desde Instagram»; sin dato, «Directo». El reporte
de resultados anteponía «Anuncio:» a todo origen (un cliente orgánico de Instagram contaba como pagado): corregido. Aún no visto con un
anuncio real; un anuncio que lleva a la web y de ahí a WhatsApp necesita el enlace con `?o=`.
**Plantillas y rubros:** 3 plantillas de proceso (cómo avanza la venta) y 13 rubros (lo que se sabe de cada industria), entre ellos
«otros», el genérico por defecto. Recomendación: no una plantilla por rubro (cada una pide sus evals con el modelo real); enriquecer los
rubros (hoy ~4 reglas cada uno) y crear una plantilla solo donde la venta fluye distinto. Pendiente: los rubros de las 20 empresas.

## C-15: gpt-5.4-mini para la conversación y el prompt ordenado para la caché (2026-10-27)
**Decisión de Giannier (costo del LLM):** «dale» a la recomendación tras la comparación de C-13: gpt-5.4-mini atiende la conversación
(vuelve a ser el modelo por defecto en el código; producción ya lo usaba) y se baja el costo con la caché de prompt de OpenAI.
**Por qué la caché casi no servía:** OpenAI cobra a una fracción (90 % menos en gpt-5.4-mini, 75 % en gpt-4.1-mini) la parte inicial del
prompt que es idéntica a una llamada anterior. El bloque ESTADO (con la hora) estaba en medio del prompt del sistema: todo lo que venía
después, incluido el historial de la conversación, nunca se repetía igual.
**Cambios:** (1) el prompt se parte en dos (`StagePrompt.head` y `.tail`): lo que no cambia de un turno a otro va antes de la
conversación; las situaciones y ejemplos del mensaje, el ESTADO y «ANTES DE RESPONDER» van después del historial, justo antes del mensaje
del cliente. El orden de las secciones que lee el modelo es el mismo; solo el historial queda antes de la parte final. (2) Cada llamada
lleva `prompt_cache_key` = la conversación (solo OpenAI y la clave propia de OpenAI de un negocio; otros endpoints podrían rechazarlo).
(3) Se registran los tokens leídos de la caché (`agent_traces.cached_input_tokens`, migración 0069) y los reportes de evals muestran el
costo estimado con precios de lista.
