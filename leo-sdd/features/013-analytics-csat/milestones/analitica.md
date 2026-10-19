# Milestone 013: Analítica avanzada y satisfacción (TASK-1301..1306)

Fecha: 2026-10-19. Claude lo verificó con pruebas automáticas. Nada de la 013 usa el modelo: la encuesta
es un texto fijo y su respuesta se lee sin IA. Giannier lo prueba con el número real.

## Qué hay

- **Resultados**, con cuatro vistas:
  - **Resumen:** lo de la 010, más el gasto estimado de WhatsApp del mes.
  - **Embudo:** conversaciones → interesados → llegaron a pagar → compraron, con tasas, comparación
    con el período anterior, de dónde llegan (campaña, anuncio, directo) y los días con más
    conversaciones.
  - **Productos:**
    - Lo más vendido y el ticket promedio.
    - Lo que preguntan pero no compran.
    - Agotados que piden.
  - **Satisfacción:** promedio, notas, comentarios, el asistente frente a cada persona del equipo
    (solo dueño y administradores, con 5 respuestas o más) y los interruptores de la encuesta y del
    reporte mensual.
- **Encuesta de satisfacción** (apagada por defecto):
  - Cuándo se envía: una hora después de una venta o de cerrar una conversación, solo dentro de las
    24 h, como máximo una vez al mes por cliente y nunca a quien escribió BAJA.
  - Cómo se lee la respuesta: "5", "cinco", estrellas, "excelente"…
  - Si el cliente además pregunta algo, el asistente también contesta.
  - Si nadie responde en 48 h, vence.
- **Reporte mensual por correo:** el día 1, al dueño, una sola vez por mes. Se puede ver antes desde
  Satisfacción.
- **NPS de Leo:** tarjeta en Inicio a los 30 días y luego cada 90; los resultados están en
  Plataforma → NPS.
- **A/B de campañas:** una segunda plantilla opcional. Cada persona cae en A o B de forma estable, y
  el reporte compara las dos con un aviso si hay menos de 30 por lado.
- **Reglas de Meta de octubre de 2026** (`leo-sdd/meta-reglas-2026-10.md`):
  - Tarifas con la categoría "servicio".
  - Gasto estimado del mes en Resultados.
  - El tope de marketing de Meta (error 131049) aparece en el reporte de la campaña.

## Evidencia

- **Backend:** suite completa en verde (3,707). Las pruebas de la 013 son:
  - `unit/test_csat_reading.py`
  - `integration/test_csat.py`
  - `integration/test_analytics.py`
  - `isolation/test_analytics_isolation.py`
- **e2e:** `frontend/e2e/analitica.spec.ts` pasa en escritorio y Android. También pasan Resultados,
  Campañas, navegación, plataforma y onboarding.
- **Capturas:** `analitica-embudo-*`, `analitica-satisfaccion-*` y `analitica-nps-*`.

## Decisiones

**Bloqueantes:** ninguna.

**Para ratificar:**
- C-105: satisfacción por persona del equipo (dato de desempeño de empleados).
- C-111 de la 011: tarifas y reglas de Meta (verificar con la factura real).

**Informativas:** C-102 (cuándo va la encuesta), C-103 (cómo se lee), C-104 (analítica en vivo), C-106
(A/B con plantilla B), C-107 (NPS) y C-108 (reporte mensual).

## Milestone humano pendiente

Con el piloto:
- Revisar que la encuesta no genere quejas.
- Confirmar que el reporte mensual "se entiende en 30 segundos".
