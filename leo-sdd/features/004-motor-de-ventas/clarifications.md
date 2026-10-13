# Clarifications — Motor de ventas (004 v2)

Decisiones tomadas por Claude con el contexto de ESTADO.md (2026-10-07), el agente Capacita v21 y el
análisis de Ventor. **Pendientes de ratificar por Giannier.** Si alguna no te convence, se cambia
aquí antes de implementar; el plan y las tasks ya las asumen.

---

## C-01: Número de feature y orden del MVP
**Situación:** El CLAUDE.md de Leo dice "MVP restante: 004 → 006 → 007 → 009 → 010. Fase 2 (011–016) solo con ratificación por feature". La "fase 2" de nuestra conversación no es esa Fase 2.
**Decisión:** Esta feature es la **004 v2**. Reemplaza la spec 004 "Tools" (que queda archivada) y cierra lo pendiente de la 003. No toca 011–016.
**Impacto en implementación:** La spec vieja de 004 se mueve a `leo-sdd/features/_archivo/004-tools-v1/`. Las tareas de la 003 (301–308) se concilian en TASK-401.
**Registrado por:** Claude — por ratificar

## C-02: Qué entra de la 003 (circuit breaker, memoria, model_policies)
**Situación:** ESTADO.md deja abierto si esos tres entran al MVP.
**Decisión:** Entran en versión mínima:
- **Circuit breaker:** si el proveedor LLM falla 3 veces en 60 s para un tenant, se responde `unsupported_fallback`, se avisa al tenant y no se llama al modelo por 2 minutos.
- **Memoria:** ventana de los últimos 20 mensajes más los hechos de la conversación. No hay resúmenes ni memoria a largo plazo.
- **model_policies:** el "nivel de inteligencia" (económico, medio, mejor) se mapea a modelos en `settings` y el plan limita qué niveles se pueden elegir. **Revisado por Giannier (Milestone B, decisión 4):** no se bloquean niveles por plan: todos los planes de pago pueden usar los tres y cada nivel descuenta créditos según su costo real; la prueba gratis solo usa `economico`.
**Impacto en implementación:** TASK-445. Si el tenant usa su propio LLM (`tenant_llm_settings`), el nivel no aplica y la UI lo indica.
**Registrado por:** Claude — por ratificar

## C-03: Hechos guardados frente a hechos derivados
**Situación:** Si el estado de la conversación copia datos que también viven en pedidos o contactos, tarde o temprano se desalinean.
**Decisión:** Hay dos tipos de hechos.
- **Guardados** en `conversation_state.facts`, porque solo existen a nivel de conversación: `saludo_enviado`, `entrada_anuncio`, `camino`, `recursos_enviados`, `opciones_mostradas`, `metodos_pago_enviados`, `derivado`, `seguimiento`, `promos_ofrecidas`.
- **Derivados**, que se calculan en cada turno desde su tabla de origen: `pedido` (estado, completo, faltantes, total), `datos_completos` y `datos_faltantes` (contacto frente a lo que pide la plantilla), `pago_estado`.
**Impacto en implementación:** `FactsLoader` arma los dos tipos en una sola lectura. Nadie escribe hechos derivados.
**Registrado por:** Claude — por ratificar

## C-04: Prioridades entre etapas
**Situación:** Una conversación puede estar derivada y a la vez tener un pago en verificación.
**Decisión:** El orden del cálculo es fijo: CERRADO > VERIFICACION > DERIVADO > INICIO > PEDIDO > DATOS > COBRO > ASESORIA > ANUNCIO/CALIFICAR > IDENTIFICAR. Un pago pendiente pesa más que la derivación (igual que en Capacita v21).
**Impacto en implementación:** Una sola función pura `compute_stage(template, options, facts)` con tests de tabla (todas las combinaciones relevantes).
**Registrado por:** Claude — por ratificar

## C-05: Plantillas en código, textos en base de datos
**Situación:** Si el orden de etapas se pudiera editar desde el panel, un cliente podría romper su propio proceso.
**Decisión:**
- El **orden, las condiciones, las herramientas por etapa y la columna del embudo** viven en código, versionados por plantilla.
- Lo que el tenant edita son las **opciones** (activar calificación, modalidad de pago, datos requeridos, textos de los caminos) y las **instrucciones** de cada etapa.
- "Restaurar por defecto" borra el texto editado.
**Impacto en implementación:** Tabla `process_stage_overrides`. Si una versión nueva de la plantilla cambia el texto por defecto de una etapa que el tenant editó, se conserva el texto del tenant y el panel le avisa que hay un texto nuevo disponible.
**Registrado por:** Claude — por ratificar

## C-06: Un pedido activo por conversación
**Situación:** Un cliente puede volver a comprar después de un pedido cerrado, o abandonar un borrador.
**Decisión:**
- El pedido activo es el último pedido de la conversación.
- Si está confirmado y el cliente quiere otra cosa, `gestionar_pedido(agregar)` crea un pedido nuevo y la etapa vuelve a PEDIDO.
- Los borradores sin movimiento por 7 días pasan a `descartado` (lo hace el job diario `orders.discard_stale`).
**Impacto en implementación:** `orders.conversation_id` con índice; `OrderService.active_for(conversation)`.
**Registrado por:** Claude — por ratificar

## C-07: Pago contra entrega
**Situación:** Una tienda con delivery puede cobrar al entregar; en ese caso no hay comprobante.
**Decisión:** La modalidad es una sola opción, `modalidad_pago` (`previo` o `contra_entrega`), disponible solo en la plantilla productos. `OrderService` la copia al pedido al crearlo. Con `contra_entrega`, en COBRO el asistente resume el pedido y pide confirmación. Cuando el cliente confirma, `gestionar_pedido(confirmar)` deja el pedido `por_confirmar` y la etapa pasa a VERIFICACION ("esperando confirmación del negocio"). El operador lo confirma desde Pedidos igual que un pago.
**Impacto en implementación:** `pago_estado = por_verificar` también cuando el pedido está `por_confirmar` sin comprobante. Las instrucciones de VERIFICACION cubren ambos casos con el hecho `modalidad_pago`. Si el operador rechaza un pedido contra entrega, vuelve a COBRO con el motivo en el ESTADO. La opción "que el cliente elija" queda fuera de esta versión.
**Registrado por:** Claude — por ratificar

## C-08: Comprobantes sin depender del modelo
**Situación:** En Capacita el modelo leía el comprobante y llamaba `registrar pago`; si se olvidaba, el pago se perdía.
**Decisión:** Las imágenes que llegan en COBRO o en VERIFICACION (con pago rechazado) pasan por un pipeline del sistema **antes** de llamar al modelo:
1. Visión con salida estructurada: `es_comprobante`, monto, moneda, fecha, código, entidad, destinatario y confianza.
2. Si es comprobante, se crea `payment_proof` en estado `por_verificar` y el pedido pasa a `por_confirmar`.
3. Se marcan como observaciones (nunca como rechazo automático): monto distinto del total, código repetido en el tenant, confianza baja.
4. El modelo recibe el resultado en el bloque de ESTADO y solo responde "en verificación".

Si la visión dice que no es un comprobante, el modelo lo sabe y le pide la foto correcta.
**Impacto en implementación:** `orders/proofs.py` y `orders/vision.py`; nueva entrada en `agent_traces`. El sistema nunca confirma un pago.
**Registrado por:** Claude — por ratificar

## C-09: Confirmar un pago fuera de la ventana de 24 h
**Situación:** Meta solo deja escribir texto libre dentro de las 24 h desde el último mensaje del cliente. Las plantillas están fuera de alcance.
**Decisión:** Si al confirmar o rechazar la ventana está cerrada, el pedido se actualiza igual, no se envía el WhatsApp y el panel muestra: "La ventana de WhatsApp está cerrada; avísale al cliente por otro medio". El aviso queda en `tenant_notifications`.
**Impacto en implementación:** `ConversationWindow.is_open(conversation)` reutiliza `conversations.last_inbound_at`. Lo mismo aplica a los seguimientos.
**Registrado por:** Claude — por ratificar

## C-10: Promociones deterministas
**Situación:** El modelo no debe calcular descuentos ni inventar ofertas.
**Decisión:**
- Hay dos usos:
  - **Automática:** se aplica sola si el pedido cumple las condiciones.
  - **De cierre:** cupón, "lo pensaré", último seguimiento. Solo se aplica si el asistente la ofreció en esa conversación (`gestionar_pedido(aplicar_promocion)`) y dura las horas configuradas.
- La oferta de una promoción de cierre la registra el sistema en `facts.promos_ofrecidas` (con su vencimiento) cuando el modelo llama `gestionar_pedido(aplicar_promocion)`, aunque todavía no haya pedido: se aplica sola al pedido que se cree dentro de la vigencia. El último seguimiento también registra la promoción que menciona. El ESTADO muestra las promociones de cierre disponibles y si ya se ofrecieron.
- Las promociones no se acumulan: se aplica la de mayor ahorro.
- Tipos: porcentaje, monto fijo, N×M (2×1, 3×2) y precio especial por cantidad.
- Los paquetes ("5 certificaciones por S/ 500") no son promociones, son productos con `bundle_size`.
**Impacto en implementación:** `orders/pricing.py` es una función pura con tests de tabla. La tabla `promotions` lleva vigencia por fechas.
**Registrado por:** Claude — por ratificar

## C-11: Presupuesto de mensajes por turno
**Situación:** Cada mensaje saliente cuesta y varias burbujas seguidas se ven mal.
**Decisión:**
- Cada turno tiene un presupuesto de **3 mensajes salientes** y uno queda reservado para el texto. Las herramientas que envían algo (recurso, opciones, métodos de pago) consumen del presupuesto; si se acaba, devuelven `no_disponible` con la instrucción "ofrécelo en tu próximo mensaje".
- El texto sale así según el nivel de detalle:
  - **Breve:** 1 burbuja.
  - **Moderado:** 1 burbuja, o 2 si pasa de 700 caracteres.
  - **Extenso:** hasta 2.
- Si ya se enviaron 2 cosas, el texto sale en 1 sola burbuja.
**Impacto en implementación:** `TurnBudget` se inyecta en el contexto de las herramientas. El formateador es un port del nodo *Formato WhatsApp* de Capacita v21.
**Registrado por:** Claude — por ratificar

## C-12: Modos del asistente
**Situación:** Ventor tiene "Responde y gestiona" y otros modos.
**Decisión:** En esta versión solo hay dos modos: **Activo** (responde y gestiona) y **Pausado** (no responde y guarda los mensajes). Un negocio que no quiere pedidos usa "Servicios con cotización" con todos sus productos en modo cotización.
**Impacto en implementación:** `agent_configs.is_active` ya existe y se reutiliza. No se crea un campo nuevo.
**Registrado por:** Claude — por ratificar

## C-13: Seguimiento automático
**Situación:** Un seguimiento mal hecho molesta y además cuesta.
**Decisión:**
- Lo genera el modelo con las instrucciones de seguimiento de la etapa, sin herramientas y en 1 sola burbuja.
- Solo se envía si se cumplen todas estas condiciones: la ventana de 24 h está abierta con 30 minutos de margen, es horario permitido (08:00 a 21:00 en la zona horaria del tenant), la conversación no está derivada, el cliente no hizo opt-out, el asistente está activo y la etapa es IDENTIFICAR, PEDIDO, DATOS o COBRO.
- También aplica a VERIFICACION cuando el pago fue rechazado: es la única forma de que el cliente no se quede ahí.
- Valores por defecto: 4 horas de espera y 2 intentos como máximo.
- El opt-out ya existe en Leo (`contacts.opted_out_at` y `agent_configs.opt_out_keywords`) y se reutiliza tal cual.
**Impacto en implementación:** Cron de ARQ cada 15 minutos con un candado por conversación. Se resetea cuando el cliente vuelve a escribir.
**Registrado por:** Claude — por ratificar

## C-14: Derivación a humano sin bandeja
**Situación:** La bandeja (007) no existe todavía.
**Decisión:**
- `derivar_a_humano` pausa el bot en esa conversación por N horas (6 por defecto) y avisa al tenant en el panel y por correo, con el motivo y el resumen.
- La pausa vale aunque haya un pago pendiente: el embudo muestra EN_VERIFICACION (C-04), pero el bot no responde. Si durante la pausa llega un comprobante, el sistema lo registra y avisa al operador igual.
- Con `motivo = asesoria` se guarda además `asesoria_derivada_en`, que no se borra al vencer la pausa. Así el asistente no vuelve a derivar cada vez que el cliente escribe.
- Mientras tanto se guardan los mensajes del cliente sin respuesta. Al vencer la pausa, el bot vuelve con la etapa recalculada.
- El operador puede reactivar el bot antes desde el detalle del pedido o de la conversación (`POST /conversations/{id}/resume-bot`).
**Impacto en implementación:** Hecho `derivado = {motivo, resumen, hasta}`. La 007 reutiliza el mismo hecho.
**Registrado por:** Claude — por ratificar

## C-15: Respuestas a botones y listas
**Situación:** Lo que el cliente toca debe registrarse sin depender de que el modelo entienda.
**Decisión:** Los IDs llevan prefijo: `camino:{clave}`, `producto:{id}`, `categoria:{id}`, `promo:{id}`, `accion:{clave}`. Solo `camino:` fija un hecho de forma directa. Los demás se le pasan al modelo como una línea de contexto: "El cliente eligió: {título} (producto_id=…)".
**Impacto en implementación:** `process/interactive.py`. El adaptador de YCloud parsea `button_reply` y `list_reply`.
**Registrado por:** Claude — por ratificar

## C-16: Imágenes fuera del cobro
**Situación:** El cliente puede mandar fotos de un producto, capturas, etc.
**Decisión:** Fuera de COBRO y VERIFICACION, la imagen no se analiza: el modelo recibe "[El cliente envió una imagen]" y pregunta qué necesita. El análisis general de imágenes queda para la 006.
**Impacto en implementación:** Se evita el costo de visión en cada imagen.
**Registrado por:** Claude — por ratificar

## C-17: Cómo se envían los archivos de la biblioteca
**Situación:** WhatsApp descarga el archivo desde un link público o usa un `media_id` subido antes. MinIO en local no es público.
**Decisión:** El adaptador sube el archivo al proveedor la primera vez, guarda `provider_media_id` con su vencimiento y lo reutiliza. Si la subida falla, usa un link firmado de 1 hora.
**Impacto en implementación:** TASK-419 empieza confirmando en la documentación de YCloud el endpoint de subida y la duración del `media_id`. No se asume sin verificar.
**Registrado por:** Claude — por ratificar

## C-18: Base central `control` del holding
**Situación:** Las 19 empresas reportan a la base `control` (vista en n8n).
**Decisión:** Leo no escribe en `control` en esta feature. `conversation_stage_events` usa exactamente los mismos códigos de embudo (NUEVO, ATENDIDO, NEGOCIACION, CUENTAS_ENVIADAS, EN_VERIFICACION, GANADO, DERIVADO, PERDIDO), así la 010 solo tiene que replicarlos.
**Impacto en implementación:** Enum `FunnelCode` compartido. PERDIDO queda reservado (lo marca la 010).
**Registrado por:** Claude — por ratificar

## C-19: Simulador
**Situación:** Las pruebas desde el simulador no deben ensuciar Pedidos ni las métricas.
**Decisión:**
- El simulador usa el mismo motor con una conversación sandbox: los pedidos llevan `is_sandbox = true` y Pedidos los oculta por defecto.
- Nada sandbox sale al proveedor. Los mensajes asíncronos (aviso de confirmación o rechazo, derivación, seguimientos) se guardan como salientes y el simulador los consulta con `GET /sandbox/messages?after=`. Para sandbox la ventana de 24 h siempre está abierta.
- Lo sandbox no envía correos, no cuenta para `codigo_repetido` ni para el resumen de Pedidos. El contacto de prueba usa `external_user_id = "sandbox:{user_id}"`.
- Los comprobantes del simulador se pueden subir como imagen.
- El botón "Reiniciar conversación" borra el estado y los pedidos sandbox.
**Impacto en implementación:** Hay columna `is_sandbox` en `orders` y `conversation_state`. El modo depuración muestra la etapa, los hechos y las herramientas habilitadas.
**Registrado por:** Claude — por ratificar

## C-20: Datos personales
**Situación:** El DNI, la dirección y los comprobantes son datos sensibles del cliente final.
**Decisión:**
- Se guardan en `contacts.custom_data` y en MinIO privado.
- No se escriben en `agent_traces` ni en los logs: se enmascaran (`DNI ****5678`).
- Las imágenes se sirven con URL firmada de 10 minutos y solo a usuarios del tenant.
- Confirmar o rechazar un pago queda en `audit_logs`.
**Impacto en implementación:** Hay un redactor en el logger de trazas y tests que verifican que no se filtra.
**Registrado por:** Claude — por ratificar

## C-21: Salida de un pago rechazado
**Situación:** Con un comprobante rechazado, si la única salida fuera mandar otra foto, el cliente que quiere cambiar su pedido o sus datos se quedaría trabado en VERIFICACION.
**Decisión:** El rechazo marca `orders.rechazo_vigente = true`, y mientras siga así la etapa es VERIFICACION. Cualquier cambio de ítems o de datos del cliente lo apaga, y la etapa vuelve a PEDIDO, DATOS o COBRO según corresponda. En VERIFICACION con rechazo están habilitados `gestionar_pedido`, `registrar_datos_cliente` y un reenvío de métodos de pago.
**Impacto en implementación:** Columna `rechazo_vigente` y regla en `OrderService`. `metodos_pago_enviados` se guarda por pedido (`{order_id, en}`) y solo cuenta si es posterior al último rechazo.
**Registrado por:** Claude — por ratificar

## C-22: Una sola fuente para la entrega
**Situación:** Si la plantilla tuviera una opción "delivery" y además existiera la sección Entregas, podrían contradecirse.
**Decisión:** La entrega se configura solo en *Información del negocio → Entregas* (delivery con zonas, recojo en tienda). Aplica a los pedidos con algún producto que no sea digital. El cliente elige `tipo_entrega` con `registrar_datos_cliente`; si el negocio solo ofrece recojo, el valor se fija solo.
**Impacto en implementación:** `orders.tipo_entrega` y `datos_faltantes` dinámicos (plantillas.md §1). La plantilla no tiene opción `delivery`.
**Registrado por:** Claude — por ratificar

---

## Clarificaciones Pendientes
- [ ] ¿Aviso al operador por WhatsApp además del correo? Decisión provisional: no en esta versión (va con la 007).
- [ ] ¿Horario permitido de seguimiento configurable por tenant? Decisión provisional: fijo de 08:00 a 21:00 por ahora.
