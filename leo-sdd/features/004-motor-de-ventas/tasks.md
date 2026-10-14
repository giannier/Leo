# Tasks — Motor de ventas (004 v2)

**Convención de commits:** `feat(módulo): descripción [TASK-XXX]` (también `fix`, `test`, `refactor`, `docs`).
**Gate de cada task:** `ruff check`, `ruff format --check`, `mypy backend/app` y los tests del módulo en verde.
Los tests de aislamiento se escriben junto con cada tabla nueva, no al final.
**Milestones humanos:** las tasks marcadas 🧑 las valida Giannier; Claude Code prepara el escenario y se detiene.

---

## Fase A · Motor

- [x] TASK-401: Conciliar ESTADO.md, la 003 y esta spec
  - Input: `ESTADO.md`, `leo-sdd/features/003-agent-core/tasks.md`, `leo-sdd/features/004-*`, el código de `backend/app/agent/`
  - Output: `leo-sdd/features/004-motor-de-ventas/conciliacion.md` con:
    - qué existe hoy del agente (core, prompt builder, tools, trazas, catalog_guard);
    - qué de 301–308 ya está hecho (marcarlo `[x]` en la 003) y qué absorbe esta feature;
    - diferencias de nombres o tipos con `data-model.md` (tipo de PK, nombres de columnas de `contacts`, prefijo de API, estructura del frontend).

    La spec 004 vieja se mueve a `leo-sdd/features/_archivo/004-tools-v1/`.
  - Depends on: —
  - Verification: Giannier lee `conciliacion.md`. Ningún cambio de código en esta task
  - Parallel: false

- [x] TASK-402: Migración 0014 — `conversation_state` y `conversation_stage_events`
  - Input: `data-model.md §0014`, `conciliacion.md`
  - Output: modelos SQLAlchemy, migración Alembic (upgrade y downgrade) y tests de aislamiento de las 2 tablas
  - Depends on: TASK-401
  - Verification: `alembic upgrade head && alembic downgrade -1 && alembic upgrade head`; `pytest backend/tests/isolation -k stage`
  - Parallel: false

- [x] TASK-403: Migración 0015 — `business_info`, `faqs`, `promotions`
  - Input: `data-model.md §0015` (incluye los esquemas Pydantic de secciones)
  - Output: modelos, migración, esquemas Pydantic de las 5 secciones y tests de aislamiento
  - Depends on: TASK-402
  - Verification: upgrade/downgrade; tests de validación de cada esquema (casos válidos e inválidos)
  - Parallel: false (las migraciones 0014–0020 van en cadena; en paralelo pueden avanzar 409, 410, 415 y 416)

- [x] TASK-404: Migración 0016 — comportamiento, proceso y niveles por plan
  - Input: `data-model.md §0016`
  - Output: columnas nuevas en `agent_configs` y `plans`, tabla `process_stage_overrides` y tests de aislamiento
  - Depends on: TASK-403
  - Verification: upgrade/downgrade; los tenants existentes quedan con los defaults (`productos`, `equilibrado`, `moderado`)
  - Parallel: false

- [x] TASK-405: Migración 0017 — `media_assets`
  - Input: `data-model.md §0017`
  - Output: modelo, migración, FK de `payment_methods_business.media_asset_id` y test de aislamiento
  - Depends on: TASK-404
  - Verification: upgrade/downgrade; una `clave` duplicada en el mismo tenant falla y en otro tenant pasa
  - Parallel: false

- [x] TASK-406: Migración 0018 — productos aptos para venta
  - Input: `data-model.md §0018`
  - Output: columnas en `products`, esquemas de la API de catálogo actualizados (crear, editar e importar CSV/XLSX)
  - Depends on: TASK-405
  - Verification: tests de catálogo existentes en verde; la importación acepta las columnas nuevas como opcionales
  - Parallel: false

- [x] TASK-407: Migración 0019 — pedidos, ítems, comprobantes y secuencias
  - Input: `data-model.md §0019`
  - Output: modelos, migración y tests de aislamiento de `orders`, `order_items`, `payment_proofs` y `tenant_sequences`
  - Depends on: TASK-406
  - Verification: upgrade/downgrade; dos tenants generan su pedido #1 cada uno
  - Parallel: false

- [x] TASK-408: Migración 0020 — contactos con BSUID, `custom_data` y trazas
  - Input: `data-model.md §0020`; revisar primero cómo `contacts` guarda hoy el teléfono y el `wa_id`
  - Output: migración ajustada a lo que existe; upsert de contacto por BSUID o teléfono (el que llegue); columnas nuevas en `agent_traces`
  - Depends on: TASK-407
  - Verification: tests del webhook existentes en verde; upsert de un contacto que llega solo con BSUID (la respuesta a ese contacto se prueba en TASK-418)
  - Parallel: false

- [x] TASK-409: Plantillas de proceso en código
  - Input: `plantillas.md` §1–§3 y §5–§7
  - Output: `process/templates/{base,productos,certificaciones,servicios}.py`:
    - etapas, herramientas por etapa, embudo, opciones con su JSON Schema y defaults;
    - textos por defecto e instrucciones de seguimiento;
    - `version = 1`.
  - Depends on: TASK-402
  - Verification: test que valida que cada etapa tenga texto y embudo, que sus herramientas estén en la constante `TOOL_NAMES` (las 10 de `contracts/api.md §7`) y que los placeholders sean conocidos
  - Parallel: true

- [x] TASK-410: Motor de etapas (`engine.py`)
  - Input: `plantillas.md §1`, `referencias/engine_referencia.py` (34 casos ya validados: los 18 de Capacita v21 más 16 nuevos)
  - Output: `compute_stage()` y `funnel_for()` como funciones puras
  - Depends on: TASK-409
  - Verification: los 34 casos de la referencia portados a un test parametrizado de pytest con las plantillas reales. Cobertura de 100 % en `engine.py`
  - Parallel: false

- [x] TASK-411: Hechos (`facts.py`)
  - Input: `data-model.md` (hechos guardados y derivados), C-03, tabla de `pago_estado`
  - Output: modelo `Facts` y `FactsLoader.load(conversation)` en 1–3 queries
  - Depends on: TASK-407, TASK-408, TASK-410
  - Verification: tests con pedidos en cada estado, comprobantes y datos parciales; `datos_faltantes` respeta entrega (delivery, recojo, zonas vacías) y productos digitales; `rechazo_vigente` determina `rechazado`; `metodos_pago_enviados` por pedido y después del último rechazo
  - Parallel: false

- [x] TASK-412: Estado persistente (`state.py`)
  - Input: `data-model.md §0014`
  - Output: `StateRepo.get_or_create()` y `StateRepo.save(stage, funnel, facts_patch, reason, expected_version)`, que inserta un evento solo si cambia la etapa o el embudo y reintenta una vez si choca la versión
  - Depends on: TASK-411
  - Verification: test de concurrencia (dos saves con la misma versión) y test de no duplicar eventos
  - Parallel: false

- [x] TASK-413: Respuestas interactivas (`interactive.py`)
  - Input: C-15
  - Output: `apply_interactive(reply, state)`: `camino:` fija el hecho; el resto devuelve una línea de contexto
  - Depends on: TASK-412
  - Verification: tests por prefijo; un `camino:` con una clave que no existe se ignora y queda en el log
  - Parallel: true

- [x] TASK-414: Prompt builder por secciones
  - Input: `plantillas.md §4`, `plan.md` (presupuesto de tokens)
  - Output:
    - `process/service.py`, lado lectura: `ProcessConfigService.effective(tenant)` (plantilla, opciones, overrides y caché de 60 s);
    - `prompt_builder.py`, que arma reglas de plataforma, estilo, negocio compacto, ESTADO (con "ESTE TURNO" desde `TurnContext`, nombre confirmado o de perfil, promociones de cierre disponibles u ofrecidas, "asesoría ya derivada") e instrucciones de la etapa, con placeholders (si falta el valor, quita la línea).
  - Depends on: TASK-403, TASK-404, TASK-409, TASK-411
  - Verification: snapshot del prompt por etapa en las 3 plantillas; test de presupuesto ≤ 6 000 tokens con un negocio "lleno"; sin llaves `{…}` sin resolver
  - Parallel: true

- [x] TASK-415: Presupuesto de mensajes (`budget.py`)
  - Input: C-11
  - Output: `TurnBudget(max=3, reserva_texto=1)` con `consume()` y `remaining_text_bubbles()`
  - Depends on: —
  - Verification: tests unitarios
  - Parallel: true

- [x] TASK-416: Formateador WhatsApp
  - Input: nodo *Formato WhatsApp1* de Capacita v21 y sus 7 casos de `harness21.js`
  - Output: `formatter.to_bubbles(texto, detalle, budget) -> list[str]`
  - Depends on: TASK-415
  - Verification: los 7 casos portados a pytest más los de detalle breve/moderado/extenso y presupuesto agotado
  - Parallel: true

- [x] TASK-417: Registry de herramientas
  - Input: `contracts/api.md §7`, `plantillas.md §2`
  - Output:
    - `tools/registry.py` con el sobre `ToolResult(status, data, decir)`, `for_stage(template, stage, facts)` y un contexto de herramienta (tenant, conversación, budget, adaptador, sandbox, `TurnContext`);
    - las **10 herramientas declaradas** con su schema; las que llegan en fases posteriores (`gestionar_pedido`, `registrar_datos_cliente`, `enviar_metodos_pago`, `derivar_a_humano`) quedan como stub que devuelve `no_disponible`;
    - las tools existentes (catálogo, conocimiento) migran al sobre.
  - Depends on: TASK-409, TASK-415
  - Verification:
    - cada etapa expone solo sus herramientas más las globales (`plantillas.md §2`);
    - `elegir_camino` no aparece si no hay caminos;
    - `gestionar_pedido(confirmar)` se rechaza fuera de COBRO o sin contra entrega.
  - Parallel: false

- [x] TASK-418: YCloud — interactivos, media, referral y BSUID
  - Input: documentación oficial de YCloud (enviar interactive/buttons/list, media, webhook `whatsapp.inbound_message.received` con `interactive`, `referral` y el identificador de usuario); `contracts/api.md §8`
  - Output: `send_buttons`, `send_list` y `send_media`; `Recipient` con teléfono o BSUID; parseo de `interactive`, `referral` y `from_user_id`; fixtures JSON reales en `tests/fixtures/ycloud/`
  - Depends on: TASK-408
  - Verification: tests de contrato contra los fixtures; firma HMAC sin cambios; un contacto solo con BSUID recibe una respuesta (envío por `recipient`). Si la doc contradice `contracts/api.md`, manda la doc y se anota en `conciliacion.md`
  - Parallel: true (con 409–418)

- [x] TASK-419: YCloud — subida de media y caché
  - Input: C-17, doc de YCloud (endpoint de subida y vigencia del `media_id`)
  - Output: `upload_media()`, guardado de `provider_media_id` y su vencimiento, fallback con link firmado de 1 h, job `media.refresh_provider_ids`
  - Depends on: TASK-418, TASK-405
  - Verification: tests con el proveedor simulado: la primera vez sube, la segunda reutiliza, si vence vuelve a subir y si la subida falla usa el link
  - Parallel: false

- [x] TASK-420: Tools `enviar_recurso`, `mostrar_opciones`, `elegir_camino` y extensión del catálogo
  - Input: `contracts/api.md §7`
  - Output: las 3 tools; `buscar_productos` y `ver_producto` devuelven `modo_venta`, `bundle_size`, `destacado`, recursos y promociones automáticas aplicables, y fijan `vio_productos` cuando hay resultados
  - Depends on: TASK-417, TASK-405, TASK-406, TASK-418
  - Verification:
    - no se reenvía un recurso (`omitido`);
    - se truncan los límites de WhatsApp (3 botones de 20, 10 filas de 24/72, cuerpo de 1024);
    - el enum de caminos es por tenant;
    - con presupuesto agotado devuelve `no_disponible`.
  - Parallel: false

- [x] TASK-421: TurnPipeline
  - Input: `plan.md` (pipeline de 14 pasos), `agent/core.py`
  - Output: `agent/pipeline.py` con `TurnContext`, `stage_previa` y gate (pausado, opt-out, derivación vigente aunque la etapa sea VERIFICACION); `workers/channel.py` y el simulador llaman al pipeline; `core.py` recibe el prompt y las tools ya armados; las trazas incluyen etapa, embudo, tools habilitadas, salientes y nivel
  - Depends on: TASK-412, TASK-413, TASK-414, TASK-416, TASK-420, TASK-418
  - Verification:
    - tests de integración con LLM falso (`offline`) recorriendo INICIO → CALIFICAR → IDENTIFICAR;
    - `saludo_enviado` queda fijado;
    - se crea un evento por cambio de etapa;
    - la suite completa existente sigue en verde.
  - Parallel: false

- [x] TASK-422: Simulador — salientes tipados y modo depuración
  - Input: `contracts/api.md §4`
  - Output: el endpoint acepta texto, `interactive_id` o imagen; devuelve `salientes[]` y `debug`; `GET /sandbox/messages?after=` para salientes asíncronos; `POST /sandbox/reset`; contacto sandbox con `external_user_id` sintético; nada sandbox sale al proveedor; el frontend dibuja botones, listas e imágenes y el panel de depuración (etapa, hechos, tools)
  - Depends on: TASK-421
  - Verification: Playwright: tocar un botón del simulador cambia la etapa en el panel de depuración
  - Parallel: false

- [x] TASK-423: 🧑 Milestone A — Capacita en el simulador (sin pedidos)
  - Input: un tenant "Escuela Capacita" con la plantilla `certificaciones`, sus productos y paquetes (carga por CSV), recursos `saludo` y `flyer_paquetes`
  - Output: guion de prueba en `leo-sdd/features/004-motor-de-ventas/milestones/A.md` (escenarios 1–3 de Capacita: orgánica, calificación escrita, anuncio simulado)
  - Depends on: TASK-419, TASK-422
  - Verification: Giannier recorre los 3 escenarios y aprueba
  - Parallel: false

## Fase B · Venta

- [x] TASK-424: Pricing y promociones (`orders/pricing.py`)
  - Input: C-10, `data-model.md §0015` (promotions)
  - Output: función pura `price(items, promociones_vigentes, promos_ofrecidas, now) -> Totales` que elige la de mayor ahorro sin acumular; incluye envío por zona y envío gratis desde un monto
  - Depends on: TASK-403, TASK-406
  - Verification: tests de tabla (porcentaje, monto fijo, 2×1, 3×2, precio por cantidad, monto mínimo, vigencia, cierre no ofrecido, cierre vencido, envío gratis). Cobertura de 100 %
  - Parallel: true (con Fase A desde TASK-410)

- [x] TASK-425: OrderService
  - Input: `data-model.md §0019` (transiciones), C-06, C-07
  - Output: pedido activo, crear pedido nuevo después de confirmado, numeración por tenant, transiciones válidas (`InvalidTransition`), snapshot de `datos_cliente` al pasar a `por_confirmar`, `is_sandbox`
  - Depends on: TASK-407, TASK-424
  - Verification: test de la matriz completa de transiciones; numeración concurrente sin duplicados
  - Parallel: false

- [x] TASK-426: Tool `gestionar_pedido`
  - Input: `contracts/api.md §7`
  - Output: acciones agregar, quitar, cantidad, elegir (libre o del catálogo según `bundle_permite_libres`), ver, vaciar, aplicar_promocion (registra la oferta en `promos_ofrecidas` aunque no haya pedido) y confirmar; cualquier cambio apaga `rechazo_vigente`; las selecciones libres entran en la lista blanca del `catalog_guard`; devuelve siempre el pedido completo
  - Depends on: TASK-425, TASK-417
  - Verification:
    - un producto de cotización devuelve `no_disponible`;
    - un paquete de 5 con 2 selecciones no queda completo;
    - "lo pensaré" sin pedido → `aplicar_promocion` registra la oferta y se aplica al crear el pedido dentro de la vigencia, pero no después;
    - un tema libre confirmado no lo bloquea el `catalog_guard`;
    - `confirmar` solo funciona con contra entrega en COBRO;
    - los precios nunca vienen del modelo.
  - Parallel: false

- [x] TASK-427: Tool `registrar_datos_cliente`
  - Input: `data-model.md §0020` (validación por campo), opción `datos_requeridos`, sección Entregas (C-22)
  - Output: herramienta global; guardado parcial en `custom_data` (incluido `nombres` en cualquier etapa); `tipo_entrega`, `direccion`, `referencia` y `zona_envio` cuando el pedido requiere entrega, con recálculo del envío; apaga `rechazo_vigente`; devuelve `faltan` e `invalidos`
  - Depends on: TASK-426, TASK-408, TASK-403
  - Verification:
    - un DNI de 7 dígitos queda inválido;
    - una zona inexistente devuelve las zonas válidas;
    - si el negocio solo ofrece recojo, `tipo_entrega` se fija solo;
    - un producto digital no pide dirección;
    - los datos no aparecen en trazas (enmascarados).
  - Parallel: true

- [x] TASK-428: Tool `enviar_metodos_pago`
  - Input: `payment_methods_business`, `media_asset_id` y el recurso `metodos_pago`
  - Output: texto con los métodos activos más la imagen o QR (consume presupuesto) y el hecho `metodos_pago_enviados = {order_id, en}`
  - Depends on: TASK-417, TASK-419, TASK-425
  - Verification: `omitido` la segunda vez para el mismo pedido; se permite un reenvío después de un rechazo; un pedido nuevo después de CERRADO vuelve a recibir los métodos; el embudo pasa a CUENTAS_ENVIADAS
  - Parallel: true

- [x] TASK-429: ProofPipeline y visión
  - Input: C-08, `plan.md` (comprobantes)
  - Output: `orders/vision.py` (salida estructurada, timeout de 8 s) y `orders/proofs.py`: guarda la imagen en MinIO privado, crea el `payment_proof`, calcula las observaciones (monto distinto, código repetido sin contar sandbox, baja confianza, sin datos), pasa el pedido a `por_confirmar` y avisa al operador. Se integra en el paso 4 del pipeline usando `stage_previa`, incluso con la conversación derivada
  - Depends on: TASK-425, TASK-421
  - Verification:
    - tests con visión simulada para un comprobante válido, una foto que no es comprobante, un monto distinto, un código repetido y un timeout;
    - un comprobante que llega durante una derivación se registra y avisa, pero el bot no responde;
    - la etapa pasa a VERIFICACION;
    - el modelo recibe el resultado en el ESTADO.
  - Parallel: false

- [x] TASK-430: API de pedidos y avisos al cliente
  - Input: `contracts/api.md §5`, C-09
  - Output: router con lista, resumen, detalle, imagen firmada, confirmar, rechazar (`rechazo_vigente` y motivo; contra entrega vuelve a COBRO) y status; aviso por WhatsApp después del commit con los mensajes de sistema (sandbox: queda como saliente sandbox); comprobación de ventana de 24 h; `audit_logs`; recálculo de etapa con su evento
  - Depends on: TASK-429
  - Verification:
    - tests de API por rol;
    - con la ventana cerrada responde `aviso_cliente: ventana_cerrada` y no llama al proveedor;
    - un pedido de otro tenant da 404;
    - una doble confirmación da 409.
  - Parallel: false

- [x] TASK-431: Notificaciones al tenant
  - Input: `contracts/api.md §10`
  - Output: tipos nuevos en `tenant_notifications` y correos con el proveedor existente (`console` en desarrollo), con límite para `llm_caido`
  - Depends on: TASK-430
  - Verification: tests de que cada evento crea su notificación y su correo
  - Parallel: true

- [x] TASK-432: Frontend — Pedidos
  - Input: `contracts/api.md §5`, referencia visual: página "Pedidos" de Ventor (tarjetas de resumen, filtros por estado, tabla y detalle)
  - Output: página `/pedidos` con resumen por periodo, filtros, búsqueda y tabla; detalle con ítems, datos del cliente, imagen del comprobante, datos leídos, observaciones resaltadas y botones Confirmar (con "siguiente paso") y Rechazar (con motivo); aviso si la ventana está cerrada
  - Depends on: TASK-430
  - Verification: Playwright: confirmar un pedido sandbox cambia su estado y muestra "aviso enviado"; `tsc` y `eslint` limpios
  - Parallel: true

- [x] TASK-433: 🧑 Milestone B — Pedido completo (aprobado el 2026-10-14 por Claude, por delegación expresa de
  Giannier: "bajo tu criterio, define si se aprueba la B"; sustento en `milestones/B.md`, sección 4)
  - Input: tenant Capacita de la TASK-423 más un tenant "Tienda demo" con la plantilla `productos`, delivery y 2×1
  - Output: guion `milestones/B.md` (Capacita escenarios 4–5; tienda con 3 productos, promoción, delivery, comprobante y confirmación)
  - Depends on: TASK-426, TASK-427, TASK-428, TASK-431, TASK-432
  - Verification: Giannier lo recorre en el simulador y confirma desde Pedidos
  - Parallel: false

## Fase C · Configuración en capas (Fase 1 de la 017)

> **Reemplazada por la Fase 1 de la feature 017** (Giannier, 2026-10-13; `017-configuracion-en-capas/`).
> Cuatro capas: plantilla de proceso → rubro → perfil del negocio → situaciones; gana la más específica.
> El negocio **apaga** reglas de plantilla y rubro, no las edita; sus reglas propias son **situaciones**.
> Diseño en `017/plan.md`; decisiones en `017/clarifications.md`.
> Empieza tras el Milestone B (TASK-433, aprobado el 2026-10-14). TASK-459 (número real) pasa al final
> del MVP por decisión de Giannier (017 C-13).

- [ ] TASK-459: 🧑 Prueba con un número real de WhatsApp por túnel (antes de la Fase 1)
  - Input: sugerencia aceptada por Giannier (2026-10-13); TASK-209 de la 002 (milestone humano pendiente);
    TASK-456 (fixtures reales)
  - **Preparado (2026-10-13):** la guía `milestones/numero-real.md` ya está escrita; falta ejecutarla.
  - Output: `milestones/numero-real.md`, guía paso a paso para correr Leo en la PC de Giannier (WSL2 o
    contenedor) con un túnel HTTPS (Cloudflare Tunnel o ngrok, herramienta de su PC, no dependencia del
    repo) apuntado a `POST /webhooks/ycloud`, con `YCLOUD_WEBHOOK_SECRET` y el número conectado desde el
    panel; guion de prueba (texto, botón, lista, imagen de comprobante, pedido completo, confirmación
    desde Pedidos, ventana de 24 h, referral de anuncio si es posible); medición del p95 de respuesta;
    captura de los webhooks reales que reemplazan los fixtures escritos a mano (cierra TASK-456) y
    corrección de lo que falle
  - Depends on: TASK-433
  - Verification: Giannier completa el guion con su número; la suite pasa con los fixtures reales;
    TASK-209 de la 002 queda cerrada
  - Parallel: false

- [x] TASK-460: Catálogo versionado de plantillas y rubros
  - **Hecho (2026-10-14):** migración 0030; `models/catalog.py`, `repositories/catalog.py`,
    `process/rule_split.py`; 118 reglas de proceso v1 y seis rubros v1 publicados; el texto de etapa sale
    de la BD (respaldo: el código); triggers que congelan lo publicado; `process_stage_overrides` eliminada.
    **Para que Giannier revise:** el contenido de los rubros en `backend/alembic/data/rubros_v1.json`.
  - Input: `017/plan.md` (modelo de datos), C-01, C-03, C-04 de la 017
  - Output: migraciones de `catalog_versions`, `catalog_rules`, `catalog_faqs`, `catalog_objections`,
    `catalog_suggested_situations` y `rubros`; migración de datos que parte los textos de etapa de
    `process/templates/*` en reglas con `rule_key` estable (proceso v1 publicada); rubros v1 de los seis
    rubros (reglas, 5 FAQ, 5 objeciones, 3 a 5 situaciones sugeridas, plantilla sugerida) para que
    Giannier los revise; se elimina `process_stage_overrides`
  - Depends on: TASK-433 (TASK-459 movida al final, 017 C-13)
  - Verification: el prompt de cada etapa armado desde la BD es idéntico al de hoy (snapshots); una
    versión publicada no se puede modificar
  - Parallel: false

- [x] TASK-461: Resolución de capas e interruptores
  - **Hecho (2026-10-14):** migración 0031; `process/layers.py` (`LayerResolver`: reglas de proceso y de
    rubro menos las apagadas, novedades por versión vista); `repositories/layers.py`; el texto de cada
    etapa y las reglas del rubro salen de ahí en `ProcessConfigService` (8 consultas por lectura); un
    interruptor invalida la caché; FAQ y objeciones del rubro en `catalog_embeddings` (job
    `index_catalog_knowledge` al arrancar el worker y cada hora) y `consultar_conocimiento` las devuelve
    como "Guía del rubro" bajo un umbral de distancia; `tenant_users.puede_revisar` (el dueño siempre).
    **Decisión:** las FAQ del rubro no se guardan como `knowledge_source` del negocio (tendrían que
    copiarse a cada negocio o romper el aislamiento): van a una tabla de plataforma y se buscan con el
    mismo vector de la consulta, sin llamada extra.
  - Input: `017/plan.md` (resolución de capas), C-04, C-09 de la 017
  - Output: `process/layers.py` (`LayerResolver`), `tenant_rule_switches`, `tenant_catalog_seen` (aviso
    de novedades) y la caché invalidada al publicar o al cambiar un interruptor; FAQ y objeciones del
    rubro indexadas como conocimiento de tipo `rubro`; `tenant_users.puede_revisar` (dueño por defecto)
  - Depends on: TASK-460
  - Verification: una regla apagada no llega al prompt; una versión nueva trae sus reglas activas y
    respeta las apagadas; el aviso aparece hasta marcarse visto; tests de aislamiento
  - Parallel: false

- [x] TASK-434: API de información del negocio, FAQ, promociones y perfil
  - **Hecho (2026-10-14):** `api/v1/assistant_info.py` (business-info, FAQ indexadas como conocimiento,
    promociones, behavior con el perfil por formulario, categorías); `titular` y `media_asset_id` en
    métodos de pago; `ProofPipeline` compara con los titulares.
  - Input: `contracts/api.md §1`, RF-010 de la 017
  - Output: routers; las FAQ se indexan como `knowledge_source` de tipo `faq`; borrar una FAQ borra su
    fuente. Perfil por formulario: tratamiento (tú o usted), emojis, saludo, política de descuentos.
    Cuentas de cobro con titular (hallazgo D-1): `ProofPipeline(payees=...)` compara el destinatario.
    Gestión de categorías del catálogo (decisión 13)
  - Depends on: TASK-403
  - Verification: tests de API y aislamiento; `consultar_conocimiento` encuentra una FAQ recién creada; un
    comprobante con el titular configurado sale sin `destinatario_no_verificado`
  - Parallel: true

- [x] TASK-462: Situaciones
  - **Hecho (2026-10-14):** migración 0032 (`situations`, campos del perfil en `agent_configs`,
    `payment_methods_business.titular`, `agent_traces.situaciones`); `restrictions` pasa a una situación
    `migrada` y la columna se elimina; API `/assistant/situations` (CRUD, activar/desactivar, desde
    sugerida); embedding por el job `embed_situations` y barrido cada hora; el wizard guarda su texto
    libre como la situación migrada hasta TASK-468.
  - Input: RF-006, RF-011, RF-013 de la 017
  - Output: tabla `situations` con embedding; repositorio y API (crear, editar, borrador, activar,
    desactivar, desde sugerida del rubro); embedding calculado por el worker al guardar; migración de
    `agent_configs.restrictions` a una situación por negocio (origen `migrada`) y baja de la columna
  - Depends on: TASK-461
  - Verification: tests de API y aislamiento; un borrador no es visible para el canal real
  - Parallel: true

- [x] TASK-463: Selector de situaciones en el pipeline
  - **Hecho (2026-10-14):** `agent/situations.py`; sin situaciones que comparar no hay llamada de
    embedding; umbral 0,55 (C-15).
  - Input: C-10 de la 017
  - Output: `agent/situations.py`: "siempre" + coincidentes con el mensaje y la etapa hasta
    `max_situaciones` (5 por defecto); una llamada de embedding por turno; si falla, solo las "siempre";
    ids y puntajes en la traza
  - Depends on: TASK-462
  - Verification: tests con embeddings controlados: una situación de otra etapa no entra; el máximo se
    respeta; el fallo del embedding no tira el turno
  - Parallel: false

- [x] TASK-464: Prompt por capas
  - **Hecho (2026-10-14):** PROCESO → RUBRO → NEGOCIO → SITUACIONES → PRIORIDAD; ESTILO con tratamiento,
    emojis y saludo; política de descuentos en NEGOCIO (C-14); topes repartidos sin subir de 6 000 (C-16);
    snapshots regenerados. La prueba de presupuesto lleva 5 situaciones largas y las reglas del rubro.
  - Input: C-03 y C-11 de la 017
  - Output: secciones PROCESO, RUBRO, NEGOCIO y SITUACIONES DEL NEGOCIO con la regla de prioridad; ESTILO
    sin "reglas adicionales" y con tratamiento, emojis y saludo; topes y test de presupuesto con 5
    situaciones largas; snapshots regenerados
  - Depends on: TASK-461, TASK-463
  - Verification: test de presupuesto; snapshots revisados en el diff
  - Parallel: false

- [x] TASK-435: API de comportamiento y proceso (sin edición de etapas)
  - **Hecho (2026-10-14):** templates, process (GET/PUT), rules con interruptor (admin o revisor), rubro con
    novedades, prompt-preview; `puede_revisar` en Equipo; dependencia `ReviewerDep`.
  - Input: `contracts/api.md §1–§2`, C-01 de la 017
  - Output: behavior, templates, process (opciones validadas contra el schema de la plantilla) y reglas
    por etapa con su interruptor (`PUT /assistant/rules/{kind}/{rule_key}`); **se retiran** `PUT` y
    `DELETE /assistant/process/stages/{stage_key}`; rubro con novedades; prompt-preview; caché por tenant
    invalidada al guardar. `contracts/api.md` actualizado
  - Depends on: TASK-404, TASK-461
  - Verification: un nivel fuera del plan da 403 solo con `BILLING_ENFORCED` encendido; apagar una regla
    cambia el siguiente mensaje del simulador
  - Parallel: true

- [x] TASK-465: Simulador con situaciones en borrador
  - **Hecho (2026-10-14):** `situaciones_prueba` en el cuerpo; depuración con `situaciones` (con
    distancia) y `reglas_apagadas`. El panel (frontend) va en TASK-467.
  - Input: RF-009 de la 017, TASK-422
  - Output: `situaciones_prueba` en el simulador; el panel de depuración muestra reglas apagadas,
    situaciones elegidas y su puntaje
  - Depends on: TASK-463
  - Verification: un borrador probado en el simulador entra; en el canal real no
  - Parallel: true

- [x] TASK-466: Panel de plataforma — catálogo, evals y publicación (API; la pantalla va con el frontend)
  - **Hecho (2026-10-14):** `api/v1/platform_catalog.py`, `services/catalog_evals.py` (estructura y dos
    casos con el modelo: COBRO envía métodos; una situación gana sobre el rubro sin inventar precios),
    job `run_catalog_evals`; publicar exige evals verdes sobre el contenido actual (hash).
  - Input: C-04 de la 017
  - Output: API y pantalla de versiones de plantillas y rubros (borrador editable, evals encolados,
    publicar con 409 si no pasaron); evals de catálogo en `backend/evals/catalog/`; auditoría de plataforma
  - Depends on: TASK-460, TASK-464
  - Verification: publicar sin evals verdes es imposible; los negocios ven la novedad
  - Parallel: true

- [x] TASK-436: API de multimedia
  - **Hecho (2026-10-14):** `api/v1/assistant_media.py` y `services/media_library.py`: tipo por bytes
    (magic bytes), límites de WhatsApp, vista previa firmada, reemplazo del archivo (mismo tipo).
  - Input: `contracts/api.md §3`, límites de `data-model.md §0017`
  - Output: subida a MinIO privado con validación de tipo real (magic bytes) y tamaño; preview firmado;
    reemplazo de archivo invalida `provider_media_id`; el `PATCH` de métodos de pago acepta
    `media_asset_id` (QR). Las situaciones adjuntan archivos de la biblioteca
  - Depends on: TASK-405, TASK-419
  - Verification: tests de límites (imagen de 6 MB rechazada; un .exe renombrado a .jpg rechazado)
  - Parallel: true

- [x] TASK-437: Overview y lista de activación
  - **Hecho (2026-10-14):** `services/overview.py`; pasos nuevos `primera_situacion` y `probar_situacion`.
  - Input: `contracts/api.md §1` (overview)
  - Output: estado por sección y progreso; suma "activaste tu primera situación" y "probaste una
    situación en el simulador"; `probar_simulador` se marca con la primera conversación sandbox que llega
    a IDENTIFICAR
  - Depends on: TASK-434, TASK-435, TASK-436, TASK-462
  - Verification: tests con un tenant vacío, uno parcial y uno completo
  - Parallel: false

- [x] TASK-438: Frontend — Información del negocio y perfil
  - **Hecho (2026-10-14):** `/asistente/informacion` (métodos de pago con titular y QR, entregas, locales y
    horarios, FAQ, promociones, política de descuentos, tipos de cliente, devoluciones, contacto).
    Playwright: una zona guardada aparece en la vista previa del prompt.
  - Input: los 8 bloques de RF-001; RF-010 de la 017
  - Output: tarjetas por bloque (métodos de pago con titular y QR, locales y horarios, entregas, tipos de
    cliente, FAQ, promociones, devoluciones, contacto, política de descuentos), sin texto libre
    obligatorio
  - Depends on: TASK-434
  - Verification: Playwright: completar entregas y verlo reflejado en una respuesta del simulador
  - Parallel: true

- [x] TASK-439: Frontend — Comportamiento y estilo
  - **Hecho (2026-10-14):** `/asistente/comportamiento`; casilla «Revisor» en Equipo. Arreglo de paso: el
    cliente usaba `PATCH` para miembros del equipo y métodos de pago y el backend solo tiene `PUT` (405).
  - Input: `contracts/api.md §1` (behavior)
  - Output: ritmo, detalle (con ejemplo), seguimiento, tratamiento, emojis, saludo, nivel de inteligencia
    (sin bloqueo mientras `BILLING_ENFORCED` esté apagado), pausa de derivación, mensajes de sistema y
    estado del asistente; casilla "Revisor" en Equipo
  - Depends on: TASK-435
  - Verification: Playwright; `tsc` y `eslint` limpios
  - Parallel: true

- [x] TASK-440: Frontend — Proceso de venta y rubro
  - **Hecho (2026-10-14):** `/asistente/proceso` (plantilla con la sugerida del rubro, etapas con
    interruptores, rubro con novedades, FAQ, objeciones y situaciones sugeridas, vista previa del prompt).
    Playwright: apagar una regla la saca de la vista previa.
  - Input: `contracts/api.md §2`, C-01 y C-04 de la 017
  - Output: selector de plantilla (con la sugerida por el rubro); opciones generadas desde el schema;
    etapas en línea de tiempo con su columna del embudo y **sus reglas con interruptor** (sin editor de
    texto); reglas, FAQ y objeciones del rubro con interruptor; aviso de novedades; vista previa del
    prompt para el admin
  - Depends on: TASK-435
  - Verification: Playwright: apagar una regla y verla desaparecer de la vista previa del prompt
  - Parallel: true

- [x] TASK-467: Frontend — Situaciones
  - **Hecho (2026-10-14):** `/asistente/situaciones`; el simulador acepta `?probar=<id>` y su panel de
    depuración muestra situaciones y reglas apagadas. Playwright: crear, probar en borrador, activar.
  - Input: RF-006 y RF-009 de la 017
  - Output: lista de tarjetas con filtros por estado y origen; formulario (cuándo aplica, qué hace,
    archivos, derivar, ejemplo, etapas, siempre); "Probar en el simulador"; activar y desactivar
  - Depends on: TASK-462, TASK-465
  - Verification: Playwright: crear una situación, probarla en borrador, activarla
  - Parallel: true

- [x] TASK-468: Wizard — estilo y situaciones sugeridas (toca la 001)
  - **Hecho (2026-10-14):** el paso del agente pide trato, emojis y saludo (ya no «qué NO debe hacer») y
    aplica una vez la plantilla que sugiere el rubro; el resumen ofrece hasta 5 situaciones sugeridas.
    Playwright: el wizard termina con una situación sugerida activa.
  - Input: C-02 y C-03 de la 017
  - Output: paso 2 con tratamiento, emojis y saludo en lugar de "restricciones"; el rubro sugiere la
    plantilla; paso final con 3 a 5 situaciones sugeridas del rubro para activar o completar.
    `001/tasks.md` y `001/spec.md` actualizados
  - Depends on: TASK-461, TASK-462
  - Verification: Playwright: un negocio nuevo termina el wizard con situaciones activas
  - Parallel: true

- [x] TASK-441: Frontend — Multimedia, checklist y depuración
  - **Hecho (2026-10-14):** `/asistente/multimedia` (arrastrar, clave sugerida desde el título, reemplazar);
    widget del asistente en Inicio; `/asistente` con progreso. El Playwright de subir imagen necesita
    MinIO (`make up`); aquí no se pudo correr. Arreglo de paso: el operador de plataforma no tenía el
    enlace «Panel de operación» en el teléfono.
  - Input: `contracts/api.md §3`, overview
  - Output: biblioteca con subida (arrastrar), clave sugerida desde el título, "cuándo usarlo", vínculo a
    producto, promoción o situación; widget de activación en el inicio del panel; interruptor de
    depuración en el simulador
  - Depends on: TASK-436, TASK-437, TASK-422
  - Verification: Playwright: subir una imagen, pedirla en el simulador y verla llegar
  - Parallel: true

- [x] TASK-469: Evals de cierre de la Fase 1
  - **Hecho (2026-10-14):** evals del agente 2/2 y del catálogo 9/9 (dos veces) con `gpt-5.4-mini`
    (`scripts/run_catalog_evals.py`). La primera corrida encontró que el modelo dejaba de lado una
    situación «siempre» cuando el cliente preguntaba otra cosa: se reforzó el encabezado de SITUACIONES y
    una línea final las repite (medido: 10/12 → 12/12).
  - Input: C-01 de la 017
  - Output: evals del agente y de catálogo con OpenAI; resultado adjunto en `milestones/C.md`
  - Depends on: TASK-464, TASK-466
  - Verification: evals en verde
  - Parallel: false

- [x] TASK-442: 🧑 Milestone C — Configuración en capas en 30 minutos
  - Input: `017/spec.md` criterios de la Fase 1
  - Output: `milestones/C.md`: configurar una tienda desde cero con capas y situaciones, apagar una regla,
    probar una situación en borrador y activarla, publicar una versión de rubro con evals y ver la novedad
  - Depends on: TASK-437 a TASK-441, TASK-467, TASK-468, TASK-469
  - Verification: Giannier aprueba
  - Parallel: false
  - Hecho (2026-10-14): verificado por Claude con el modelo real, por instrucción de Giannier del
    2026-10-13 (milestones intermedios automáticos). 14/14 en dos corridas, evidencia en `milestones/C.md`.
    Pendiente pasado a la Fase D: la regla de INICIO intenta `enviar_recurso("saludo")` aunque no exista.

## Fase D · Autonomía

- [x] TASK-443: Derivación con pausa
  - Input: C-14, `contracts/api.md §6, §7, §9`
  - Output: tool `derivar_a_humano` (reemplaza el stub; termina el turno, envía el mensaje de derivación y con `asesoria` guarda `asesoria_derivada_en`), job `handoff.expire`, `GET /conversations/{id}/state`, `POST …/resume-bot`
  - Depends on: TASK-421, TASK-431
  - Verification: tests de que durante la pausa no se llama al LLM y se guardan los mensajes; al vencer se recalcula la etapa; el aviso al tenant incluye el resumen; después de vencer la pausa, ASESORIA no vuelve a derivar sola
  - Parallel: true
  - Hecho (2026-10-14): `agent/tools/handover.py` (la pausa sale de `derivacion_pausa_horas`, el mensaje de
    `mensajes_sistema.derivacion`), el loop corta en cuanto la tool termina (`ToolContext.ending`),
    `services/handoff.py` + job `handoff.expire`, `api/v1/conversations.py` y el aviso con "Reactivar
    asistente" en el detalle del pedido. Tests: `test_handover.py`, `test_conversation_state_isolation.py`.
    En el simulador la pausa también vale (así el dueño la ve); una conversación nueva empieza sin ella.

- [x] TASK-444: Seguimiento y descarte de borradores
  - Input: C-13, `plantillas.md §6`
  - Output: jobs `followups.run` (ventana con margen de 30 min, horario de 08:00 a 21:00 del tenant, intentos, etapas IDENTIFICAR, PEDIDO, DATOS, COBRO y VERIFICACION con rechazo, opt-out existente, derivación, y promoción para seguimiento en el último intento registrada en `promos_ofrecidas`) y `orders.discard_stale` (con evento `pedido_descartado`)
  - Depends on: TASK-425, TASK-443
  - Verification: tests con reloj congelado para cada condición que impide enviar; reset cuando el cliente escribe; candado contra doble envío
  - Parallel: false
  - Hecho (2026-10-14): `services/followups.py` (`why_not` puro con cada condición de C-13), workers
    `followups.run` y `orders.discard_stale`. Candado: el de la conversación más el intento reclamado con
    compare-and-set (`StateRepo.save(retry=False)`). La espera cuenta desde el último mensaje o el último
    seguimiento, lo más reciente. El seguimiento cuenta como respuesta del agente (`record_ai_message`).
    Tests: `test_followup_rules.py` (29), `test_followups.py` (9); probado con el modelo real.

- [ ] TASK-445: Circuit breaker, niveles de inteligencia y memoria (cierre de la 003)
  - Input: C-02
  - Output: `circuit.py` (3 fallas en 60 s → abierto 2 min, `unsupported_fallback`, aviso `llm_caido`), `model_policy.py` (nivel → modelo desde settings, limitado por plan, ignorado si el tenant usa su propio LLM), ventana de 20 mensajes; tareas 301–308 de la 003 marcadas. **Milestone B, decisión 4:** los planes de pago permiten los tres niveles (migración 0027) y **cada nivel descuenta créditos según su costo real** (un mensaje `mejor` gasta más que uno `economico`; la tabla de costo por nivel va en settings y la cobra la 009); la prueba gratis solo usa `economico`. **Mientras `BILLING_ENFORCED` esté apagado** (Giannier prueba con sus negocios), ningún nivel se bloquea por plan
  - Depends on: TASK-421, TASK-431
  - Verification: tests del breaker con proveedor que falla; un nivel no permitido cae al permitido más alto; la ventana se respeta
  - Parallel: true

## Fase E · Cierre

- [ ] TASK-446: Aislamiento de todo lo nuevo
  - Input: todas las tablas y endpoints nuevos
  - Output: tests cruzados de tenant en `backend/tests/isolation` para cada endpoint (lista, detalle, edición) y cada tool (que un ID de otro tenant devuelva `no_disponible`)
  - Depends on: TASK-426, TASK-427, TASK-428, TASK-430, TASK-434, TASK-435, TASK-436, TASK-443
  - Verification: `pytest backend/tests/isolation` en verde, con más de 174 tests
  - Parallel: true

- [ ] TASK-447: Evals "flujos de venta"
  - Input: los 6 escenarios de Capacita, `plantillas.md`, criterios de la spec
  - Output: en `make evals` se suman 8 conversaciones guionadas por plantilla (24 en total), con aserciones sobre etapa final, herramientas llamadas, que no haya recursos repetidos, ≤ 3 salientes por turno, que nunca diga "confirmado" antes del operador y precios exactos
  - Depends on: TASK-433, TASK-444, TASK-445
  - Verification: ≥ 90 % por plantilla con el modelo `economico`; el resultado se adjunta al PR
  - Parallel: true

- [ ] TASK-448: E2E Playwright
  - Input: flujos de las Fases B y C
  - Output: specs e2e para configurar una tienda desde cero, completar un pedido sandbox con comprobante, confirmarlo desde Pedidos y editar y restaurar una etapa
  - Depends on: TASK-432, TASK-442
  - Verification: `npx playwright test` en verde y `next build` sin errores
  - Parallel: true

- [ ] TASK-449: 🧑 Aceptación y PR
  - Input: criterios de aceptación de la spec
  - Output:
    - `milestones/aceptacion.md` con Capacita (6 escenarios), Tienda y Zentek360 (cotización derivada + hosting de precio fijo);
    - cobertura por módulo;
    - `ESTADO.md` actualizado;
    - PR con evals adjuntos.
  - Depends on: TASK-446, TASK-447, TASK-448
  - Verification: Giannier aprueba los 3 negocios y hace el merge
  - Parallel: false

## Antes de producción (salidas del Milestone B)

Giannier las pidió como tareas: no entran en el Milestone B, pero **bloquean el deploy a producción**
(TASK-026 de la 001). Cada una lleva su test y su verificación.

- [x] TASK-450: País del negocio y prefijo telefónico (decisión 10)
  - **Hecho (2026-10-13):** `tenants.pais` (migración 0028; en el tenant y no en el perfil porque existe
    desde el registro), selector de país en el registro, `+51` solo para negocios de Perú (fuera de Perú se
    pide el número completo). Editarlo desde el panel queda en TASK-438.
  - Input: decisión 10 (prefijo según el país del negocio, Perú por defecto); hoy el tenant no tiene país
  - Output: `business_profiles.pais` (ISO 3166, por defecto `PE`, migración) en el wizard y en Información
    del negocio; `orders/customer_data.py` toma el prefijo del país (9 dígitos que empiezan en 9 → `+51`
    solo si el país es `PE`); la máscara de la lista de pedidos usa el mismo país
  - Verification: un negocio de otro país no convierte "987654321" a `+51`; Perú sigue igual
  - Parallel: true

- [x] TASK-451: El DNI o RUC registrado no vuelve al modelo (decisión 18)
  - Input: decisión 18; `conciliacion.md §8.22`
  - Output: cuando un DNI o RUC ya se guardó con `registrar_datos_cliente`, el historial que va al LLM lo
    reemplaza por `[DNI registrado]` / `[RUC registrado]` (al armar el historial, no al guardar el
    mensaje: el operador sigue viendo el original)
  - Verification: test con el pipeline: el valor no aparece en ninguna llamada al modelo después de
    registrarse; los evals no bajan
  - Parallel: true

- [x] TASK-452: Visión después de los gates y nunca con el perfil gratuito (hallazgo P-1)
  - Input: revisión de TASK-429 a 431, P-1 (Ley 29733, minimización)
  - Output: si la conversación está pausada, derivada o con opt-out, la imagen se guarda para el humano
    sin llamar al modelo (comprobante `sin_datos`, revisión humana); un perfil sin visión aprobada
    (`vision_aprobada` en settings; el gratuito nunca) tampoco la llama
  - Verification: tests de cada gate y del perfil gratuito: ninguna llamada a `read_image`
  - Parallel: true

- [x] TASK-453: Outbox transaccional del aviso al cliente (hallazgo E-4)
  - Input: revisión, E-4
  - Output: la fila del aviso (`queued`) se escribe en la misma transacción que confirmar, rechazar o
    anular; el worker la envía. Si el proceso muere después del commit, el aviso igual sale
  - Verification: test que corta entre el commit y el envío: la fila existe y el worker la manda
  - Parallel: true

- [x] TASK-454: Imagen no reconocida, guardada y visible (hallazgo D-4)
  - Input: revisión, D-4
  - Output: si el modelo dice que no es comprobante, la imagen se guarda igual y el pedido muestra el
    evento "imagen no reconocida" (sin crear comprobante)
  - Verification: test del pipeline y del historial del pedido
  - Parallel: true

- [x] TASK-455: Cuentas de prueba de Playwright
  - Input: pendiente del Milestone B
  - Output: las cuentas que crean los e2e se borran al terminar o quedan marcadas como de prueba (no
    cuentan en el panel de plataforma)
  - Verification: correr `npx playwright test` dos veces no deja cuentas sueltas sin marca
  - Parallel: true

- [ ] TASK-456: Fixtures reales de YCloud (se cierra dentro de TASK-459)
  - Input: pendiente del Milestone B; depende de un número real conectado (TASK-209 de la 002)
  - Output: capturar webhooks reales (texto, imagen, botón, lista, **referral**) y reemplazar los fixtures
    escritos a mano en `backend/tests/fixtures/`
  - Verification: la suite pasa con los fixtures reales
  - Parallel: true

### Backlog

- E-3 (revisión de TASK-429): la segunda imagen que llega mientras un comprobante espera se guarda en
  el pedido aunque no se lea. Hoy se ignora (no crea fila ni aviso, decisión E-1).

---

## Mapa de dependencias (resumen)

```
401 ─▶ 402 ─▶ 403 ─▶ 404 ─▶ 405 ─▶ 406 ─▶ 407 ─▶ 408 ─▶ 418 (YCloud) ─▶ 419 (media)
        └─▶ 409 ─▶ 410 ─▶ 411 ─▶ 412 ─▶ 413 ──────────────┐
             415 ─▶ 416 ──────────────────────────────────┤
             409+415 ─▶ 417 ─▶ 420 (+418) ────────────────┼─▶ 421 (pipeline) ─▶ 422 ─▶ 423 🧑
             403+404+409+411 ─▶ 414 ──────────────────────┘
403+406 ─▶ 424 ─▶ 425 ─▶ 426 ─▶ 427
                  425 ─▶ 428 (+417, 419)
                  425+421 ─▶ 429 ─▶ 430 ─▶ 431 · 432
426 · 427 · 428 · 431 · 432 ─▶ 433 🧑
433 🧑 ─▶ 459 🧑 (número real) ─▶ 460 ─▶ 461 ─▶ 462 ─▶ 463 ─▶ 464 ─▶ 466 · 469
          434 · 435 · 436 · 465 ─▶ 437 ─▶ 438 · 439 · 440 · 441 · 467 · 468 ─▶ 442 🧑
443 · 444 · 445 ─▶ 446 · 447 · 448 ─▶ 449 🧑
```
