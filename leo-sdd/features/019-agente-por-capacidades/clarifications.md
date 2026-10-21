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
