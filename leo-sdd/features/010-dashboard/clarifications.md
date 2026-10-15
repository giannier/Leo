# Clarificaciones — 010 Dashboard

Decisiones tomadas al construir la feature (2026-10-15), con la lectura conservadora.

## C-101: Días locales
Cada día es el día local del negocio (zona de su perfil; `America/Lima` por defecto). Un mensaje a las
23:30 en Lima cuenta para ese día aunque en UTC ya sea el siguiente.

## C-102: Qué cuenta y qué no
- Solo el canal real (WhatsApp). El simulador no cuenta: es donde el negocio prueba.
- Una conversación "con una persona" es la que una persona del negocio escribió o tomó ese día; el resto es
  "solo el asistente". Las horas ahorradas son esas conversaciones por los minutos que el negocio configura
  (4 por defecto).
- Primera respuesta: para las conversaciones cuyo primer mensaje llegó ese día, el tiempo hasta la primera
  respuesta (del asistente o de una persona).
- Ventas: solo pedidos confirmados (`confirmed_at`), fuera del simulador, sumados **por moneda**; nunca se
  suman monedas distintas ni se estiman ventas (C-001).
- "Productos más consultados" no entra en v1: las consultas no dejan un dato confiable por producto. Se
  muestran los más vendidos (pedidos confirmados).
- El costo incluye las trazas del simulador: le cuestan a la plataforma igual que las reales.

## C-103: Precio de los modelos
No se siembra ningún precio: el admin los carga en *Métricas* (por 1 000 tokens de entrada y salida, desde
una fecha). Una respuesta sin precio queda sin costo y el panel la cuenta aparte ("N respuestas sin costo"),
nunca como gratis. Un precio nuevo no reescribe el costo de lo que ya pasó. Solo se costea el chat del agente;
OCR, lectura de comprobantes y embeddings no quedan en `agent_traces` (para una versión siguiente).

## C-104: Retención (C-003)
Un job diario borra `metrics_daily` de más de 24 meses y `agent_traces` de más de 6 meses.

## C-105: Pantallas
- *Resultados* (`/resultados`) para todos los roles; el operador lo ve sin dinero: sin ventas, sin más
  vendidos y con un CSV también sin ventas.
- Una sola gráfica (conversaciones por día, apiladas por quién atendió), con leyenda, tooltip y vista de
  tabla. El resto son números con su cambio frente al período anterior. Colores de la paleta de referencia,
  validados contra la superficie blanca del panel.
- *Métricas* (`/admin/metricas`) para la plataforma: estados, registros, conversión, bajas, MRR, margen por
  negocio, operación y precios de modelos.
