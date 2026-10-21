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
