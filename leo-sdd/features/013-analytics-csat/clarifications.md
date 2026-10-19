# Clarificaciones — 013 Analytics avanzado y CSAT

## C-101: Ratificación (Giannier, 2026-10-19)
"Continúa". Antes de construir se consultó la única decisión de dinero: la encuesta de satisfacción fuera
de las 24 h solo puede ir como plantilla (Meta la cobra). Giannier eligió **solo dentro de las 24 h**, como
texto fijo, y pidió revisar las reglas nuevas de Meta (resumen en `leo-sdd/meta-reglas-2026-10.md`; cambios
aplicados en la 011, C-111).
**Registrado por:** Claude — ratificado en el chat

## C-102: La encuesta (CSAT)
- Apagada por defecto; el negocio la enciende en Resultados → Satisfacción.
- Se dispara cuando una conversación se **gana** (pedido confirmado) o cuando una persona la **cierra** en la
  bandeja, pasada **1 hora** (para no juntarse con la confirmación de pago y la pregunta de promociones).
- Solo si la ventana de 24 h sigue abierta (con 30 min de margen), el cliente no escribió BAJA, la
  conversación no está tomada por una persona y no se le preguntó en los últimos **30 días**.
- Texto fijo, sin IA: "¿Qué tal te atendimos? Responde con un número del 1 al 5 (5 = excelente)." Desde
  octubre de 2026 cuenta como un mensaje de servicio de WhatsApp (la pantalla lo dice).
- Sin respuesta en **48 h**: vence en silencio.
**Registrado por:** Claude — informativa

## C-103: La respuesta
- Se lee sin IA: "5", "cinco", "⭐⭐⭐⭐⭐", "5 muy bien", "excelente" (=5), "malo" (=2)…
- Con nota: se guarda, se agradece con un texto fijo y el asistente **no** se llama, salvo que el mensaje
  traiga una pregunta (tiene "?" o más de 12 palabras): entonces también pasa al asistente (C-002 de la spec).
- Sin nota clara: la encuesta se cierra con el texto como comentario, sin puntaje, y el mensaje sigue al
  asistente normalmente (C-001: no se insiste).
**Registrado por:** Claude — informativa

## C-104: Analítica en vivo
Embudo, productos y satisfacción se calculan al pedirlos desde conversaciones, leads, pedidos y encuestas
(como el CRM, 012 C-108), sin columnas nuevas en `metrics_daily`.
**Registrado por:** Claude — informativa

## C-105: Satisfacción por persona
El promedio por persona del equipo (vs el asistente) solo lo ven dueño y administradores, y solo se muestra
con 5 respuestas o más (C-003 de la spec). La encuesta se atribuye a la persona que escribió último en esa
conversación; si nadie escribió, al asistente.
**Registrado por:** Claude — por ratificar (dato de desempeño de empleados)

## C-106: A/B de campañas
En vez de dos campañas enlazadas (`variant_of`), una campaña lleva una **plantilla B opcional**; cada
destinatario cae en A o B de forma estable (por su id) y el reporte compara ambas, con aviso si alguna tiene
menos de 30 personas.
**Registrado por:** Claude — informativa

## C-107: NPS hacia Leo
Al dueño y administradores, a los 30 días del negocio y luego cada 90, una tarjeta en Inicio (0 a 10 y
comentario), descartable hasta el siguiente ciclo. Los resultados solo los ve el operador de plataforma.
**Registrado por:** Claude — informativa

## C-108: Reporte mensual
El día 1, al dueño, por correo: conversaciones, ventas, satisfacción, más vendidos y gasto estimado en
WhatsApp, con enlace al panel. Encendido por defecto, se apaga en Resultados. Un envío por mes (idempotente).
**Registrado por:** Claude — informativa
