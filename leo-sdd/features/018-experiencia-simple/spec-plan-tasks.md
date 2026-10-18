# Spec + Plan — Experiencia simple (lo que Ventor tiene, a la manera de Leo)

**Feature ID:** 018 · **Estado:** En construcción (desde 2026-10-15)
**Depende de:** 001, 004, 006, 017
**Origen:** el recorrido de Ventor (38 capturas, 2026-10-15) y el análisis de brechas con Giannier.
Decisiones en [clarifications.md](clarifications.md).

## Resumen
Leo vende mejor que Ventor (proceso por etapas, pedidos y paquetes reales, comprobantes con visión, rubros,
conocimiento con citas), pero Ventor es más fácil de usar. Esta feature cierra las brechas de **uso** sin
cambiar cómo piensa el agente y sin gastar modelo de más:

- Un **catálogo de verdad** (hoy la página Catálogo dice "próximamente" y los productos solo se cargan en la
  configuración inicial, sin fotos).
- **Todo el asistente en un solo lugar**, con menú lateral como Ventor, incluyendo productos y conocimiento,
  y el objetivo y la derivación editables (hoy solo se fijan en la configuración inicial).
- **"¿Respondió bien?" en el simulador**: marcar una respuesta, ver por qué respondió así y arreglarlo con un
  atajo, sin diagnóstico por IA.

**Fuera de alcance por decisión de Giannier (2026-10-15):** copiloto de configuración y diagnóstico con IA
(costo del LLM: por ahora cada negocio configura por formulario), login con Google y términos (datos
personales), dependencias nuevas. Quedan como Fase B, más abajo.

## User Stories
- **HU-01 Catálogo:** WHEN el dueño entra a Catálogo, THEN ve sus productos con foto, precio, categoría y estado;
  busca por nombre o etiqueta; filtra por categoría y por activos/inactivos; crea, edita, duplica, activa o
  desactiva y borra; e importa desde Excel o CSV (el flujo de vista previa que ya existe).
- **HU-02 Fotos del producto:** WHEN edita un producto, THEN sube hasta 10 fotos (la primera es la principal),
  las reordena y las borra. Son recursos multimedia ligados al producto, que el agente ya ofrece al mostrarlo.
- **HU-03 Etiquetas:** WHEN pone etiquetas a un producto ("oferta", "verano", "SIAF"), THEN el agente lo
  encuentra también por ellas en `buscar_productos`, y el dueño filtra por ellas en el catálogo.
- **HU-04 Asistente en un solo lugar:** WHEN entra a Asistente, THEN un menú lateral (pestañas en el celular)
  reúne Resumen, Información del negocio, Comportamiento, Proceso y rubro, Productos, Situaciones, Multimedia y
  Conocimiento, cada uno con su estado (completo, a medias, pendiente).
- **HU-05 Objetivo y derivación editables:** WHEN cambia en Comportamiento el objetivo del asistente o cuándo
  pasa a una persona, THEN se guarda sin volver a la configuración inicial.
- **HU-06 ¿Respondió bien?:** WHEN prueba en el simulador, THEN cada respuesta del asistente tiene 👍 / 👎. Con 👎
  escribe el motivo y ve **por qué respondió así** (etapa, situaciones aplicadas, fuentes de conocimiento y
  herramientas usadas, de la traza que ya existe) y **atajos**: crear una situación con este caso (con el mensaje
  del cliente y la respuesta esperada ya llenos), agregar una pregunta frecuente, o ir al dato del negocio. Lo
  marcado queda guardado para las sugerencias de la 017 (Fase 3).

## Requerimientos
- [RF-001] Etiquetas: hasta 10 por producto, de 1 a 30 caracteres, sin repetir (sin importar mayúsculas ni
  tildes). Columna en `products` por migración. La importación acepta una columna `etiquetas` separada por comas.
- [RF-002] `buscar_productos` busca el texto en el nombre **o** en las etiquetas. Sin cambios en el prompt ni en
  el esquema de la herramienta.
- [RF-003] Fotos: recursos multimedia de tipo imagen con `product_id`; máximo 10 por producto; el orden se guarda.
- [RF-004] La valoración del simulador se guarda solo para conversaciones del simulador, por negocio, con el
  mensaje calificado, la nota (1 o -1), el motivo (≤ 500) y quién la puso. Se puede cambiar. Aislamiento por
  `tenant_id` con su test.
- [RF-005] Ninguna pantalla nueva llama al modelo: no hay costo de LLM nuevo.

## Plan técnico (delta)
- Migraciones 0038 y 0039: `products.tags text[]` (+ índice GIN), `media_assets.orden int`,
  tabla `sandbox_feedback` (tenant_id, conversation_id, message_id único, rating, motivo, created_by, timestamps).
- API: `tags` en `ProductRequest/Patch/Response`; `GET /tenant/products?tag=`; `POST /tenant/products/{id}/duplicate`;
  `PUT /tenant/sandbox/messages/{id}/feedback`, `DELETE` igual; el historial del simulador trae la valoración.
- Agente: `ProductRepository.search_active` compara también contra las etiquetas (sin cambio de prompt).
- Frontend: `/catalog` real (lista, filtros, editor en panel lateral con fotos y etiquetas, importar); menú
  lateral de Asistente; objetivo y derivación en Comportamiento; 👍/👎 y panel "por qué" en el simulador.
- Testing: unit e integración de etiquetas, búsqueda, duplicado y valoración; aislamiento; e2e Playwright de
  catálogo y simulador; evals de catálogo solo si cambia algo que vea el modelo.

## Tasks
Commits: `feat(...): ... [TASK-18XX]`
- [x] **TASK-1801: Etiquetas de producto, orden de fotos y búsqueda por etiqueta** (migración, API, importación, `buscar_productos`).
- [x] **TASK-1802: Página Catálogo** (lista, búsqueda, filtros, editor con fotos y etiquetas, duplicar, importar). Depends: 1801.
- [x] **TASK-1803: Asistente en un solo lugar** (menú lateral con Productos y Conocimiento; objetivo y derivación en Comportamiento).
- [x] **TASK-1804: ¿Respondió bien? en el simulador** (valoración guardada, panel "por qué", atajos). Depends: 1801 (migración).
- [ ] **TASK-1805: e2e y milestone** (Playwright de catálogo, asistente y simulador; evidencia en `milestones/`).

## Fase B (no se construye ahora; cada punto necesita decisión de Giannier)
- **Copiloto de configuración** que conversa y escribe en los mismos formularios y tarjetas (nunca en texto
  libre), lee la web o la carta en PDF del negocio (006) y arranca con las sugerencias del rubro. Costo de LLM.
- **"Ajustar respuesta" con IA**: diagnóstico de la capa que falló, propuesta de cambio y re-prueba de las
  conversaciones guardadas del negocio antes de aplicar. Costo de LLM. Usa las valoraciones de HU-06.
- **Precio de oferta y variantes** (talla, color) en el pedido: tocan dinero y líneas de pedido. Mientras tanto
  las ofertas van por Promociones, que ya existen.
- **Situaciones con mensajes fijos en secuencia** (el "envío multimedia" de Ventor): cambia lo que envía el
  agente; requiere evals.
- **Login con Google, enlace al correo y aceptación de términos**: datos personales y dependencia nueva.
- **Modo copiloto** (el asistente redacta y una persona aprueba), etiquetas automáticas y kanban por etapa del
  proceso (con la 012), campañas (011), Instagram y Messenger, importar desde tiendas y catálogo de WhatsApp (015).
