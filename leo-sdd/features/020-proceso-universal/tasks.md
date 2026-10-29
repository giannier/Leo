# Tasks — 020 Proceso universal

Diseño en [spec-plan.md](spec-plan.md); decisiones en [clarifications.md](clarifications.md) (D1–D4 y D6–D8 aprobadas; D5, el
gasto en la API de OpenAI, **no aprobado por ahora**: se prueba con la suite automática y con conversaciones simuladas).
Cada tarea termina con la suite en verde, lint y tipos limpios. Nada se despliega sin el «despliega» de Giannier, y nada que
cambie el prompt llega a producción sin evals con el modelo real (constitución).

## F1 · La oferta: productos y servicios (backend)

- **T1.1 Esquema** (migración 0070): `products.tipo` (`producto` | `servicio`, por defecto `producto`; los agendables pasan a
  `servicio`), `modo_venta` admite `informar` (D7), `products.bundle_categoria` (la categoría de la que se eligen los ítems de un
  paquete, D2). Modelo, constantes y restricciones.
  *Verificación:* migración sube y baja; `alembic check` limpio; datos de producción: 13 productos sin pérdida.
- **T1.2 Cómo se cierra un ítem** (`process/offer.py`): función pura `cierre(producto)` → `pedido` | `cotizacion` | `cita` |
  `informar`, desde `modo_venta` y `agendable` (una sola fuente de verdad: no se duplica el dato).
  *Verificación:* tabla de casos.
- **T1.3 API de productos:** `tipo`, `modo_venta = informar`, `bundle_categoria` en crear, editar, responder, duplicar e
  importar (CSV y Excel, con alias).
  *Verificación:* pruebas de la API y del importador.
- **T1.4 Paquetes por categoría** (motor de pedidos): con `bundle_categoria`, solo se eligen ítems de esa categoría (y nombres
  libres si el paquete los admite); las sugerencias salen de esa categoría.
  *Verificación:* ferretería: «paquete de 3 herramientas» acepta martillo y rechaza pintura con las opciones de herramientas.
- **T1.5 «Solo informar» no entra a un pedido:** `gestionar_pedido` lo rechaza como hoy rechaza lo «a cotizar», y dice que lo
  atiende un asesor (`derivar_a_humano`, motivo asesoría).
  *Verificación:* prueba de la herramienta.
- **T1.6 Lo que el agente ve del ítem:** la búsqueda muestra si es servicio, cómo se cierra y, si es paquete, cuántos se eligen
  y de dónde. Solo agrega datos; las tres plantillas actuales no cambian su texto.
  *Verificación:* pruebas del formato y del presupuesto de tokens.

## F2 · El proceso universal v1 (detrás del interruptor)

- **T2.1** Plantilla `universal` en código (etapas técnicas y embudo de siempre), sin límite de líneas (D8), con los valores
  por defecto que hoy vienen de cada plantilla movidos a la configuración del negocio (spec §5.3).
- **T2.2** Texto de cada etapa escrito desde cero, genérico, ordenado por cómo se cierra; versión `proceso universal v1` en la
  base (migración), sin tocar las versiones de las tres plantillas.
- **T2.3** Bloque «Cómo se cierra» del prompt: solo los cierres que el negocio usa; el cierre del ítem del que se habla lo
  decide el código (`cierre()`), no el modelo.
- **T2.4** Caminos para cualquier rubro (D4).
- **T2.5** Interruptor por negocio y vuelta atrás; las conversaciones abiertas siguen en el proceso nuevo sin perder etapa,
  pedido, cotización ni datos (spec §7).
- **T2.6** Pruebas: presupuesto de 6 000 tokens, coherencia prompt-herramientas, flujos con el modelo simulado (mock) por tipo
  de negocio, aislamiento.
- **T2.7** ✅ Conversaciones simuladas con Claude como agente (sin la API de OpenAI): cinco negocios (pedido con paquete por
  categoría, cita + pedido, cotización, «lo arma una persona», educación con la plantilla aplicada). Informe en
  [simulacion-t27.md](simulacion-t27.md): ocho fallas encontradas y corregidas (C-04), entre ellas siete rubros que mandaban
  derivar toda reserva por encima de la agenda (migración 0074). Arnés y escenarios en `backend/evals/simulacion/`.

## F3 · Plantillas de rubro editables

- **T3.1** ✅ Modelo de «aplicar plantilla» (migración 0072, `services/rubro_template.py`): las situaciones sugeridas se copian
  al negocio con su clave y versión (activas si están completas, borradores si piden un dato), y los caminos, datos y pago de
  la plantilla llenan solo las opciones que el negocio no definió (C-02). API `GET /assistant/rubro/plantilla`,
  `POST …/aplicar`; historial con deshacer.
- **T3.2** ✅ Novedades por elemento cuando se publica una versión nueva: «nueva» o «mejorada», aceptar una por una o marcarlas
  vistas (C-03). API `POST …/novedades/{clave}/aceptar` y `POST …/novedades/vistas`. Las reglas mejoradas también se avisan.
- **T3.3** ✅ D3 cumplida con C-01: las reglas del rubro siguen siendo reglas, pero el negocio las escribe con sus palabras
  (`PUT /assistant/rules/rubro/{rule_key}/texto`) o las apaga. Las copias hechas antes desde las sugeridas se reconocen.
- **T3.4** Contenido: ✅ plantilla de educación con certificaciones (migración 0073, rubro `instituto` v2: 5 reglas, 9
  situaciones, 2 caminos, datos y pago); ✅ los rubros de las empresas de Giannier (migración 0075: quince rubros nuevos con
  plantilla, y el rubro pasa a ser dato: C-05, fuentes en [rubros-investigacion.md](rubros-investigacion.md)); pendiente:
  plantillas (situaciones con clave y opciones) para los trece rubros de antes, y «otros» genérico.
  *Verificación:* `tests/integration/test_rubro_template.py` (36 pruebas: aplicar, valores, idempotencia, copias anteriores,
  opciones propias, novedades, aceptar, vistas, texto propio, historial, permisos, aislamiento).

## F4 · Panel y administración

- **T4.1** ✅ Formulario de producto o servicio: qué es (producto o servicio; si se agenda, servicio), cómo se cierra («lo
  pide y lo paga», «se cotiza», «lo arma una persona», con su explicación) y de qué categoría se eligen las opciones de un
  paquete. El catálogo muestra servicio/producto, «se agenda», el paquete con su categoría y «Con asesor».
- **T4.2** ✅ Asistente › Proceso y rubro: en el proceso universal, «Cómo vende tu agente» con los cierres que usa el catálogo
  (`ProcessView.cierres`) en lugar del selector de plantillas; en el rubro, la tarjeta de la plantilla (qué trae, aplicar con
  los datos que piden algunas situaciones, novedades una por una con «Tomar» y «Marcar como vistas»), reglas del rubro con
  «Escribir con mis palabras» y «Volver al texto de la plantilla», y las reglas mejoradas en las novedades.
- **T4.3** ✅ Configuración inicial: el rubro se elige de la lista de la API (`GET /tenant/rubros`, C-05) con el aviso de política
  de WhatsApp del rubro; el resumen termina con la plantilla del rubro en una columna, con «Aplicar plantilla» arriba.
- **T4.4** ✅ Administración: en el detalle del negocio, su rubro, su proceso y «Pasar al proceso universal» / «Volver a las
  plantillas» (auditado); en el catálogo, las situaciones sugeridas con clave, etapas y «siempre», y lo que la plantilla
  sugiere para el proceso. Los evals al publicar ya existían (017, TASK-466).
- *Verificación:* ✅ capturas a 1 900, 1 280 y 390 px con un negocio de prueba (instituto en el proceso universal): ninguna
  pantalla se desborda a 390 px; al revisar se corrigieron el catálogo a 1 280 px (los nombres desaparecían y los botones
  salían de la tarjeta: ya pasaba antes) y las reglas largas del proceso en el teléfono. Aplicar la plantilla y escribir una
  regla con palabras propias probados en el panel. Tipos y lint del frontend limpios. La prueba e2e del asistente de
  configuración se actualizó a la plantilla; no se corrió la suite e2e (necesita el entorno completo).

## F5 · Validación y migración

- **T5.0 Validación adelantada de la 0074 y regresión de `main`** (Giannier, 2026-10-29: «adelántalo con un presupuesto
  pequeño») — **a la espera de que confirme el monto.** Con gpt-5.4-mini: el conjunto `SALES_FLOWS_SET=agenda_rubros`
  (16 casos: un negocio con agenda por cada uno de los 7 rubros, más 2 sin agenda que deben derivar) antes y después de la
  0074, dos corridas cada uno; y la regresión completa (`make evals`: flujos, calidad con juez, conversaciones reales de
  Nayu, recuperación, doradas) dos veces sobre `main`, porque desplegar la 0074 despliega también F1–F3 (lo del proceso
  universal sigue oculto). Estimado ≈ US$2.5; tope US$5. Nada se despliega sin «despliega».

- **T5.1** Batería universal con el modelo real, dos corridas por umbral — **bloqueada hasta que Giannier apruebe el
  presupuesto (D5)**.
- **T5.2** Migración por negocio (D6): prueba → Nayu → negocio activo, con «despliega».
- **T5.3** Retiro de las tres plantillas cuando ningún negocio las use.
