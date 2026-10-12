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

- [ ] TASK-418: YCloud — interactivos, media, referral y BSUID
  - Input: documentación oficial de YCloud (enviar interactive/buttons/list, media, webhook `whatsapp.inbound_message.received` con `interactive`, `referral` y el identificador de usuario); `contracts/api.md §8`
  - Output: `send_buttons`, `send_list` y `send_media`; `Recipient` con teléfono o BSUID; parseo de `interactive`, `referral` y `from_user_id`; fixtures JSON reales en `tests/fixtures/ycloud/`
  - Depends on: TASK-408
  - Verification: tests de contrato contra los fixtures; firma HMAC sin cambios; un contacto solo con BSUID recibe una respuesta (envío por `recipient`). Si la doc contradice `contracts/api.md`, manda la doc y se anota en `conciliacion.md`
  - Parallel: true (con 409–418)

- [ ] TASK-419: YCloud — subida de media y caché
  - Input: C-17, doc de YCloud (endpoint de subida y vigencia del `media_id`)
  - Output: `upload_media()`, guardado de `provider_media_id` y su vencimiento, fallback con link firmado de 1 h, job `media.refresh_provider_ids`
  - Depends on: TASK-418, TASK-405
  - Verification: tests con el proveedor simulado: la primera vez sube, la segunda reutiliza, si vence vuelve a subir y si la subida falla usa el link
  - Parallel: false

- [ ] TASK-420: Tools `enviar_recurso`, `mostrar_opciones`, `elegir_camino` y extensión del catálogo
  - Input: `contracts/api.md §7`
  - Output: las 3 tools; `buscar_productos` y `ver_producto` devuelven `modo_venta`, `bundle_size`, `destacado`, recursos y promociones automáticas aplicables, y fijan `vio_productos` cuando hay resultados
  - Depends on: TASK-417, TASK-405, TASK-406, TASK-418
  - Verification:
    - no se reenvía un recurso (`omitido`);
    - se truncan los límites de WhatsApp (3 botones de 20, 10 filas de 24/72, cuerpo de 1024);
    - el enum de caminos es por tenant;
    - con presupuesto agotado devuelve `no_disponible`.
  - Parallel: false

- [ ] TASK-421: TurnPipeline
  - Input: `plan.md` (pipeline de 14 pasos), `agent/core.py`
  - Output: `agent/pipeline.py` con `TurnContext`, `stage_previa` y gate (pausado, opt-out, derivación vigente aunque la etapa sea VERIFICACION); `workers/channel.py` y el simulador llaman al pipeline; `core.py` recibe el prompt y las tools ya armados; las trazas incluyen etapa, embudo, tools habilitadas, salientes y nivel
  - Depends on: TASK-412, TASK-413, TASK-414, TASK-416, TASK-420, TASK-418
  - Verification:
    - tests de integración con LLM falso (`offline`) recorriendo INICIO → CALIFICAR → IDENTIFICAR;
    - `saludo_enviado` queda fijado;
    - se crea un evento por cambio de etapa;
    - la suite completa existente sigue en verde.
  - Parallel: false

- [ ] TASK-422: Simulador — salientes tipados y modo depuración
  - Input: `contracts/api.md §4`
  - Output: el endpoint acepta texto, `interactive_id` o imagen; devuelve `salientes[]` y `debug`; `GET /sandbox/messages?after=` para salientes asíncronos; `POST /sandbox/reset`; contacto sandbox con `external_user_id` sintético; nada sandbox sale al proveedor; el frontend dibuja botones, listas e imágenes y el panel de depuración (etapa, hechos, tools)
  - Depends on: TASK-421
  - Verification: Playwright: tocar un botón del simulador cambia la etapa en el panel de depuración
  - Parallel: false

- [ ] TASK-423: 🧑 Milestone A — Capacita en el simulador (sin pedidos)
  - Input: un tenant "Escuela Capacita" con la plantilla `certificaciones`, sus productos y paquetes (carga por CSV), recursos `saludo` y `flyer_paquetes`
  - Output: guion de prueba en `leo-sdd/features/004-motor-de-ventas/milestones/A.md` (escenarios 1–3 de Capacita: orgánica, calificación escrita, anuncio simulado)
  - Depends on: TASK-419, TASK-422
  - Verification: Giannier recorre los 3 escenarios y aprueba
  - Parallel: false

## Fase B · Venta

- [ ] TASK-424: Pricing y promociones (`orders/pricing.py`)
  - Input: C-10, `data-model.md §0015` (promotions)
  - Output: función pura `price(items, promociones_vigentes, promos_ofrecidas, now) -> Totales` que elige la de mayor ahorro sin acumular; incluye envío por zona y envío gratis desde un monto
  - Depends on: TASK-403, TASK-406
  - Verification: tests de tabla (porcentaje, monto fijo, 2×1, 3×2, precio por cantidad, monto mínimo, vigencia, cierre no ofrecido, cierre vencido, envío gratis). Cobertura de 100 %
  - Parallel: true (con Fase A desde TASK-410)

- [ ] TASK-425: OrderService
  - Input: `data-model.md §0019` (transiciones), C-06, C-07
  - Output: pedido activo, crear pedido nuevo después de confirmado, numeración por tenant, transiciones válidas (`InvalidTransition`), snapshot de `datos_cliente` al pasar a `por_confirmar`, `is_sandbox`
  - Depends on: TASK-407, TASK-424
  - Verification: test de la matriz completa de transiciones; numeración concurrente sin duplicados
  - Parallel: false

- [ ] TASK-426: Tool `gestionar_pedido`
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

- [ ] TASK-427: Tool `registrar_datos_cliente`
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

- [ ] TASK-428: Tool `enviar_metodos_pago`
  - Input: `payment_methods_business`, `media_asset_id` y el recurso `metodos_pago`
  - Output: texto con los métodos activos más la imagen o QR (consume presupuesto) y el hecho `metodos_pago_enviados = {order_id, en}`
  - Depends on: TASK-417, TASK-419, TASK-425
  - Verification: `omitido` la segunda vez para el mismo pedido; se permite un reenvío después de un rechazo; un pedido nuevo después de CERRADO vuelve a recibir los métodos; el embudo pasa a CUENTAS_ENVIADAS
  - Parallel: true

- [ ] TASK-429: ProofPipeline y visión
  - Input: C-08, `plan.md` (comprobantes)
  - Output: `orders/vision.py` (salida estructurada, timeout de 8 s) y `orders/proofs.py`: guarda la imagen en MinIO privado, crea el `payment_proof`, calcula las observaciones (monto distinto, código repetido sin contar sandbox, baja confianza, sin datos), pasa el pedido a `por_confirmar` y avisa al operador. Se integra en el paso 4 del pipeline usando `stage_previa`, incluso con la conversación derivada
  - Depends on: TASK-425, TASK-421
  - Verification:
    - tests con visión simulada para un comprobante válido, una foto que no es comprobante, un monto distinto, un código repetido y un timeout;
    - un comprobante que llega durante una derivación se registra y avisa, pero el bot no responde;
    - la etapa pasa a VERIFICACION;
    - el modelo recibe el resultado en el ESTADO.
  - Parallel: false

- [ ] TASK-430: API de pedidos y avisos al cliente
  - Input: `contracts/api.md §5`, C-09
  - Output: router con lista, resumen, detalle, imagen firmada, confirmar, rechazar (`rechazo_vigente` y motivo; contra entrega vuelve a COBRO) y status; aviso por WhatsApp después del commit con los mensajes de sistema (sandbox: queda como saliente sandbox); comprobación de ventana de 24 h; `audit_logs`; recálculo de etapa con su evento
  - Depends on: TASK-429
  - Verification:
    - tests de API por rol;
    - con la ventana cerrada responde `aviso_cliente: ventana_cerrada` y no llama al proveedor;
    - un pedido de otro tenant da 404;
    - una doble confirmación da 409.
  - Parallel: false

- [ ] TASK-431: Notificaciones al tenant
  - Input: `contracts/api.md §10`
  - Output: tipos nuevos en `tenant_notifications` y correos con el proveedor existente (`console` en desarrollo), con límite para `llm_caido`
  - Depends on: TASK-430
  - Verification: tests de que cada evento crea su notificación y su correo
  - Parallel: true

- [ ] TASK-432: Frontend — Pedidos
  - Input: `contracts/api.md §5`, referencia visual: página "Pedidos" de Ventor (tarjetas de resumen, filtros por estado, tabla y detalle)
  - Output: página `/pedidos` con resumen por periodo, filtros, búsqueda y tabla; detalle con ítems, datos del cliente, imagen del comprobante, datos leídos, observaciones resaltadas y botones Confirmar (con "siguiente paso") y Rechazar (con motivo); aviso si la ventana está cerrada
  - Depends on: TASK-430
  - Verification: Playwright: confirmar un pedido sandbox cambia su estado y muestra "aviso enviado"; `tsc` y `eslint` limpios
  - Parallel: true

- [ ] TASK-433: 🧑 Milestone B — Pedido completo
  - Input: tenant Capacita de la TASK-423 más un tenant "Tienda demo" con la plantilla `productos`, delivery y 2×1
  - Output: guion `milestones/B.md` (Capacita escenarios 4–5; tienda con 3 productos, promoción, delivery, comprobante y confirmación)
  - Depends on: TASK-426, TASK-427, TASK-428, TASK-431, TASK-432
  - Verification: Giannier lo recorre en el simulador y confirma desde Pedidos
  - Parallel: false

## Fase C · Configuración

- [ ] TASK-434: API de información del negocio, FAQ y promociones
  - Input: `contracts/api.md §1`
  - Output: routers; las FAQ se indexan como `knowledge_source` de tipo `faq`; borrar una FAQ borra su fuente
  - Depends on: TASK-403
  - Verification: tests de API y aislamiento; `consultar_conocimiento` encuentra una FAQ recién creada
  - Parallel: true (desde que termina la Fase A)

- [ ] TASK-435: API de comportamiento y proceso
  - Input: `contracts/api.md §1–§2`, C-05
  - Output: behavior (con límite por plan), templates, process (opciones validadas contra el schema de la plantilla), stages (editar y restaurar, `hay_texto_nuevo`), prompt-preview; caché por tenant invalidada al guardar
  - Depends on: TASK-404, TASK-414
  - Verification: un nivel fuera del plan da 403; restaurar devuelve el texto por defecto; tras guardar, el siguiente mensaje del simulador usa el cambio
  - Parallel: true

- [ ] TASK-436: API de multimedia
  - Input: `contracts/api.md §3`, límites de `data-model.md §0017`
  - Output: subida a MinIO privado con validación de tipo real (magic bytes) y tamaño; preview firmado; reemplazo de archivo invalida `provider_media_id`; el `PATCH` de métodos de pago acepta `media_asset_id` (QR)
  - Depends on: TASK-405, TASK-419
  - Verification: tests de límites (imagen de 6 MB rechazada; un .exe renombrado a .jpg rechazado)
  - Parallel: true

- [ ] TASK-437: Overview y lista de activación
  - Input: `contracts/api.md §1` (overview)
  - Output: cálculo de estado por sección y progreso; `probar_simulador` se marca con la primera conversación sandbox que llega a IDENTIFICAR
  - Depends on: TASK-434, TASK-435, TASK-436
  - Verification: tests del cálculo con un tenant vacío, uno parcial y uno completo
  - Parallel: false

- [ ] TASK-438: Frontend — Información del negocio
  - Input: los 8 bloques de RF-001; referencia visual de Ventor ("Información del negocio" con tarjetas por bloque)
  - Output: página con tarjetas por bloque (métodos de pago, que ya existe y ahora permite elegir su QR de la biblioteca, más locales y horarios, entregas, tipos de cliente, FAQ, promociones, devoluciones y contacto), cada una con estado y formulario sin texto libre obligatorio
  - Depends on: TASK-434
  - Verification: Playwright: completar entregas y verlo reflejado en una respuesta del simulador
  - Parallel: true

- [ ] TASK-439: Frontend — Comportamiento
  - Input: `contracts/api.md §1` (behavior)
  - Output: selectores visuales de ritmo, detalle (con ejemplo de respuesta para cada uno), seguimiento, nivel de inteligencia (bloqueado según plan, con "Mejorar plan"), pausa de derivación, mensajes de sistema editables y estado del asistente
  - Depends on: TASK-435
  - Verification: Playwright; `tsc` y `eslint` limpios
  - Parallel: true

- [ ] TASK-440: Frontend — Proceso de venta
  - Input: `contracts/api.md §2`
  - Output:
    - selector de plantilla con descripción;
    - formulario de opciones generado desde el schema (calificación con hasta 3 caminos, modalidad de pago, datos requeridos con checkboxes);
    - etapas en línea de tiempo con su columna del embudo;
    - editor de instrucciones por etapa con "Restaurar por defecto" y aviso de texto nuevo;
    - vista previa del prompt para el admin.
  - Depends on: TASK-435
  - Verification: Playwright: editar CALIFICAR, restaurar y verificar que vuelve el default
  - Parallel: true

- [ ] TASK-441: Frontend — Multimedia, checklist y depuración
  - Input: `contracts/api.md §3`, overview
  - Output: biblioteca con subida (arrastrar), clave sugerida desde el título, "cuándo usarlo", vínculo a producto o promoción; widget de activación en el inicio del panel; interruptor de depuración en el simulador
  - Depends on: TASK-436, TASK-437, TASK-422
  - Verification: Playwright: subir una imagen, pedirla en el simulador y verla llegar
  - Parallel: true

- [ ] TASK-442: 🧑 Milestone C — Configuración en 30 minutos
  - Input: una persona que no participó en el desarrollo, un negocio real o ficticio con 10 productos
  - Output: `milestones/C.md` con el tiempo medido y los puntos donde se trabó
  - Depends on: TASK-438, TASK-439, TASK-440, TASK-441
  - Verification: llega a vender en el simulador en ≤ 30 min. Si no, se crean tasks de ajuste antes de seguir
  - Parallel: false

## Fase D · Autonomía

- [ ] TASK-443: Derivación con pausa
  - Input: C-14, `contracts/api.md §6, §7, §9`
  - Output: tool `derivar_a_humano` (reemplaza el stub; termina el turno, envía el mensaje de derivación y con `asesoria` guarda `asesoria_derivada_en`), job `handoff.expire`, `GET /conversations/{id}/state`, `POST …/resume-bot`
  - Depends on: TASK-421, TASK-431
  - Verification: tests de que durante la pausa no se llama al LLM y se guardan los mensajes; al vencer se recalcula la etapa; el aviso al tenant incluye el resumen; después de vencer la pausa, ASESORIA no vuelve a derivar sola
  - Parallel: true

- [ ] TASK-444: Seguimiento y descarte de borradores
  - Input: C-13, `plantillas.md §6`
  - Output: jobs `followups.run` (ventana con margen de 30 min, horario de 08:00 a 21:00 del tenant, intentos, etapas IDENTIFICAR, PEDIDO, DATOS, COBRO y VERIFICACION con rechazo, opt-out existente, derivación, y promoción para seguimiento en el último intento registrada en `promos_ofrecidas`) y `orders.discard_stale` (con evento `pedido_descartado`)
  - Depends on: TASK-425, TASK-443
  - Verification: tests con reloj congelado para cada condición que impide enviar; reset cuando el cliente escribe; candado contra doble envío
  - Parallel: false

- [ ] TASK-445: Circuit breaker, niveles de inteligencia y memoria (cierre de la 003)
  - Input: C-02
  - Output: `circuit.py` (3 fallas en 60 s → abierto 2 min, `unsupported_fallback`, aviso `llm_caido`), `model_policy.py` (nivel → modelo desde settings, limitado por plan, ignorado si el tenant usa su propio LLM), ventana de 20 mensajes; tareas 301–308 de la 003 marcadas
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
434 · 435 · 436 ─▶ 437 ─▶ 438 · 439 · 440 · 441 ─▶ 442 🧑
443 · 444 · 445 ─▶ 446 · 447 · 448 ─▶ 449 🧑
```
