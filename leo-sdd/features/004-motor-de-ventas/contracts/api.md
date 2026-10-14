# Contracts — Motor de ventas (004 v2)

Prefijo, auth y forma de errores: **los del repo** (Claude Code los toma de los routers existentes). Aquí
se usa `/api/v1` y JWT de tenant como referencia. Roles:
- **admin:** owner o admin del tenant.
- **operador:** cualquier miembro activo del tenant.

Errores comunes:

| HTTP | code | Cuándo |
|---|---|---|
| 404 | `not_found` | El recurso no existe o es de otro tenant (nunca 403 entre tenants) |
| 409 | `invalid_transition` | Cambio de estado de pedido no permitido |
| 409 | `already_reviewed` | El comprobante ya fue confirmado o rechazado |
| 403 | `plan_limit` | Nivel de inteligencia o recurso fuera del plan |
| 422 | `validation_error` | Cuerpo inválido (detalle por campo) |

---

## 1. Asistente (configuración)

### GET /api/v1/assistant/overview — admin
Estado de cada sección y lista de activación.
```json
{
  "progreso": 70,
  "secciones": [
    { "clave": "informacion_basica", "estado": "completa" },
    { "clave": "informacion_negocio", "estado": "parcial", "detalle": { "metodos_pago": "completa", "locales": "vacia",
      "entregas": "vacia", "tipos_cliente": "vacia", "faqs": "completa", "promociones": "vacia",
      "devoluciones": "vacia", "contacto": "completa" } },
    { "clave": "comportamiento", "estado": "completa" },
    { "clave": "proceso_venta", "estado": "completa" },
    { "clave": "productos", "estado": "completa" },
    { "clave": "multimedia", "estado": "opcional" },
    { "clave": "conocimiento", "estado": "opcional" }
  ],
  "activacion": [
    { "paso": "productos", "hecho": true },
    { "paso": "metodos_pago", "hecho": true },
    { "paso": "proceso_venta", "hecho": true },
    { "paso": "probar_simulador", "hecho": false, "accion": "/asistente/simulador" },
    { "paso": "conectar_whatsapp", "hecho": false, "accion": "/whatsapp" }
  ],
  "siguiente_paso": "probar_simulador"
}
```
Regla del progreso: los 5 pasos de activación pesan 80 % y las secciones opcionales completas suman el 20 % restante.

### GET | PUT /api/v1/assistant/business-info/{section} — admin
`section` ∈ `locales | entregas | tipos_cliente | devoluciones | contacto`. Cuerpo = esquema de `data-model.md §0015`.
```http
PUT /api/v1/assistant/business-info/entregas
{ "hace_delivery": true, "zonas": [{ "nombre": "Chiclayo centro", "costo": "8.00", "tiempo": "mismo día" }],
  "envio_gratis_desde": "150.00", "recojo_en_tienda": true, "notas": null }
→ 200 { "section": "entregas", "data": { … }, "updated_at": "…" }
```

### FAQ — admin
- `GET /api/v1/assistant/faqs` → `{ "items": [{ "id", "pregunta", "respuesta", "orden", "is_active" }] }`
- `POST /api/v1/assistant/faqs` `{ "pregunta", "respuesta" }` → 201. Encola la indexación en conocimiento.
- `PATCH /api/v1/assistant/faqs/{id}` → 200. Si cambia el texto, vuelve a indexar.
- `DELETE /api/v1/assistant/faqs/{id}` → 204. Borra su `knowledge_source`.

### Métodos de pago (extiende el endpoint existente) — admin
El `PUT /api/v1/tenant/payment-methods/{id}` (y el `POST`) acepta `media_asset_id` (QR o imagen de
cuentas) o `null` —tiene que ser una imagen del mismo negocio, si no 422 `invalid_media`— y `titular`
(017, RF-010): el nombre que el comprobante debe mostrar como destinatario. Con titulares configurados,
un comprobante a otro nombre sale con `destinatario_distinto`.

### Promociones — admin
- `GET /api/v1/assistant/promotions?vigentes=true`
- `POST /api/v1/assistant/promotions`
```json
{ "nombre": "2x1 en polos", "descripcion": "Lleva 2 polos y paga 1", "uso": "automatica", "tipo": "nxm",
  "n": 2, "m": 1, "aplica_a": "categorias", "categorias": ["polos"], "inicia_en": null, "termina_en": "2026-10-31T23:59:00-05:00" }
```
- `PATCH /api/v1/assistant/promotions/{id}` y `DELETE /api/v1/assistant/promotions/{id}` (DELETE = `is_active=false` si algún pedido la usó).

### GET | PUT /api/v1/assistant/behavior — admin
```json
{
  "agent_name": "Capacitín", "tone": "cercano",
  "tratamiento": "tu", "emojis": "pocos", "saludo": "¡Hola! Bienvenido a Capacita",
  "politica_descuentos": "solo_promociones", "descuento_maximo_pct": null, "max_situaciones": 5,
  "ritmo": "equilibrado", "detalle": "moderado",
  "seguimiento": { "activo": true, "horas": 4, "max_intentos": 2 },
  "nivel_inteligencia": "economico", "niveles_permitidos": ["economico", "medio"],
  "derivacion_pausa_horas": 6, "activo": true,
  "mensajes_sistema": { "pago_confirmado": "…", "pago_rechazado": "…", "pedido_rechazado": "…", "pedido_anulado": "…", "derivacion": "…" }
}
```
`niveles_permitidos` es solo lectura (sale del plan; con `BILLING_ENFORCED` apagado son los tres). Si se
pide un nivel fuera del plan, responde 403 `plan_limit` (solo con el cobro encendido). `mensajes_sistema`
acepta `null` por clave para volver al texto por defecto; un marcador fuera de `{nombre_cliente}`,
`{numero}`, `{motivo}`, `{siguiente_paso}` da 422 `unknown_placeholder`. Ya no hay `reglas_adicionales`
(017, RF-011): las reglas propias son situaciones. Con `negociable` el agente **no** concede descuentos:
deriva (C-14 de la 017).

### Categorías — admin (decisión 13 del Milestone B)
- `GET /api/v1/assistant/categories` → `{ "items": [{ "id", "nombre", "orden", "activa", "productos" }] }`
- `PUT /api/v1/assistant/categories/{id}` `{ "nombre", "orden", "activa" }` → 200. Renombrar actualiza la
  etiqueta de sus productos; 409 `category_exists`.

---

## 2. Proceso de venta

### GET /api/v1/process/templates — admin
```json
{ "items": [{
  "key": "certificaciones", "version": 1, "nombre": "Cursos y certificaciones",
  "descripcion": "Califica al cliente, arma paquetes de N temas y cobra antes de emitir.",
  "etapas": [{ "key": "INICIO", "nombre": "Saludo", "embudo": "NUEVO", "herramientas": ["enviar_recurso", "…"] }, "…"],
  "opciones_schema": { "…JSON Schema de process_options…" },
  "opciones_default": { "…" }
}]}
```

### GET /api/v1/assistant/templates — admin (017)
`{ "items": [{ "key", "nombre", "descripcion", "opciones_schema", "opciones_default", "sugerida_por_rubro" }] }`.
`sugerida_por_rubro` marca la plantilla que sugiere el rubro del negocio (C-03); el negocio puede elegir otra.

### GET /api/v1/assistant/process — admin o revisor (017)
```json
{
  "template_key": "certificaciones", "template_version": 1,
  "options": { "…process_options…" },
  "etapas": [{ "key": "COBRO", "embudo": "NEGOCIACION",
               "reglas": [{ "rule_key": "COBRO.pago_es_previo_metodos_pago", "texto": "…", "apagada": false }] }],
  "novedades": null | { "version": 2, "vista": 1, "reglas": [ … ] }
}
```
Las etapas **no se editan** (017, C-01): se retiraron `PUT` y `DELETE /assistant/process/stages/{stage_key}`.

### PUT /api/v1/assistant/process — admin
`{ "template_key": "productos", "options": { … } }` → 200 con la vista de arriba. 422 `unknown_template` o
`invalid_options` (validadas contra el esquema de la plantilla). Invalida la caché del negocio.

### PUT /api/v1/assistant/rules/{kind}/{rule_key} — admin o revisor (017)
`kind` ∈ `proceso | rubro`. `{ "apagada": true }` → `{ "rule_key", "texto", "apagada" }`. 404 `unknown_rule`
si la regla no está en la versión publicada que corre el negocio. El apagado se recuerda por `rule_key`
entre versiones; invalida la caché.

### GET /api/v1/assistant/rubro — admin o revisor (017)
`{ "key", "nombre", "plantilla_sugerida", "version", "reglas": [Regla], "faqs": [{pregunta, respuesta}],
"objeciones": [{objecion, manejo}], "situaciones_sugeridas": [{id, titulo, cuando_aplica, que_hace, campos}],
"novedades" }`.

### POST /api/v1/assistant/novedades/{kind}/vistas — admin o revisor (017)
Marca vista la versión publicada → 204. Un negocio que nunca vio ninguna empieza en la actual.

### GET /api/v1/assistant/prompt-preview?stage=COBRO — admin
Devuelve el prompt armado con hechos de ejemplo y sin situaciones: `{ "stage", "prompt", "tokens_estimados",
"secciones": {…}, "herramientas": [ … ] }`. Sirve para ver qué cambia al apagar una regla.

### Situaciones (017, TASK-462) — admin
Reglas propias del negocio, como tarjetas. Nacen en `borrador`; solo las `activa` llegan al canal real.
- `GET /api/v1/assistant/situations?estado=&origen=` → `{ "items": [Situacion] }`
- `POST /api/v1/assistant/situations` → 201 `Situacion`. Cuerpo:
  `{ "titulo" (≤80), "cuando_aplica" (≤300), "que_hace" (≤1000), "media_asset_ids": [] (≤5),
  "derivar": false, "ejemplo_respuesta": null (≤600), "etapas": null | ["COBRO", …], "siempre": false }`.
  Errores 422: `que_hace_too_long`, `unknown_stage`, `unknown_media`. Encola el embedding de
  "cuándo aplica" (`embed_situations`); si la cola falla, lo hace el barrido de cada hora.
- `GET | PUT | DELETE /api/v1/assistant/situations/{id}` (404 `situation_not_found`). Cambiar "cuándo
  aplica" borra el embedding hasta que el worker lo recalcula.
- `POST /api/v1/assistant/situations/{id}/activar` y `…/desactivar` → `Situacion`.
- `POST /api/v1/assistant/situations/desde-sugerida/{id}` `{ "valores": {campo: valor} }` → 201, copia
  una situación sugerida del rubro del negocio (origen `sugerida_rubro`). 422 `unknown_suggestion` o
  `missing_fields`.
- `Situacion`: los campos del cuerpo más `id`, `estado`, `origen` (`manual | sugerida_rubro | migrada |
  aprendida`), `lista_para_coincidir` (ya tiene embedding), `created_at`, `updated_at`.
- `PUT /api/v1/tenant/agent-config` (wizard) acepta `tratamiento`, `emojis` y `saludo`; su
  `restrictions` se guarda como la situación `migrada` hasta que TASK-468 lo retire del wizard.

---

## 3. Multimedia — admin

- `GET /api/v1/assistant/media` → `{ "items": [{ "id", "clave", "tipo", "titulo", "cuando_usar", "caption", "product_id", "promotion_id", "preview_url", "size_bytes" }] }`
- `POST /api/v1/assistant/media` (multipart: `file`, `clave`, `titulo`, `cuando_usar`, `caption?`, `product_id?`, `promotion_id?`) → 201
  - 422 si el tipo o el tamaño excede los límites de WhatsApp, o si la `clave` está repetida.
- `PATCH /api/v1/assistant/media/{id}` (sin archivo) → 200. Si cambia el archivo, va por `POST …/{id}/file` y se invalida `provider_media_id`.
- `DELETE /api/v1/assistant/media/{id}` → 204

---

## 4. Simulador (extiende el existente)

- `POST /api/v1/sandbox/messages` acepta `{ "text": "…" }`, `{ "interactive_id": "camino:certificacion", "title": "Solo certificar" }` o multipart con `image`.
- Respuesta:
```json
{
  "salientes": [
    { "tipo": "imagen", "clave": "saludo", "caption": "…" },
    { "tipo": "botones", "body": "…", "botones": [{ "id": "camino:certificacion", "title": "Solo certificar" }] },
    { "tipo": "texto", "texto": "…" }
  ],
  "debug": {                                     // solo si ?debug=true
    "etapa_antes": "INICIO", "etapa_despues": "CALIFICAR", "embudo": "NUEVO",
    "hechos": { "…guardados + derivados (enmascarados)…" },
    "herramientas_habilitadas": ["mostrar_opciones", "elegir_camino", "consultar_conocimiento", "enviar_recurso", "derivar_a_humano"],
    "herramientas_llamadas": [{ "nombre": "mostrar_opciones", "status": "hecho" }],
    "tokens": { "prompt": 4210, "completion": 180 }, "modelo": "economico",
    "situaciones": [{ "id": "…", "titulo": "Si piden factura", "siempre": false, "borrador": true, "distancia": 0.21 }],
    "reglas_apagadas": { "proceso": ["COBRO.pedido_datos_completos"], "rubro": [] }
  }
}
```
- 017 (TASK-465): el cuerpo acepta `"situaciones_prueba": [id, …]` (≤10; en multipart, ids separados por
  comas): esas situaciones en borrador entran como activas **solo en ese turno**. El canal real nunca
  las usa.
- `GET /api/v1/sandbox/messages?after=<message_id>` → salientes asíncronos posteriores (aviso de confirmación o rechazo, derivación, seguimiento). El simulador consulta cada 3 s mientras está abierto.
- `POST /api/v1/sandbox/reset` → borra el estado, los mensajes y los pedidos sandbox → 204.
- Reglas sandbox (C-19): nada se envía al proveedor, la ventana siempre está abierta, no hay correos y no cuenta en el resumen ni en `codigo_repetido`.

## 4b. Panel de plataforma — catálogo (017, TASK-466)

Solo el operador de plataforma (404 para cualquier otro). Todo queda en `audit_logs`.
- `GET /api/v1/platform-admin/catalog` → `{ "items": [{ "kind", "key", "nombre", "publicada", "versiones": [{ "version", "estado", "evals_ok", "publicada_en" }] }] }`
- `POST /api/v1/platform-admin/catalog/{kind}/{key}/versions` → 201, borrador copiado de la versión más
  nueva. 409 `draft_exists` si ya hay un borrador.
- `GET | PUT /api/v1/platform-admin/catalog/{kind}/{key}/versions/{n}`. El `PUT` reemplaza el contenido del
  borrador: `{ "reglas": [{ "rule_key", "etapa", "texto" }], "faqs", "objeciones", "situaciones_sugeridas", "notas" }`.
  409 `version_frozen` si ya se publicó; 422 `invalid_content` (rule_key repetido, regla de proceso sin
  etapa o de rubro con etapa). Editar deja sin efecto los evals anteriores.
- `POST …/versions/{n}/evals` → 202, encola `run_catalog_evals`. El resultado queda en `evals`
  (`{ ok, hash, modelo, corrido_en, casos: [{ nombre, ok, detalle }] }`); `evals_vigentes` dice si pasaron
  sobre el contenido actual. Sin modelo configurado, falla con el motivo.
- `POST …/versions/{n}/publicar` → 200. 409 `evals_not_passed` si los evals no pasaron sobre este
  contenido. La versión anterior queda `retirada`; los negocios ven las reglas nuevas activas (salvo las
  apagadas) y el aviso de novedades; se reindexa el conocimiento del rubro.

---

## 5. Pedidos

### GET /api/v1/orders — operador
Query: `estado` (multi), `desde`, `hasta`, `q` (número, nombre o teléfono), `sandbox=false`, `page`, `page_size≤50`.
```json
{ "items": [{ "id": "…", "numero": 42, "estado": "por_confirmar", "cliente": { "nombre": "Ana P.", "telefono": "+51 9•• ••• 628" },
              "total": "500.00", "moneda": "PEN", "items_resumen": "Paquete 5 certificaciones",
              "tiene_comprobante": true, "observaciones": ["monto_distinto"], "creado": "…" }],
  "total": 18, "page": 1 }
```

### GET /api/v1/orders/summary?periodo=hoy|7d|30d — operador
```json
{ "conversiones": 12, "valor_total": "5400.00", "ticket_promedio": "450.00", "moneda": "PEN",
  "por_estado": { "borrador": 7, "por_confirmar": 3, "confirmado": 9, "en_curso": 2, "completado": 1, "descartado": 4 },
  "pendientes_revision": 3 }
```
`conversiones` = pedidos que llegaron a `confirmado` o más en el periodo.

### GET /api/v1/orders/{id} — operador
Pedido completo: ítems con selecciones, totales, promoción, entrega (`tipo_entrega`, zona, dirección), `datos_cliente` (completos para miembros del tenant; en la lista van enmascarados), comprobantes (sin URL), `conversation_id`, `ventana_abierta` (bool) e historial de estados.

### GET /api/v1/orders/{id}/proofs/{proof_id}/image — operador
`{ "url": "https://minio…", "expires_at": "…" }`. URL firmada de 10 minutos.

### POST /api/v1/orders/{id}/confirm — operador
`{ "siguiente_paso": "Te enviamos tus certificados en 48 h" }` (opcional) → 200
```json
{ "estado": "confirmado", "aviso_cliente": "enviado" }   // o "ventana_cerrada" (C-09) o "no_enviado"
```
Efectos en una transacción:
1. El último comprobante (si hay; en contra entrega no hay) pasa a `aprobado` y el pedido a `confirmado`.
2. Se registra en `audit_logs`.
3. Se recalcula la etapa (CERRADO / GANADO) y se crea el evento con `reason=operador:confirmar`.
4. Después del commit se encola el aviso por WhatsApp.

`aviso_cliente`: `enviado`; `ventana_cerrada` (ventana de 24 h cerrada: no se llama al proveedor); `no_enviado` (falló el proveedor o no hay número conectado; la decisión queda guardada y el negocio recibe el aviso `ventana_cerrada` con motivo `envio_fallido`). `no_enviado` se agregó en el Milestone B (decisión 15). Lo mismo vale para `reject` y para `status` al anular.

### POST /api/v1/orders/{id}/reject — operador
`{ "motivo": "El monto no coincide con el total" }` (obligatorio, de 5 a 300 caracteres) → 200
1. El comprobante pasa a `rechazado` y el pedido vuelve a `borrador` con `motivo_rechazo` y `rechazo_vigente = true`.
2. La etapa pasa a VERIFICACION y el hecho `pago_estado` a `rechazado` (evento `operador:rechazar`).
3. Se encola el aviso al cliente con el motivo (mensaje de sistema `pago_rechazado`).

En contra entrega no hay comprobante: el pedido vuelve a `borrador` con `motivo_rechazo` y `rechazo_vigente = false`, `pago_estado` queda `null` y la etapa vuelve a COBRO con el motivo en el ESTADO. El aviso usa el mensaje `pedido_rechazado`.

### POST /api/v1/orders/{id}/status — operador
`{ "estado": "en_curso" | "completado" | "descartado", "motivo"?: "…" }` → 200, o 409 `invalid_transition`. Recalcula la etapa (evento `operador:estado` si cambia).
```json
{ "estado": "descartado", "aviso_cliente": "enviado", "devolucion_pendiente": true }
```
**Descartar un pedido con comprobante vigente** (`por_verificar` o `aprobado`; Milestone B, hallazgo E-2): el cliente ya pagó, así que `motivo` (5–300) es obligatorio (sin él, 400 `validation_error`), el comprobante pasa a `anulado` y se avisa al cliente con el mensaje `pedido_anulado` (`aviso_cliente` como en confirmar). Si el pedido estaba `confirmado` o `en_curso`, queda `devolucion_pendiente = true`, visible en la lista y el detalle. Sin comprobante (contra entrega) o al mover a `en_curso`/`completado` no hay aviso: `aviso_cliente` es `null`.

---

## 6. Conversaciones

- `GET /api/v1/conversations/{id}/state` — operador → `{ "etapa", "embudo", "hechos" (enmascarados), "derivado", "eventos": [ … últimos 50 … ] }`
- `POST /api/v1/conversations/{id}/resume-bot` — operador → limpia `facts.derivado`, recalcula la etapa y crea el evento `reason=operador:reanudar` → 200.

---

## 7. Herramientas del agente (contrato con el modelo)

Todas devuelven el mismo sobre:
```json
{ "status": "hecho" | "omitido" | "no_disponible" | "error",
  "data": { … },
  "decir": "Instrucción corta para el modelo sobre qué decirle al cliente" }
```
`omitido` significa que ya se hizo antes (no reenviar ni mencionarlo). `no_disponible` significa que no aplica o que el presupuesto del turno se agotó.

| Herramienta | Parámetros | Efecto | Notas |
|---|---|---|---|
| `buscar_productos` (existe) | `consulta`, `categoria?`, `solo_paquetes?` | Con resultados: `vio_productos=true` | Agrega `modo_venta`, `bundle_size`, `destacado` y `promociones_aplicables` (automáticas) al resultado |
| `ver_producto` (existe) | `producto_id` | `vio_productos=true` | Agrega las claves de recursos vinculados |
| `consultar_conocimiento` (existe) | `pregunta` | — | Incluye FAQ |
| `enviar_recurso` | `clave` | Envía el archivo; agrega la clave a `recursos_enviados` | `omitido` si ya se envió; `no_disponible` si la clave no existe o no hay presupuesto |
| `mostrar_opciones` | `tipo` ∈ `caminos, categorias, productos, promociones`, `texto` (cuerpo, ≤1024), `filtro?` | Envía botones (≤3) o lista (≤10 filas). Las filas salen de la BD | Trunca título a 24 y descripción a 72 caracteres; botón a 20. Agrega a `opciones_mostradas` |
| `gestionar_pedido` | `accion` ∈ `agregar, quitar, cantidad, elegir, ver, vaciar, aplicar_promocion, confirmar, solicitar_cambio`; `producto_id?`, `cantidad?`, `seleccion?` (uno **o varios** temas separados por coma, punto y coma o salto de línea), `promocion_id?`, `nota?` | Crea o modifica el pedido activo; recalcula el total con `pricing`; cualquier cambio apaga `rechazo_vigente` | `confirmar` solo si `modalidad_pago=contra_entrega` y la etapa es COBRO. `agregar` con `modo_venta=cotizacion` → `no_disponible` + "deriva con resumen". `aplicar_promocion` (solo promos de cierre vigentes) registra `promos_ofrecidas[id] = now + vigencia_horas` y, si hay pedido, la aplica; sin pedido devuelve `hecho` con "se aplicará cuando arme su pedido". `solicitar_cambio` (Milestone B, decisión 8) solo con el pedido `por_confirmar`: no cambia nada, deja `nota` en `orders.notas` para el operador y el modelo le explica al cliente que el pedido está en revisión; en VERIFICACION por verificar es la única acción junto con `ver`. Devuelve siempre el pedido completo o `null` |
| `registrar_datos_cliente` (global) | cualquier campo de `datos_requeridos`, `nombres` siempre, y si el pedido requiere entrega: `tipo_entrega` (`delivery`/`recojo`), `direccion`, `referencia`, `zona_envio` | Guarda en `contacts.custom_data`; `tipo_entrega` y `zona_envio` van al pedido (recalcula envío); apaga `rechazo_vigente` | Acepta datos parciales; valida por campo; devuelve `faltan`, `invalidos` y las zonas válidas si la zona no existe |
| `enviar_metodos_pago` | — | Envía el texto de métodos y la imagen o QR si existe; `metodos_pago_enviados = {order_id, en}` | `omitido` si ya se enviaron para el pedido activo después del último rechazo (C-21) |
| `elegir_camino` | `camino` (enum con las claves configuradas) | `facts.camino` | El enum se arma por tenant; la tool no existe si no hay caminos |
| `derivar_a_humano` | `motivo` ∈ `pide_humano, queja, sin_respuesta, asesoria, cotizacion, otro`, `resumen` (≤500) | `facts.derivado`; con `asesoria` también `asesoria_derivada_en`; aviso al tenant; mensaje de derivación | Termina el turno: lo que el modelo escriba después se descarta |

Ejemplo de `gestionar_pedido` → `data`:
```json
{ "pedido": { "numero": 42, "estado": "borrador",
  "items": [{ "nombre": "Paquete 5 certificaciones", "cantidad": 1, "precio_unitario": "500.00",
              "selecciones": ["SIAF", "Gestión pública"], "faltan_selecciones": 3 }],
  "subtotal": "500.00", "descuento": "0.00", "envio": "0.00", "total": "500.00", "moneda": "PEN",
  "promocion": null, "completo": false, "faltan_selecciones": 3, "requiere_entrega": false } }
```
Las promociones de cierre disponibles no van en el resultado de la herramienta: van en el bloque ESTADO de cada turno (plantillas.md §4).

---

## 8. Proveedor de WhatsApp (extensión del adaptador)

```python
class Recipient(BaseModel):
    phone: str | None          # E.164
    bsuid: str | None          # ID de usuario de Meta; se usa si no hay teléfono

class WhatsAppProvider(Protocol):
    async def send_text(self, to: Recipient, text: str) -> SendResult: ...            # existe; cambia `to`
    async def send_media(self, to: Recipient, media: MediaRef, caption: str | None) -> SendResult: ...
    async def send_buttons(self, to: Recipient, body: str, buttons: list[Button],      # 1..3
                           header: str | None = None, footer: str | None = None) -> SendResult: ...
    async def send_list(self, to: Recipient, body: str, button_text: str,
                        sections: list[ListSection]) -> SendResult: ...                 # ≤10 filas en total
    async def upload_media(self, data: bytes, mime: str, filename: str) -> UploadedMedia: ...  # media_id + expires_at

class MediaRef(BaseModel):
    kind: Literal["image", "document", "video", "audio"]
    media_id: str | None; link: HttpUrl | None; filename: str | None
```

El parseo de entrada agrega:
- `InboundMessage.interactive = {"id", "title"}` para `button_reply` y `list_reply`.
- `InboundMessage.referral = {"ad_id", "source_url", "headline", "body", "media_url"}` para anuncios Click-to-WhatsApp.
- `InboundMessage.from_user_id` (BSUID) además del teléfono.

Los nombres exactos de los campos de YCloud se verifican en su documentación (TASK-418) y se cubren con fixtures reales del webhook.

---

## 9. Trabajos programados (ARQ)

| Job | Frecuencia | Qué hace |
|---|---|---|
| `followups.run` | cada 15 min | Seguimientos según C-13 (incluye VERIFICACION con rechazo). Candado `followup:{conversation_id}` con TTL de 10 min |
| `orders.discard_stale` | diario 03:00 (America/Lima) | Pasa a `descartado` los borradores con más de 7 días sin actividad y recalcula la etapa (evento `pedido_descartado`) |
| `handoff.expire` | cada 5 min | Limpia `facts.derivado` vencidos y crea el evento `pausa_vencida` |
| `media.refresh_provider_ids` | diario | Vuelve a subir los `provider_media_id` que vencen en menos de 48 h |

## 10. Notificaciones al tenant (`tenant_notifications` + correo)

| type | Cuándo | Correo |
|---|---|---|
| `nuevo_comprobante` | Comprobante recibido | sí |
| `pedido_contra_entrega` | Cliente confirmó un pedido contra entrega | sí |
| `derivacion` | `derivar_a_humano` | sí |
| `ventana_cerrada` | No se pudo avisar al cliente | no |
| `llm_caido` | Se abrió el circuit breaker | sí, máximo 1 por hora |
