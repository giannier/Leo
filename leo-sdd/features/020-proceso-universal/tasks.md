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
- **T2.7** Conversaciones simuladas con Claude como agente (sin la API de OpenAI) sobre la batería universal; informe de lo que
  muestran y de lo que no pueden medir. **Se corre al cerrar F3**, junto con las plantillas de rubro (educación incluida), para
  no hacer dos pasadas.

## F3 · Plantillas de rubro editables

- **T3.1** Modelo de «aplicar plantilla»: las piezas se copian al negocio con su origen; editar, apagar y borrar.
- **T3.2** Novedades por cambio cuando se publica una versión nueva de la plantilla (extiende `tenant_catalog_seen`).
- **T3.3** Las reglas de rubro de hoy pasan a ser situaciones sugeridas (D3), con migración de los negocios existentes.
- **T3.4** Contenido: plantilla de educación (con certificaciones), luego los rubros de las 20 empresas, luego el resto, y
  «otros» genérico.

## F4 · Panel y administración

- **T4.1** Formulario de producto o servicio: tipo, cómo se cierra, paquete (cuántos y de dónde).
- **T4.2** Asistente › Proceso sin elección de plantilla: «cómo vende tu agente», plantilla aplicada, novedades.
- **T4.3** Configuración inicial: rubro → vista previa de la plantilla → aplicar.
- **T4.4** Administración: proceso universal y plantillas de rubro, con evals al publicar.
- *Verificación:* capturas a 1 900, 1 280 y 390 px; tipos y lint del frontend.

## F5 · Validación y migración

- **T5.1** Batería universal con el modelo real, dos corridas por umbral — **bloqueada hasta que Giannier apruebe el
  presupuesto (D5)**.
- **T5.2** Migración por negocio (D6): prueba → Nayu → negocio activo, con «despliega».
- **T5.3** Retiro de las tres plantillas cuando ningún negocio las use.
