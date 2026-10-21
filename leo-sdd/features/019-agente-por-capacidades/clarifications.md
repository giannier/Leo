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
